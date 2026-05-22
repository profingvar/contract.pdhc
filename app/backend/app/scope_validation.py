"""Scope concept-existence validation (ticket #135).

When a Contract is created or updated and its `term[]` declares a
`request_scope` or `return_scope`, every concept GUID it references
must exist in plan.pdhc. Otherwise the contract is unprovisionable —
gateway.pdhc and request.pdhc would later fail on lookups.

Used by `POST /fhir/Contract` and `PUT /fhir/Contract/<guid>`.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Iterable

import requests
from flask import current_app

logger = logging.getLogger(__name__)


_GUID_FROM_URL_RE = re.compile(r'/api/v1/concepts/([0-9a-fA-F\-]{36})$')


def extract_scope_concept_guids(fhir_contract: dict) -> set[str]:
    """Walk Contract.term[].asset[].typeReference[] and return concept GUIDs."""
    guids: set[str] = set()
    for term in fhir_contract.get('term', []) or []:
        for asset in term.get('asset', []) or []:
            for ref in asset.get('typeReference', []) or []:
                reference = (ref or {}).get('reference', '')
                m = _GUID_FROM_URL_RE.search(reference)
                if m:
                    guids.add(m.group(1).lower())
    return guids


def verify_concepts_exist(guids: Iterable[str]) -> tuple[bool, dict]:
    """Hit plan.pdhc once per GUID. Returns (ok, info).

    info shape on success:
        {'verified': N}
    info shape on failure:
        {'missing': [...]}                   when STRICT and any 404
        {'reason': 'plan_unreachable',
         'detail': '<error>'}                when STRICT and HTTP error

    When STRICT_SCOPE_CONCEPTS is False, we always return (True, ...)
    so the caller can ship contracts without plan.pdhc running (used
    during local development).
    """
    guids = sorted(set(guids))
    if not guids:
        return True, {'verified': 0}

    # Read env directly rather than current_app.config — Config class
    # attributes freeze at import time, so monkeypatch.setenv from tests
    # would not flow through. This module is small enough that the live
    # env read is the simplest correct path.
    strict = os.environ.get('STRICT_SCOPE_CONCEPTS', 'true').lower() in (
        'true', '1', 'yes'
    )

    # When strict is off, skip validation entirely — for local dev where
    # plan.pdhc isn't running. The contract is accepted as-is.
    if not strict:
        return True, {'verified': 0, 'skipped': True}

    base = (
        os.environ.get('PLAN_BASE_URL')
        or current_app.config.get('PLAN_BASE_URL', '')
    ).rstrip('/')

    if not base:
        return False, {'reason': 'plan_not_configured'}

    missing: list[str] = []
    for guid in guids:
        try:
            resp = requests.get(
                f'{base}/api/v1/concepts/{guid}',
                headers={'Accept': 'application/json'},
                timeout=10,
            )
        except requests.RequestException as e:
            logger.warning('plan.pdhc concept lookup failed for %s: %s', guid, e)
            return False, {'reason': 'plan_unreachable', 'detail': str(e)}

        if resp.status_code == 404:
            missing.append(guid)
            continue
        if resp.status_code != 200:
            logger.warning(
                'plan.pdhc concept lookup returned %d for %s',
                resp.status_code, guid,
            )
            return False, {
                'reason': 'plan_unreachable',
                'detail': f'HTTP {resp.status_code}',
            }

    if missing:
        return False, {'missing': missing}
    return True, {'verified': len(guids)}
