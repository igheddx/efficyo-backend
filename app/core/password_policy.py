from __future__ import annotations

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 64


def password_policy_error(password: str) -> str | None:
    if len(password) < PASSWORD_MIN_LENGTH:
        return "Use at least 10 characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return "Password cannot be longer than 64 characters."
    return None
