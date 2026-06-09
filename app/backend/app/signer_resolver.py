"""Verify signer references resolve to real guids (ticket #230).

Separate from ``fhir.ensure_contract_shape`` because shape validation
is pure (no I/O), and resolution wants to hit IPS for patients and
the local users table for User/Practitioner. Operators may also need
to skip resolution in local dev when IPS isn't running.

Resolution policy
-----------------
- Patient/<guid>: best-effort GET against IPS. When ``IPS_BASE_URL``
  is empty, skip — match the existing #231 ``IPS_BASE_URL=''`` ->
  noop posture. When IPS is reachable, 404 means the guid doesn't
  exist and the contract is rejected.
- Practitioner/<guid> and User/<guid>: look up the local
  ``users.guid`` column. (contract.pdhc has its own users table for
  admin login; SSO professionals also flow through if they've
  authenticated at least once.) When the lookup misses, the guid
  is treated as unresolved and the contract is rejected.
- Organization/<guid>: no SSO client in contract.pdhc today;
  skipped with a structured note (configurable strict mode via
  ``STRICT_SIGNER_VALIDATION``). Until an SSO client lands here,
  organisation signers are accepted on shape only.

Strict mode
-----------
``STRICT_SIGNER_VALIDATION`` env (default true). When false, every
resolution failure (404 from IPS, missing user row, network error)
is downgraded to a warning and the contract is accepted. Used during
local dev and emergency hotfix paths.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import requests as http_requests
from flask import current_app
from sqlalchemy import select

from .db import make_session_factory  # noqa: F401  (typing aid)
from .models import User


log = logging.getLogger(__name__)


_REF_RE = re.compile(r"^([A-Z][a-zA-Z]+)/([^\s]+)$")


def _coerce_bool(val) -> bool:
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "no")
    return bool(val)


def _strict_mode() -> bool:
    """Strict mode is the default: unresolved signers reject the
    contract. Set ``STRICT_SIGNER_VALIDATION=false`` to allow.

    Lookup order matches how the rest of contract.pdhc handles
    config knobs: env first (so tests using ``monkeypatch.setenv``
    work even though the Config class snapshots env at import time),
    then ``app.config``, then default ``True``."""
    raw = os.environ.get("STRICT_SIGNER_VALIDATION")
    if raw is not None:
        return _coerce_bool(raw)
    if current_app:
        val = current_app.config.get("STRICT_SIGNER_VALIDATION", True)
        return _coerce_bool(val)
    return True


def _ips_base() -> str:
    """Env over app.config — see _strict_mode() docstring for why
    the Config class can't be trusted alone in test runs."""
    base = os.environ.get("IPS_BASE_URL")
    if base is None and current_app:
        base = current_app.config.get("IPS_BASE_URL") or ""
    return (base or "").rstrip("/")


def _ips_headers() -> dict:
    h = {"Accept": "application/json"}
    key = (
        os.environ.get("IPS_API_KEY")
        or (current_app and current_app.config.get("IPS_API_KEY"))
        or (current_app and current_app.config.get("INTERNAL_SERVICE_KEY"))
        or ""
    )
    if key:
        h["X-API-Key"] = key
    return h


def _resolve_patient(guid: str, *, strict: bool) -> Optional[str]:
    """Return a reason string when the patient does NOT resolve,
    or ``None`` when ok."""
    base = _ips_base()
    if not base:
        # Local dev / standalone install — skip silently. The #231
        # auto-emit path uses the same convention.
        return None
    url = f"{base}/api/v1/patients/{guid}"
    try:
        r = http_requests.get(
            url, headers=_ips_headers(), timeout=5,
        )
    except http_requests.RequestException as e:
        if strict:
            return f"IPS unreachable while resolving Patient/{guid}: {e}"
        log.warning(
            "patient resolve network error (lenient): %s", e,
        )
        return None
    if r.status_code == 200:
        return None
    if r.status_code == 404:
        if strict:
            return f"Patient/{guid} not found in IPS"
        log.warning(
            "patient resolve 404 for Patient/%s (lenient)", guid,
        )
        return None
    if strict:
        return (
            f"IPS returned {r.status_code} resolving Patient/{guid}"
        )
    log.warning(
        "patient resolve unexpected status %s (lenient)",
        r.status_code,
    )
    return None


def _resolve_user_or_practitioner(
    guid: str, *, strict: bool, session,
) -> Optional[str]:
    """Local users table check. Returns reason on failure, None on ok."""
    row = session.scalar(
        select(User).where(User.guid == guid)
    )
    if row is not None:
        return None
    if strict:
        return (
            f"User/Practitioner {guid} not found in local users table"
        )
    log.warning(
        "user resolve miss for guid %s (lenient)", guid,
    )
    return None


def verify_signer_references(
    resource: dict, *, session,
) -> list[str]:
    """Walk Contract.signer[].party[] and resolve each reference
    against the relevant catalogue.

    Returns a list of failure reasons (empty on success). The route
    layer turns a non-empty list into a 400 with a field-level error.
    """
    strict = _strict_mode()
    failures: list[str] = []
    signers = resource.get("signer") or []
    if not isinstance(signers, list):
        return failures  # shape was already rejected upstream
    for i, s in enumerate(signers):
        if not isinstance(s, dict):
            continue
        party = s.get("party")
        parties = party if isinstance(party, list) else [party]
        for j, p in enumerate(parties):
            ref = (p or {}).get("reference") if isinstance(p, dict) else None
            if not isinstance(ref, str):
                continue
            m = _REF_RE.match(ref)
            if m is None:
                continue
            res_type, guid = m.group(1), m.group(2)
            if res_type == "Patient":
                reason = _resolve_patient(guid, strict=strict)
            elif res_type in ("User", "Practitioner"):
                reason = _resolve_user_or_practitioner(
                    guid, strict=strict, session=session,
                )
            elif res_type == "Organization":
                # No SSO client yet — accept on shape alone until one
                # lands. Documented in module docstring.
                reason = None
            else:
                # Unknown resource type slipped past the shape check
                # (which only enforces 'ResourceType/id' pattern, not
                # an allow-list). Reject in strict mode.
                reason = (
                    f"signer[{i}].party[{j}].reference has unknown "
                    f"resource type {res_type!r}"
                    if strict else None
                )
            if reason:
                failures.append(f"signer[{i}].party[{j}]: {reason}")
    return failures
