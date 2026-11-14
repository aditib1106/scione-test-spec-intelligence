"""Session, CSRF and role boundaries against the real central account store."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from scione.auth import AuthManager, AuthMiddleware, current_principal, router
from scione.auth.manager import token_hash
from scione.auth.passwords import hash_password
from scione.auth.store import sessions, users
from scione.config import Settings

PASSWORD = "Synthetic-fixture-password-2026"


@pytest.fixture
def auth(tmp_path):
    manager = AuthManager(
        Settings(
            _env_file=None,
            scione_workspace_dir=tmp_path / "workspace",
            scione_database_url=None,
            scione_auth_database_url=None,
            scione_auth_enabled=True,
        )
    )
    manager.initialize()
    first = manager.store.create_lab("First lab", active=True)
    second = manager.store.create_lab("Second lab", active=True)
    encoded = hash_password(PASSWORD)
    for username, role, lab in (
        ("labone", "lab", first),
        ("labtwo", "lab", second),
        ("operator", "maintainer", None),
    ):
        manager.store.create_user(username, username, role, encoded, lab_id=lab.id if lab else None)
    app = FastAPI()
    app.state.auth_manager = manager
    app.include_router(router)
    app.add_middleware(AuthMiddleware, manager=manager)

    @app.get("/api/private")
    async def private():
        initial = current_principal()
        await asyncio.sleep(0.005)
        assert current_principal() == initial
        return initial.public()

    @app.post("/api/private")
    def write_private():
        return current_principal().public()

    @app.get("/api/diagnostics/private")
    def developer():
        return {"internal": True}

    @app.post("/api/reports/{document_id}/exclusions")
    def scope_change(document_id: str):
        return {"document_id": document_id, "business_comparison": True}

    @app.get("/api/reports/{document_id}")
    def legacy_report(document_id: str):
        return {"internal_artifact": True}

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def frontend():
        return {"login_page": True}

    with TestClient(app) as client:
        yield client, manager, first, second, app
    manager.close()


def sign_in(client, username="labone"):
    result = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert result.status_code == 200, result.text
    return result.json()


def test_public_session_open_business_api_requires_login(auth):
    client, *_ = auth
    assert client.get("/").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/session").json() == {
        "enabled": True,
        "authenticated": False,
        "user": None,
        "csrf_token": None,
    }
    assert client.get("/api/private").status_code == 401


def test_cookie_opaque_httponly_only_hash_stored(auth):
    client, manager, first, *_ = auth
    result = client.post("/api/auth/login", json={"username": "LABONE", "password": PASSWORD})
    assert result.status_code == 200
    assert result.json()["user"]["lab_id"] == first.id
    cookie = result.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    raw = client.cookies.get(manager.cookie_name)
    assert len(raw) == 43 and PASSWORD not in result.text
    with manager.store.engine.connect() as connection:
        stored = connection.execute(select(sessions.c.token_hash)).scalar_one()
    assert stored == token_hash(raw) and stored != raw
    assert client.get("/api/private").json()["lab_id"] == first.id
    assert current_principal() is None


def test_bad_credentials_do_not_enumerate_accounts(auth):
    client, *_ = auth

    def attempt(username):
        return client.post("/api/auth/login", json={"username": username, "password": "incorrect"})

    known, unknown = attempt("labone"), attempt("nobody")
    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json()


def test_csrf_bound_to_cookie_origin_and_logout_revokes_session(auth):
    client, manager, *_ = auth
    state = sign_in(client)
    csrf = state["csrf_token"]
    raw = client.cookies.get(manager.cookie_name)
    assert client.post("/api/private").status_code == 403
    assert client.post("/api/private", headers={"X-CSRF-Token": "a" * 64}).status_code == 403
    assert (
        client.post(
            "/api/private",
            headers={
                "X-CSRF-Token": csrf,
                "Origin": "https://attacker.example",
            },
        ).status_code
        == 403
    )
    assert client.post("/api/private", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/private").status_code == 401
    client.cookies.set(manager.cookie_name, raw)
    assert client.get("/api/private").status_code == 401


def test_lab_cannot_enumerate_switch_labs_or_access_developer_outputs(auth):
    client, _manager, _first, second, _app = auth
    state = sign_in(client)
    assert client.get("/api/auth/workspaces").status_code == 403
    assert client.get("/api/diagnostics/private").status_code == 403
    assert (
        client.post(
            "/api/auth/workspace",
            json={"lab_id": second.id},
            headers={
                "X-CSRF-Token": state["csrf_token"],
            },
        ).status_code
        == 403
    )
    assert client.get("/api/private").json()["lab_id"] == state["user"]["lab_id"]


def test_maintainer_switch_rotates_cookie_csrf_and_preserves_expiry(auth):
    client, manager, _first, second, _app = auth
    state = sign_in(client, "operator")
    old = client.cookies.get(manager.cookie_name)
    expires = manager.authenticate(old).expires_at
    assert len(client.get("/api/auth/workspaces").json()["labs"]) == 2
    result = client.post(
        "/api/auth/workspace",
        json={"lab_id": second.id},
        headers={
            "X-CSRF-Token": state["csrf_token"],
        },
    )
    assert result.status_code == 200
    assert result.json()["user"]["lab_id"] == second.id
    assert result.json()["csrf_token"] != state["csrf_token"]
    new = client.cookies.get(manager.cookie_name)
    assert new != old and manager.authenticate(old) is None
    assert manager.authenticate(new).expires_at == expires
    assert (
        client.post("/api/private", headers={"X-CSRF-Token": state["csrf_token"]}).status_code
        == 403
    )
    assert client.get("/api/diagnostics/private").status_code == 200


def test_absolute_and_idle_expiry_are_server_checked(auth):
    client, manager, *_ = auth
    sign_in(client)
    raw = client.cookies.get(manager.cookie_name)
    assert manager.authenticate(raw, now=manager.authenticate(raw).expires_at + 1) is None
    sign_in(client)
    raw = client.cookies.get(manager.cookie_name)
    with manager.store.engine.begin() as connection:
        connection.execute(
            update(sessions).where(sessions.c.token_hash == token_hash(raw)).values(last_seen_at=0)
        )
    assert client.get("/api/private").status_code == 401


def test_password_reset_and_disable_revoke_sessions(auth):
    client, manager, *_ = auth
    state = sign_in(client)
    user_id = state["user"]["id"]
    fresh = "Another-synthetic-password-2026"
    manager.store.reset_password(user_id, hash_password(fresh))
    assert client.get("/api/private").status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"username": "labone", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post("/api/auth/login", json={"username": "labone", "password": fresh}).status_code
        == 200
    )
    manager.store.disable_user(user_id)
    assert client.get("/api/private").status_code == 401
    assert (
        client.post("/api/auth/login", json={"username": "labone", "password": fresh}).status_code
        == 401
    )


def test_username_ip_throttles_are_database_backed(auth):
    client, manager, *_ = auth
    manager.settings.scione_auth_login_user_limit = 3
    for _ in range(3):
        assert (
            client.post(
                "/api/auth/login", json={"username": "labone", "password": "bad"}
            ).status_code
            == 401
        )
    blocked = client.post("/api/auth/login", json={"username": "labone", "password": PASSWORD})
    assert blocked.status_code == 429 and blocked.headers["retry-after"]
    manager.settings.scione_auth_login_ip_limit = 5
    assert (
        client.post("/api/auth/login", json={"username": "otherone", "password": "bad"}).status_code
        == 401
    )
    assert (
        client.post("/api/auth/login", json={"username": "othertwo", "password": "bad"}).status_code
        == 429
    )


def test_validation_never_echoes_credentials_or_accepts_oversized_body(auth):
    client, *_ = auth
    secret = "SHOULD-NOT-BE-ECHOED-" * 100
    response = client.post("/api/auth/login", json={"username": "labone", "password": secret})
    assert response.status_code == 400 and secret not in response.text
    assert client.post("/api/auth/login", content=b"x" * 16385).status_code == 413


def test_parallel_sessions_have_request_local_principals(auth):
    _client, _manager, first, second, app = auth

    def view(username, expected):
        with TestClient(app) as browser:
            sign_in(browser, username)
            for _ in range(3):
                assert browser.get("/api/private").json()["lab_id"] == expected

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(view, "labone", first.id), pool.submit(view, "labtwo", second.id)]
        for job in jobs:
            job.result()
    assert current_principal() is None


def test_corrupt_cookie_and_unknown_role_cannot_create_an_identity(auth):
    client, manager, *_ = auth
    client.cookies.set(manager.cookie_name, "not-a-session")
    assert client.get("/api/auth/session").json()["authenticated"] is False
    client.cookies.clear()
    state = sign_in(client)
    with manager.store.engine.begin() as connection:
        connection.execute(
            update(users).where(users.c.id == state["user"]["id"]).values(role="unexpected-role")
        )
    assert client.get("/api/private").status_code == 401


def test_lab_can_change_report_scope_without_reading_legacy_raw_report(auth):
    client, *_ = auth
    state = sign_in(client)
    document_id = "0123456789abcdef"
    assert client.get(f"/api/reports/{document_id}").status_code == 403
    changed = client.post(
        f"/api/reports/{document_id}/exclusions",
        headers={"X-CSRF-Token": state["csrf_token"]},
    )
    assert changed.status_code == 200 and changed.json()["business_comparison"]
    assert (
        client.post(
            "/api/reports/not-a-document/exclusions",
            headers={"X-CSRF-Token": state["csrf_token"]},
        ).status_code
        == 403
    )


def test_production_session_cookie_is_secure_host_only_and_rejects_foreign_origin(auth):
    _client, manager, _first, _second, app = auth
    manager.settings.scione_environment = "production"
    manager.settings.scione_public_url = "https://testserver"
    with TestClient(app, base_url="https://testserver") as browser:
        rejected = browser.post(
            "/api/auth/login",
            json={"username": "labone", "password": PASSWORD},
            headers={"Origin": "https://foreign.test"},
        )
        assert rejected.status_code == 403
        result = browser.post(
            "/api/auth/login",
            json={"username": "labone", "password": PASSWORD},
            headers={"Origin": "https://testserver"},
        )
        assert result.status_code == 200
        cookie = result.headers["set-cookie"]
        assert cookie.startswith("__Host-scione_session=")
        assert "Secure" in cookie and "HttpOnly" in cookie and "Domain=" not in cookie
        assert browser.get("/api/private").status_code == 200
        with TestClient(app, base_url="http://testserver") as insecure:
            for value in browser.cookies.jar:
                insecure.cookies.jar.set_cookie(value)
            assert insecure.get("/api/private").status_code == 401


def test_password_change_requires_current_secret_and_revokes_all_sessions(auth):
    client, manager, _first, _second, app = auth
    state = sign_in(client)
    new_password = "A-new-synthetic-private-password-2026"
    with TestClient(app) as other:
        sign_in(other)
        rejected = client.post(
            "/api/auth/password",
            json={"current_password": "wrong", "new_password": new_password},
            headers={"X-CSRF-Token": state["csrf_token"]},
        )
        assert rejected.status_code == 400
        changed = client.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": new_password},
            headers={"X-CSRF-Token": state["csrf_token"]},
        )
        assert changed.status_code == 200 and not changed.json()["authenticated"]
        assert client.get("/api/private").status_code == 401
        assert other.get("/api/private").status_code == 401
        assert verify_changed_password(manager, new_password)


def verify_changed_password(manager, password):
    from scione.auth.passwords import verify_password

    return verify_password(password, manager.store.user_by_username("labone")["password_hash"])


def test_database_faults_during_session_and_login_fail_closed_without_secrets(auth, monkeypatch):
    client, manager, *_ = auth
    sign_in(client)
    private_text = "synthetic-sensitive-SQL-bound-value"

    def unavailable(*_args, **_kwargs):
        raise SQLAlchemyError(private_text)

    with monkeypatch.context() as patch:
        patch.setattr(manager.store, "load_session", unavailable)
        response = client.get("/api/private")
        assert response.status_code == 503
        assert private_text not in response.text and PASSWORD not in response.text
    client.cookies.clear()
    monkeypatch.setattr(manager.store, "consume_login_attempt", unavailable)
    response = client.post("/api/auth/login", json={"username": "labone", "password": PASSWORD})
    assert response.status_code == 503
    assert private_text not in response.text and PASSWORD not in response.text
    assert not response.cookies
    assert client.get("/api/private").status_code == 401
