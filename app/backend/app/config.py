from __future__ import annotations

import os


def getenv_required(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


class Config:
    ENV = os.getenv("FLASK_ENV", "production")
    JWT_SECRET_KEY = getenv_required("JWT_SECRET_KEY")
    SECRET_KEY = os.getenv("SECRET_KEY", os.getenv("JWT_SECRET_KEY", ""))
    DATABASE_URL = getenv_required("DATABASE_URL")

    # Rate limit: 100 read requests/hour per IP
    READ_RATE_LIMIT = os.getenv("READ_RATE_LIMIT", "100 per hour")

    # SSO integration
    AUTH_DISABLED = os.getenv("AUTH_DISABLED", "false").lower() in ("true", "1", "yes")
    SSO_BASE_URL = os.getenv("SSO_BASE_URL", "https://sso.pdhc.se")
    SSO_CLIENT_ID = os.getenv("SSO_CLIENT_ID", "")
    SSO_CLIENT_SECRET = os.getenv("SSO_CLIENT_SECRET", "")
    SSO_CALLBACK_URL = os.getenv("SSO_CALLBACK_URL", "https://contract.pdhc.se/api/v1/auth/callback")
