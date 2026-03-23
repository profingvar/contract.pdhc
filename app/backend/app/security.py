from __future__ import annotations

import hashlib
import hmac
import os


def _pepper() -> str:
    return os.getenv("PASSWORD_PEPPER", "")


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    # Lightweight, local-first; can be upgraded to argon2/bcrypt later.
    # Pepper (optional) mitigates leaked DB hashes.
    salted = (password + _pepper()).encode("utf-8")
    return hashlib.sha256(salted).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password), password_hash)

