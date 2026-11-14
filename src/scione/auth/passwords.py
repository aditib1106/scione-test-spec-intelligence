"""Salted password hashing with Python's standard cryptographic primitives."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 1024


def validate_password(password: str) -> None:
    if (
        not isinstance(password, str)
        or not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH
    ):
        raise ValueError("Use a password between 15 and 1024 characters.")
    try:
        password.encode("utf-8")
    except UnicodeError:
        raise ValueError("Enter a valid Unicode password.") from None


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return "$".join(
        (
            "pbkdf2_sha256",
            str(ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        algorithm, rounds, salt_text, digest_text = encoded.split("$")
        iterations = int(rounds)
        if algorithm != "pbkdf2_sha256" or not ITERATIONS <= iterations <= 2_000_000:
            return False
        salt = base64.b64decode(salt_text, altchars=b"-_", validate=True)
        expected = base64.b64decode(digest_text, altchars=b"-_", validate=True)
        if len(salt) != 16 or len(expected) != 32:
            return False
    except (ValueError, TypeError):
        return False
    try:
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    except UnicodeError:
        return False
    return hmac.compare_digest(actual, expected)


def generated_password() -> str:
    return secrets.token_urlsafe(24)
