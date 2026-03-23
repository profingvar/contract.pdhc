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
    DATABASE_URL = getenv_required("DATABASE_URL")

    # Rate limit: 100 read requests/hour per IP
    READ_RATE_LIMIT = os.getenv("READ_RATE_LIMIT", "100 per hour")
