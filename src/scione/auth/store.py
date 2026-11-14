"""Central account/session storage, separate from every lab's customer data."""

from __future__ import annotations

import hashlib
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.pool import StaticPool

from scione.auth.models import AuthLab, AuthPrincipal
from scione.config import Settings


class AuthStoreError(RuntimeError):
    """A controlled error that never includes SQL, secrets or bound parameters."""


class AuthConflict(AuthStoreError):
    pass


metadata = MetaData()
versions = Table(
    "scione_auth_schema",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("version", Integer, nullable=False),
)
labs = Table(
    "scione_auth_labs",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("name", String(300), nullable=False),
    Column("name_key", String(64), nullable=False, unique=True),
    Column("active", Boolean, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("provisioned_at", BigInteger),
)
users = Table(
    "scione_auth_users",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("username", String(80), nullable=False, unique=True),
    Column("display_name", String(200), nullable=False),
    Column("role", String(20), nullable=False),
    Column("lab_id", String(32), ForeignKey(labs.c.id)),
    Column("password_hash", String(400), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("auth_version", Integer, nullable=False),
    Column("created_at", BigInteger, nullable=False),
)
sessions = Table(
    "scione_auth_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(32), ForeignKey(users.c.id), nullable=False, index=True),
    Column("lab_id", String(32), ForeignKey(labs.c.id)),
    Column("csrf_salt", String(64), nullable=False),
    Column("auth_version", Integer, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    Column("last_seen_at", BigInteger, nullable=False),
)
login_windows = Table(
    "scione_auth_login_windows",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("attempts", Integer, nullable=False),
    Column("expires_at", BigInteger, nullable=False, index=True),
)
audit_events = Table(
    "scione_auth_events",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("user_id", String(32)),
    Column("lab_id", String(32)),
    Column("event", String(60), nullable=False),
    Column("created_at", BigInteger, nullable=False, index=True),
)


def username_key(value: str) -> str:
    name = unicodedata.normalize("NFKC", value).strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.@+\-]{2,79}", name):
        raise ValueError("Use a username of 3–80 letters, numbers, dots, @, +, - or underscores.")
    return name


def _name_key(name: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
    return hashlib.sha256(normalized.encode()).hexdigest()


def _lab(row) -> AuthLab:
    return AuthLab(row["id"], row["name"], row["active"], row["created_at"], row["provisioned_at"])


@dataclass(frozen=True)
class AuthSession:
    principal: AuthPrincipal
    token_hash: str
    csrf_salt: str
    expires_at: int


class AuthStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        configured = settings.scione_auth_database_url or settings.scione_database_url
        self.database_url = configured.get_secret_value() if configured else None
        self.path = Path(settings.scione_workspace_dir).absolute() / "auth.sqlite3"
        self._engine: Engine | None = None
        self._lock = threading.RLock()

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    url = self.database_url or "sqlite:///" + str(self.path)
                    options = {"pool_pre_ping": True}
                    if make_url(url).get_backend_name() == "sqlite":
                        options["connect_args"] = {"check_same_thread": False, "timeout": 20}
                        if make_url(url).database in {None, "", ":memory:"}:
                            options["poolclass"] = StaticPool
                    self._engine = create_engine(url, **options)
        return self._engine

    def initialize(self) -> None:
        """Explicit additive migration; never alters existing business tables."""
        try:
            if self.database_url is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                metadata.create_all(self.engine)
                with self.engine.begin() as connection:
                    version = connection.execute(select(versions.c.version)).scalar_one_or_none()
                    if version is None:
                        connection.execute(insert(versions).values(id=1, version=1))
                    elif version != 1:
                        raise AuthStoreError("The account database needs a compatible migration.")
        except SQLAlchemyError:
            raise AuthStoreError("The account database could not be initialized.") from None

    def ensure_ready(self) -> None:
        try:
            with self.engine.connect() as connection:
                version = connection.execute(select(versions.c.version)).scalar_one_or_none()
                if version != 1:
                    raise AuthStoreError(
                        "Initialize the account database before starting this server."
                    )
        except SQLAlchemyError:
            raise AuthStoreError(
                "The account database is unavailable; sign-in remains disabled."
            ) from None

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def _event(self, connection, event: str, user_id=None, lab_id=None) -> None:
        connection.execute(
            insert(audit_events).values(
                id=uuid4().hex,
                event=event,
                user_id=user_id,
                lab_id=lab_id,
                created_at=int(time.time()),
            )
        )

    def get_lab(self, lab_id: str) -> AuthLab | None:
        if not re.fullmatch(r"[a-f0-9]{32}", lab_id):
            return None
        with self.engine.connect() as connection:
            row = connection.execute(select(labs).where(labs.c.id == lab_id)).mappings().first()
        return _lab(row) if row else None

    def lab_by_name(self, name: str) -> AuthLab | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(labs).where(labs.c.name_key == _name_key(name)))
                .mappings()
                .first()
            )
        return _lab(row) if row else None

    def list_labs(self, *, include_inactive: bool = False) -> list[AuthLab]:
        statement = select(labs).order_by(labs.c.created_at, labs.c.name)
        if not include_inactive:
            statement = statement.where(labs.c.active.is_(True))
        with self.engine.connect() as connection:
            return [_lab(row) for row in connection.execute(statement).mappings()]

    def create_lab(self, name: str, lab_id: str | None = None, *, active=False) -> AuthLab:
        name = " ".join(name.split())
        if not name or len(name) > 300:
            raise ValueError("Enter a lab name of up to 300 characters.")
        identifier = lab_id or uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Lab identifiers must be server-generated hexadecimal IDs.")
        now = int(time.time())
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(labs).values(
                        id=identifier,
                        name=name,
                        name_key=_name_key(name),
                        active=active,
                        created_at=now,
                        provisioned_at=now if active else None,
                    )
                )
                self._event(connection, "lab_created", lab_id=identifier)
        except IntegrityError:
            raise AuthConflict("That lab already exists.") from None
        return self.get_lab(identifier)

    def mark_lab_provisioned(self, lab_id: str) -> AuthLab:
        with self.engine.begin() as connection:
            result = connection.execute(
                update(labs)
                .where(labs.c.id == lab_id)
                .values(active=True, provisioned_at=int(time.time()))
            )
            if result.rowcount != 1:
                raise AuthConflict("The lab was not found.")
        return self.get_lab(lab_id)

    def user_by_username(self, username: str) -> dict | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(users).where(users.c.username == username))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    users.c.id,
                    users.c.username,
                    users.c.display_name,
                    users.c.role,
                    users.c.lab_id,
                    users.c.active,
                    users.c.created_at,
                ).order_by(users.c.username)
            ).mappings()
            return [dict(row) for row in rows]

    def create_user(self, username, display_name, role, password_hash, *, lab_id=None) -> dict:
        username = username_key(username)
        if role not in {"lab", "maintainer"}:
            raise ValueError("Choose the lab or maintainer role.")
        if role == "lab" and not lab_id:
            raise ValueError("A lab account must belong to one lab.")
        if lab_id and not (lab := self.get_lab(lab_id)):
            raise ValueError("Create and provision the lab before adding its accounts.")
        if lab_id and not lab.active:
            raise ValueError("Provision the lab before adding its accounts.")
        display_name = display_name.strip()
        if not display_name or len(display_name) > 200:
            raise ValueError("Enter a display name of up to 200 characters.")
        identifier = uuid4().hex
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(users).values(
                        id=identifier,
                        username=username,
                        display_name=display_name,
                        role=role,
                        lab_id=lab_id,
                        password_hash=password_hash,
                        active=True,
                        auth_version=1,
                        created_at=int(time.time()),
                    )
                )
                self._event(connection, "account_created", user_id=identifier, lab_id=lab_id)
        except IntegrityError:
            raise AuthConflict("That username already exists.") from None
        return self.user_by_username(username)

    def reset_password(self, user_id: str, password_hash: str) -> None:
        with self.engine.begin() as connection:
            result = connection.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(password_hash=password_hash, auth_version=users.c.auth_version + 1)
            )
            if result.rowcount != 1:
                raise AuthConflict("The account was not found.")
            connection.execute(delete(sessions).where(sessions.c.user_id == user_id))
            self._event(connection, "password_reset", user_id=user_id)

    def rename_user(self, old_username: str, new_username: str) -> tuple[dict, bool]:
        """Rename one identity without changing its password, membership or history.

        A repeat command succeeds only when a recorded operation proves that
        the destination is the account previously renamed by that exact pair.
        An unrelated account with the desired name is never treated as a match.
        """
        old, new = username_key(old_username), username_key(new_username)
        operation = "rename:" + hashlib.sha256(f"{old}\0{new}".encode()).hexdigest()[:53]
        try:
            with self._lock, self.engine.begin() as connection:
                original = (
                    connection.execute(
                        select(users).where(users.c.username == old).with_for_update()
                    )
                    .mappings()
                    .first()
                )
                target = (
                    connection.execute(
                        select(users).where(users.c.username == new).with_for_update()
                    )
                    .mappings()
                    .first()
                )
                if original is None:
                    prior = connection.execute(
                        select(audit_events.c.id).where(
                            audit_events.c.event == operation,
                            audit_events.c.user_id == target["id"] if target else False,
                        )
                    ).first()
                    if target is not None and prior is not None:
                        return dict(target), False
                    raise AuthConflict("The original account was not found.")
                if old == new:
                    return dict(original), False
                if target is not None:
                    raise AuthConflict("That username already belongs to another account.")
                connection.execute(
                    update(users)
                    .where(users.c.id == original["id"])
                    .values(
                        username=new,
                        auth_version=users.c.auth_version + 1,
                    )
                )
                connection.execute(delete(sessions).where(sessions.c.user_id == original["id"]))
                for event in ("account_renamed", operation):
                    self._event(
                        connection,
                        event,
                        user_id=original["id"],
                        lab_id=original["lab_id"],
                    )
                return {
                    **dict(original),
                    "username": new,
                    "auth_version": original["auth_version"] + 1,
                }, True
        except IntegrityError:
            raise AuthConflict("That username already belongs to another account.") from None

    def disable_user(self, user_id: str) -> None:
        with self.engine.begin() as connection:
            result = connection.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(active=False, auth_version=users.c.auth_version + 1)
            )
            if result.rowcount != 1:
                raise AuthConflict("The account was not found.")
            connection.execute(delete(sessions).where(sessions.c.user_id == user_id))
            self._event(connection, "account_disabled", user_id=user_id)

    def consume_login_attempt(self, username: str, ip: str, now: int) -> bool:
        window = self.settings.scione_auth_login_window_seconds
        bucket = now // window
        limits = (
            ("user", username, self.settings.scione_auth_login_user_limit),
            ("ip", ip, self.settings.scione_auth_login_ip_limit),
        )
        allowed = True
        with self._lock, self.engine.begin() as connection:
            for kind, value, limit in limits:
                key = hashlib.sha256(f"{kind}:{bucket}:{value}".encode()).hexdigest()
                try:
                    with connection.begin_nested():
                        connection.execute(
                            insert(login_windows).values(
                                key=key, attempts=1, expires_at=(bucket + 2) * window
                            )
                        )
                except IntegrityError:
                    connection.execute(
                        update(login_windows)
                        .where(login_windows.c.key == key)
                        .values(attempts=login_windows.c.attempts + 1)
                    )
                count = connection.execute(
                    select(login_windows.c.attempts).where(login_windows.c.key == key)
                ).scalar_one()
                allowed = allowed and count <= limit
            connection.execute(delete(login_windows).where(login_windows.c.expires_at < now))
        return allowed

    def create_session(self, token_hash, csrf_salt, user: dict, lab_id: str | None, now: int):
        expires_at = now + self.settings.scione_auth_session_hours * 3600
        with self.engine.begin() as connection:
            current = (
                connection.execute(select(users).where(users.c.id == user["id"]).with_for_update())
                .mappings()
                .first()
            )
            if (
                not current
                or not current["active"]
                or current["auth_version"] != user["auth_version"]
            ):
                raise AuthConflict("The account changed. Sign in again.")
            connection.execute(delete(sessions).where(sessions.c.expires_at <= now))
            connection.execute(
                insert(sessions).values(
                    token_hash=token_hash,
                    user_id=user["id"],
                    lab_id=lab_id,
                    csrf_salt=csrf_salt,
                    auth_version=user["auth_version"],
                    created_at=now,
                    expires_at=expires_at,
                    last_seen_at=now,
                )
            )
            self._event(connection, "signed_in", user_id=user["id"], lab_id=lab_id)

    def load_session(self, token_hash: str, now: int) -> AuthSession | None:
        with self.engine.begin() as connection:
            session = (
                connection.execute(select(sessions).where(sessions.c.token_hash == token_hash))
                .mappings()
                .first()
            )
            if not session:
                return None
            user = (
                connection.execute(select(users).where(users.c.id == session["user_id"]))
                .mappings()
                .first()
            )
            if (
                not user
                or not user["active"]
                or user["role"] not in {"lab", "maintainer"}
                or session["expires_at"] <= now
                or session["last_seen_at"] + self.settings.scione_auth_idle_minutes * 60 <= now
                or session["auth_version"] != user["auth_version"]
            ):
                connection.execute(delete(sessions).where(sessions.c.token_hash == token_hash))
                return None
            lab_id = user["lab_id"] if user["role"] == "lab" else session["lab_id"]
            lab = (
                connection.execute(select(labs).where(labs.c.id == lab_id)).mappings().first()
                if lab_id
                else None
            )
            if lab_id and (not lab or not lab["active"]):
                return None
            if user["role"] == "lab" and not lab_id:
                return None
            if now - session["last_seen_at"] >= 60:
                connection.execute(
                    update(sessions)
                    .where(sessions.c.token_hash == token_hash)
                    .values(last_seen_at=now)
                )
            principal = AuthPrincipal(
                user["id"],
                user["username"],
                user["display_name"],
                user["role"],
                lab_id,
                lab["name"] if lab else None,
            )
            return AuthSession(principal, token_hash, session["csrf_salt"], session["expires_at"])

    def revoke_session(self, token_hash: str) -> None:
        with self.engine.begin() as connection:
            session = (
                connection.execute(select(sessions).where(sessions.c.token_hash == token_hash))
                .mappings()
                .first()
            )
            connection.execute(delete(sessions).where(sessions.c.token_hash == token_hash))
            if session:
                self._event(
                    connection, "signed_out", user_id=session["user_id"], lab_id=session["lab_id"]
                )

    def rotate_workspace(
        self, old: AuthSession, new_hash: str, new_salt: str, lab_id: str, now: int
    ):
        if old.principal.role != "maintainer":
            raise AuthConflict("Only maintainers can switch workspaces.")
        with self.engine.begin() as connection:
            lab = connection.execute(select(labs).where(labs.c.id == lab_id)).mappings().first()
            current = (
                connection.execute(
                    select(sessions)
                    .where(sessions.c.token_hash == old.token_hash)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            user = (
                connection.execute(select(users).where(users.c.id == old.principal.user_id))
                .mappings()
                .first()
            )
            if not lab or not lab["active"]:
                raise AuthConflict("That lab workspace is not available.")
            if not current or not user or not user["active"] or user["role"] != "maintainer":
                raise AuthConflict("The session changed. Sign in again.")
            if current["auth_version"] != user["auth_version"] or current["expires_at"] <= now:
                raise AuthConflict("The session changed. Sign in again.")
            connection.execute(delete(sessions).where(sessions.c.token_hash == old.token_hash))
            connection.execute(
                insert(sessions).values(
                    **{
                        key: current[key]
                        for key in ("user_id", "auth_version", "created_at", "expires_at")
                    },
                    token_hash=new_hash,
                    csrf_salt=new_salt,
                    lab_id=lab_id,
                    last_seen_at=now,
                )
            )
            self._event(connection, "workspace_selected", user_id=user["id"], lab_id=lab_id)
