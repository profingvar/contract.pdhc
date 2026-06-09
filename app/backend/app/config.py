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

    # Internal service key — gateway.pdhc uses this to call scope endpoint without SSO
    INTERNAL_SERVICE_KEY = os.getenv("INTERNAL_SERVICE_KEY", "")

    # request.pdhc — for auto-provisioning PATs on contract save
    REQUEST_BASE_URL = os.getenv("REQUEST_BASE_URL", "http://localhost:9060")

    # ips.pdhc — for auto-emitting PatientConsent when a contract is
    # signed by a patient (#231). Empty -> consent emission is a noop
    # (local dev / standalone install). IPS_API_KEY is optional; when
    # absent the emitter falls back to INTERNAL_SERVICE_KEY.
    IPS_BASE_URL = os.getenv("IPS_BASE_URL", "")
    IPS_API_KEY = os.getenv("IPS_API_KEY", "")

    # Ticket #230 — Contract.signer[] reference resolution.
    # When True (default), a signer pointing at a non-existent Patient,
    # User, or Practitioner rejects the contract write with 400.
    # When False, every resolution failure is downgraded to a warning
    # and the write proceeds. Local dev sets this False so writes work
    # without IPS running.
    STRICT_SIGNER_VALIDATION = os.getenv(
        "STRICT_SIGNER_VALIDATION", "true"
    ).lower() in ("true", "1", "yes")

    # plan.pdhc — used by scope-concept existence validation (#135).
    PLAN_BASE_URL = os.getenv("PLAN_BASE_URL", "https://plan.pdhc.se")

    # When True, contract create/update verifies each concept GUID in
    # term[] actually exists in plan.pdhc. If plan.pdhc is unreachable,
    # the contract write is refused (503). Set to false during local
    # dev when plan.pdhc isn't running.
    STRICT_SCOPE_CONCEPTS = os.getenv(
        "STRICT_SCOPE_CONCEPTS", "true"
    ).lower() in ("true", "1", "yes")

    # SSO integration
    AUTH_DISABLED = os.getenv("AUTH_DISABLED", "false").lower() in ("true", "1", "yes")
    SSO_BASE_URL = os.getenv("SSO_BASE_URL", "https://sso.pdhc.se")
    SSO_CLIENT_ID = os.getenv("SSO_CLIENT_ID", "")
    SSO_CLIENT_SECRET = os.getenv("SSO_CLIENT_SECRET", "")
    SSO_CALLBACK_URL = os.getenv("SSO_CALLBACK_URL", "https://contract.pdhc.se/api/v1/auth/callback")
