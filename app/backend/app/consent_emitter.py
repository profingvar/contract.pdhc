"""Auto-emit PatientConsent on contract.pdhc lifecycle events (#231).

When a contract is signed (status executed / executable / offered /
renewed) and has a patient in ``signer[]``, post a PatientConsent
row to ips.pdhc. The consent's ``grantee_caregiver_guid`` is the
contract's provider org (extracted from ``party[role=provider]``);
its ``granted_via='contract'``; its ``contract_guid`` is the linkback;
and ``expires_at`` mirrors the contract's ``period.end`` when present.

When the contract is cancelled / terminated / revoked / deleted, the
revoke path POSTs to the consent's revoke endpoint with a reason
linking back to this contract.

Idempotency: before posting a grant, GET the patient's active
consents and skip when one already names this ``contract_guid`` and
this grantee. Re-signing the same contract (PUT to the same id) is
therefore a no-op for ANY patient/grantee pair that's already covered.

Best-effort: HTTP failures are logged at WARNING and swallowed so a
flaky IPS doesn't break the contract write. The contract author
should never lose work because consent emission is sluggish — the
follow-up reconciliation job (out of scope here) is the recovery
path.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

import requests as http_requests
from flask import current_app


log = logging.getLogger(__name__)


_LIFECYCLE_GRANT_STATUSES = (
    "executed", "executable", "offered", "renewed",
)
_LIFECYCLE_REVOKE_STATUSES = (
    "cancelled", "terminated", "revoked",
)


# ---------------------------------------------------------------------------
# FHIR parsing
# ---------------------------------------------------------------------------

def _extract_patient_signers(contract_resource: dict) -> list[str]:
    """Patient GUIDs from ``contract.signer[].party.reference``.

    FHIR Contract.signer[].party is a Reference. We accept either a
    single party object (older shapes) or the canonical list form.
    """
    out: list[str] = []
    seen: set[str] = set()
    for signer in contract_resource.get("signer") or []:
        if not isinstance(signer, dict):
            continue
        party = signer.get("party")
        parties = party if isinstance(party, list) else [party]
        for p in parties:
            ref = (p or {}).get("reference", "") if isinstance(p, dict) else ""
            if not isinstance(ref, str):
                continue
            if ref.startswith("Patient/"):
                guid = ref.split("/", 1)[1]
                if guid and guid not in seen:
                    seen.add(guid)
                    out.append(guid)
    return out


def _extract_provider_org_guids(contract_resource: dict) -> list[str]:
    """First-level provider organisation GUIDs from
    ``party[role=provider].reference``. Mirrors the helper inside
    ``main._extract_contract_parties`` (kept private here to avoid
    main.py import cycles)."""
    out: list[str] = []
    seen: set[str] = set()
    for party in contract_resource.get("party") or []:
        if not isinstance(party, dict):
            continue
        role_codes: list[str] = []
        for role in party.get("role") or []:
            for c in (role or {}).get("coding") or []:
                code = (c or {}).get("code")
                if code:
                    role_codes.append(code)
        if "provider" not in role_codes:
            continue
        for ref in party.get("reference") or []:
            ref_str = (ref or {}).get("reference", "") if isinstance(ref, dict) else ""
            if ref_str.startswith("Organization/"):
                guid = ref_str.split("/", 1)[1]
                if guid and guid not in seen:
                    seen.add(guid)
                    out.append(guid)
    return out


def _extract_expires_at(contract_resource: dict) -> Optional[str]:
    """Contract.period.end as an ISO-8601 string, or None when absent."""
    period = contract_resource.get("period") or {}
    end = period.get("end")
    if isinstance(end, str) and end.strip():
        return end
    return None


# ---------------------------------------------------------------------------
# IPS calls
# ---------------------------------------------------------------------------

def _ips_base() -> str:
    return (current_app.config.get("IPS_BASE_URL") or "").rstrip("/")


def _ips_headers() -> dict:
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    key = current_app.config.get("IPS_API_KEY") or \
        current_app.config.get("INTERNAL_SERVICE_KEY") or ""
    if key:
        h["X-API-Key"] = key
    return h


def _list_active_consents(patient_guid: str) -> list[dict]:
    base = _ips_base()
    if not base:
        return []
    try:
        r = http_requests.get(
            f"{base}/api/v1/patients/{patient_guid}/consents",
            params={"active": "true"},
            headers=_ips_headers(),
            timeout=5,
        )
    except http_requests.RequestException as e:
        log.warning("ips list consents failed for %s: %s",
                    patient_guid[:12], e)
        return []
    if r.status_code != 200:
        log.warning("ips list consents %s -> %s", patient_guid[:12],
                    r.status_code)
        return []
    payload = r.json() or {}
    items = payload.get("items") or []
    return [it for it in items if isinstance(it, dict)]


def _post_grant(
    patient_guid: str,
    *,
    grantee_caregiver_guid: str,
    contract_guid: str,
    expires_at: Optional[str],
    note: Optional[str],
) -> bool:
    base = _ips_base()
    if not base:
        return False
    body = {
        "grantee_caregiver_guid": grantee_caregiver_guid,
        "granted_via": "contract",
        "contract_guid": contract_guid,
    }
    if expires_at:
        body["expires_at"] = expires_at
    if note:
        body["granted_note"] = note
    try:
        r = http_requests.post(
            f"{base}/api/v1/patients/{patient_guid}/consents",
            headers=_ips_headers(),
            json=body, timeout=5,
        )
    except http_requests.RequestException as e:
        log.warning("ips grant consent failed for %s: %s",
                    patient_guid[:12], e)
        return False
    if r.status_code == 409:
        # IPS server-side duplicate check fired; nothing wrong with that
        # — treat as success (idempotency at the other end).
        return True
    if r.status_code not in (200, 201):
        log.warning(
            "ips grant consent %s -> %s body=%s",
            patient_guid[:12], r.status_code, r.text[:200],
        )
        return False
    return True


def _post_revoke(
    patient_guid: str, consent_guid: str, *, reason: str,
) -> bool:
    base = _ips_base()
    if not base:
        return False
    try:
        r = http_requests.post(
            f"{base}/api/v1/patients/{patient_guid}/"
            f"consents/{consent_guid}/revoke",
            headers=_ips_headers(),
            json={"reason": reason}, timeout=5,
        )
    except http_requests.RequestException as e:
        log.warning("ips revoke consent failed for %s/%s: %s",
                    patient_guid[:12], consent_guid[:12], e)
        return False
    if r.status_code in (200, 409):  # 409 = already revoked/expired
        return True
    log.warning("ips revoke consent %s/%s -> %s",
                patient_guid[:12], consent_guid[:12], r.status_code)
    return False


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------

def emit_patient_consents(contract_resource: dict) -> dict:
    """Idempotently emit PatientConsent rows for every patient signer.

    Returns a small summary suitable for logging:
      {posted: N, skipped: N, attempted: N, status: "ok"|"skipped"|"noop"}

    Behaviour:
      - status not in ``_LIFECYCLE_GRANT_STATUSES`` -> noop.
      - No patient signers -> noop.
      - No provider orgs -> noop (nothing to grant TO).
      - IPS_BASE_URL missing -> noop (local dev).
      - For each (patient, grantee) cross-product: check active consents;
        if a row with the same ``contract_guid`` already exists for that
        grantee, skip; otherwise POST.
    """
    summary = {
        "posted": 0, "skipped": 0, "attempted": 0,
        "status": "noop",
    }
    status = (contract_resource.get("status") or "").lower()
    if status not in _LIFECYCLE_GRANT_STATUSES:
        return summary
    if not _ips_base():
        return summary

    patients = _extract_patient_signers(contract_resource)
    grantees = _extract_provider_org_guids(contract_resource)
    if not patients or not grantees:
        return summary

    contract_guid = contract_resource.get("id", "")
    expires_at = _extract_expires_at(contract_resource)
    note = f"contract={contract_guid}"

    summary["status"] = "ok"
    for patient_guid in patients:
        active = _list_active_consents(patient_guid)
        for grantee in grantees:
            summary["attempted"] += 1
            already = any(
                (c.get("contract_guid") == contract_guid)
                and (c.get("grantee_caregiver_guid") == grantee)
                for c in active
            )
            if already:
                summary["skipped"] += 1
                continue
            ok = _post_grant(
                patient_guid,
                grantee_caregiver_guid=grantee,
                contract_guid=contract_guid,
                expires_at=expires_at,
                note=note,
            )
            if ok:
                summary["posted"] += 1
    return summary


def revoke_patient_consents(
    contract_resource: dict, *, reason: str,
) -> dict:
    """Revoke every consent on ips.pdhc whose ``contract_guid`` matches.

    Used by:
      - status flip into ``_LIFECYCLE_REVOKE_STATUSES`` (update path)
      - DELETE /fhir/Contract/<guid>
    """
    summary = {
        "revoked": 0, "attempted": 0, "status": "noop",
    }
    if not _ips_base():
        return summary
    patients = _extract_patient_signers(contract_resource)
    if not patients:
        return summary
    contract_guid = contract_resource.get("id", "")

    summary["status"] = "ok"
    for patient_guid in patients:
        active = _list_active_consents(patient_guid)
        for c in active:
            if c.get("contract_guid") != contract_guid:
                continue
            consent_guid = c.get("guid")
            if not consent_guid:
                continue
            summary["attempted"] += 1
            if _post_revoke(
                patient_guid, consent_guid, reason=reason,
            ):
                summary["revoked"] += 1
    return summary
