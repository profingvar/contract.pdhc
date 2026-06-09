"""Reconciler for missed contract→IPS PatientConsent emissions (#243).

Recovery path for the "best-effort, swallow failures" contract on
:func:`consent_emitter.emit_patient_consents` (see consent_emitter.py
docstring, last paragraph). When IPS is briefly unreachable during a
contract write, the consent emission drops silently. This module
periodically walks every contract in a lifecycle status and re-calls
the emitter / revoker.

The emitter and the revoker are already idempotent at the IPS side:

  - ``emit_patient_consents`` checks each patient's active consents for
    a matching ``contract_guid`` + ``grantee_caregiver_guid`` before
    posting; matches are skipped.
  - ``revoke_patient_consents`` skips entries whose ``contract_guid``
    doesn't match the contract being reconciled.

So the reconciler is structurally simple: walk the contracts, dispatch
to the right verb based on status, sum the per-contract summaries.

The CLI command :func:`reconcile_consents_cli` registers as
``flask reconcile-consents``; it prints a one-line summary so an
operator can grep for "grants_re_emitted=" or pipe the output to a
metrics shipper.

Acceptance (per #243):
  - Planted miss is recovered: a contract whose original write didn't
    reach IPS shows up in ``grants_re_emitted`` on the next run.
  - No-op on a clean DB: ``grants_re_emitted == 0 and
    revokes_re_called == 0``.
  - Second run within the same window does ~zero work.
"""
from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .consent_emitter import (
    emit_patient_consents,
    revoke_patient_consents,
    _LIFECYCLE_GRANT_STATUSES,
    _LIFECYCLE_REVOKE_STATUSES,
)
from .models import ContractRecord


log = logging.getLogger(__name__)


_ALL_LIFECYCLE_STATUSES = (
    _LIFECYCLE_GRANT_STATUSES + _LIFECYCLE_REVOKE_STATUSES
)


def reconcile(session: Session) -> dict:
    """Walk ContractRecord rows in any lifecycle status; re-emit /
    re-revoke the consents IPS should be holding.

    Returns a structured summary::

        {
          "checked":            int,  # contracts walked
          "grants_re_emitted":  int,  # PatientConsent rows that didn't
                                      # exist on IPS and got created now
          "revokes_re_called":  int,  # consents on IPS that should have
                                      # been revoked and got revoked now
          "grant_attempts":     int,  # raw call count (helps detect
                                      # noisy upstream)
          "revoke_attempts":    int,
          "errors":             int,  # contracts that raised — the loop
                                      # keeps going so one poison row
                                      # doesn't stall the whole sweep
        }
    """
    summary = {
        "checked": 0,
        "grants_re_emitted": 0,
        "revokes_re_called": 0,
        "grant_attempts": 0,
        "revoke_attempts": 0,
        "errors": 0,
    }

    rows: Iterable[ContractRecord] = session.scalars(
        select(ContractRecord).order_by(ContractRecord.updated_at.desc())
    ).all()

    for row in rows:
        resource = row.fhir_contract or {}
        status = (resource.get("status") or "").lower()
        if status not in _ALL_LIFECYCLE_STATUSES:
            # Drafts / proposals / unknown statuses don't have consent
            # implications; skip them so the sweep stays cheap on a
            # large catalog.
            continue
        summary["checked"] += 1
        try:
            if status in _LIFECYCLE_REVOKE_STATUSES:
                sub = revoke_patient_consents(
                    resource, reason=f"reconcile:contract_status:{status}",
                )
                summary["revoke_attempts"] += int(sub.get("attempted") or 0)
                summary["revokes_re_called"] += int(sub.get("revoked") or 0)
            else:
                sub = emit_patient_consents(resource)
                summary["grant_attempts"] += int(sub.get("attempted") or 0)
                summary["grants_re_emitted"] += int(sub.get("posted") or 0)
        except Exception:  # noqa: BLE001
            # Keep the sweep going — log and count.
            log.warning(
                "reconcile-consents: contract=%s raised",
                resource.get("id", "?"), exc_info=True,
            )
            summary["errors"] += 1

    log.info("reconcile-consents summary: %s", summary)
    return summary
