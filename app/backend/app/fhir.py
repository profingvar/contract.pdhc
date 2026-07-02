from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any


# FHIR R5 Contract status codes — http://hl7.org/fhir/contract-status
ALLOWED_CONTRACT_STATUSES = frozenset({
    "amended", "appended", "cancelled", "disputed", "entered-in-error",
    "executable", "executed", "negotiable", "offered", "policy",
    "rejected", "renewed", "revoked", "resolved", "terminated",
})


API_VERSION = "1.0.0"


# Rollup #350 §2.2 — CapabilityStatement.date derives from this file's
# mtime so it's stable across gunicorn workers AND across requests
# (default gunicorn forks workers post-import; `datetime.now()` at
# module level would run once per worker and give inconsistent dates
# on consecutive requests). Only advances on a real image rebuild.
# Same pattern as request.pdhc #367 + memory
# `infra_gunicorn_worker_fork_freezes_datetime`.
_CAPABILITYSTATEMENT_DATE = datetime.fromtimestamp(
    os.path.getmtime(__file__), tz=timezone.utc
).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_capability_statement() -> dict[str, Any]:
    """Return a full FHIR R5 CapabilityStatement for this Contract server."""
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:9021")

    return {
        "resourceType": "CapabilityStatement",
        "id": "pdhc-contract-manager",
        "url": f"{base_url}/fhir/metadata",
        "version": API_VERSION,
        "name": "PDHCContractManagerCapabilityStatement",
        "title": "PDHC Contract Manager \u2014 FHIR Capability Statement",
        "status": "active",
        "experimental": False,
        "date": _CAPABILITYSTATEMENT_DATE,
        "publisher": "PDHC",
        "contact": [{"name": "PDHC Development"}],
        "description": (
            "FHIR R5 capability statement for the PDHC Contract Manager. "
            "This server manages healthcare contracts as FHIR R5 Contract "
            "resources with JWT-authenticated admin CRUD and rate-limited "
            "public read access."
        ),
        "kind": "instance",
        "software": {
            "name": "PDHC Contract Manager",
            "version": API_VERSION,
        },
        "implementation": {
            "description": "PDHC Contract Manager instance",
            "url": os.getenv("PUBLIC_WEB_URL", "http://localhost:9022"),
        },
        "fhirVersion": "5.0.0",
        "format": ["application/fhir+json"],
        "rest": [
            {
                "mode": "server",
                "documentation": (
                    "RESTful FHIR R5 server with Contract CRUD support. "
                    "Read endpoints are public and rate-limited to 100 requests/hour per IP. "
                    "Write endpoints (create, update, delete) require a JWT Bearer token "
                    "with admin role. User management is available under /admin/."
                ),
                "security": {
                    "cors": True,
                    "service": [
                        {
                            "coding": [
                                {
                                    # Rollup #350 §2.3-analogue — R5's
                                    # restful-security-service system
                                    # URL (was the R4-era
                                    # http://terminology.hl7.org/CodeSystem/…
                                    # variant which the R5 validator
                                    # doesn't resolve). Same fix
                                    # request.pdhc landed in #377.
                                    "system": "http://hl7.org/fhir/restful-security-service",
                                    "code": "OAuth",
                                    "display": "OAuth",
                                }
                            ],
                            "text": "JWT Bearer token via /auth/login",
                        }
                    ],
                    "description": (
                        "Authentication via POST /auth/login returns a JWT Bearer token "
                        "(8-hour expiry). Include as Authorization: Bearer <token> on "
                        "admin endpoints. Read endpoints are public. "
                        "Roles: admin (full CRUD + user management), reader (read-only)."
                    ),
                },
                "resource": [
                    {
                        "type": "Contract",
                        "profile": "http://hl7.org/fhir/StructureDefinition/Contract",
                        "documentation": (
                            "FHIR R5 Contract resources representing healthcare agreements. "
                            "Public read access with rate limiting. "
                            "Admin-only create, update, and delete."
                        ),
                        "interaction": [
                            {
                                "code": "read",
                                "documentation": "GET /fhir/Contract/{guid} — public, rate-limited",
                            },
                            {
                                "code": "search-type",
                                "documentation": "GET /fhir/Contract — returns Bundle searchset, public, rate-limited",
                            },
                            {
                                "code": "create",
                                "documentation": "POST /fhir/Contract — admin JWT required",
                            },
                            {
                                "code": "update",
                                "documentation": "PUT /fhir/Contract/{guid} — admin JWT required",
                            },
                            {
                                "code": "delete",
                                "documentation": "DELETE /fhir/Contract/{guid} — admin JWT required, returns 204",
                            },
                        ],
                        "versioning": "no-version",
                        "readHistory": False,
                        "updateCreate": False,
                        # `searchParam` omitted — FHIR forbids empty
                        # arrays. Add entries here when search
                        # parameters are actually supported.
                        # Rollup #350 §2.1 — advertise the /scope
                        # endpoint that main.py:446 has served since
                        # before this rollup. Bidirectional truthfulness
                        # (Rule 20). `definition` is a canonical URL;
                        # the concrete METHOD/path lives on the first
                        # line of `documentation` (matches the shape
                        # used by request.pdhc's #377 CapabilityStatement).
                        "operation": [
                            {
                                "name": "scope",
                                "definition": (
                                    f"{base_url}/OperationDefinition/Contract-scope"
                                ),
                                "documentation": (
                                    "GET /fhir/Contract/{guid}/scope — "
                                    "Returns a lightweight scope + status "
                                    "descriptor for the contract "
                                    "(request_scope, return_scope, "
                                    "scope_defined, status). Public, "
                                    "rate-limited under READ_RATE_LIMIT."
                                ),
                            },
                        ],
                    }
                ],
            }
        ],
    }


def ensure_contract_shape(resource: dict[str, Any]) -> dict[str, Any]:
    if resource.get("resourceType") != "Contract":
        raise ValueError("resourceType must be 'Contract'")

    rid = resource.get("id")
    if not rid:
        resource["id"] = str(uuid.uuid4())

    # Status is required and must be a valid FHIR R5 contract status
    status = resource.get("status")
    if not status:
        raise ValueError("status is required")
    if status not in ALLOWED_CONTRACT_STATUSES:
        raise ValueError(
            f"status must be one of: {', '.join(sorted(ALLOWED_CONTRACT_STATUSES))}"
        )

    # Validate subject references if present
    if "subject" in resource:
        subjects = resource["subject"]
        if not isinstance(subjects, list):
            raise ValueError("subject must be an array of references")
        for i, ref in enumerate(subjects):
            if not isinstance(ref, dict) or "reference" not in ref:
                raise ValueError(f"subject[{i}] must be an object with a 'reference' field")
            if not re.match(r"^[A-Z][a-zA-Z]+/[^\s]+$", ref["reference"]):
                raise ValueError(f"subject[{i}].reference must match 'ResourceType/id' format")

    if "period" in resource:
        period = resource["period"]
        if not isinstance(period, dict):
            raise ValueError("period must be an object")
        for k in ("start", "end"):
            if k in period and period[k] is not None:
                try:
                    datetime.fromisoformat(period[k].replace("Z", "+00:00"))
                except Exception as e:
                    raise ValueError(f"period.{k} must be ISO-8601 datetime") from e

    # Optional string fields
    for field in ("title", "name", "note"):
        if field in resource and not isinstance(resource[field], str):
            raise ValueError(f"{field} must be a string")

    # Issued datetime (optional)
    if "issued" in resource and resource["issued"] is not None:
        try:
            datetime.fromisoformat(resource["issued"].replace("Z", "+00:00"))
        except Exception as e:
            raise ValueError("issued must be ISO-8601 datetime") from e

    # Party array (payer/provider organisations)
    _REF_PATTERN = re.compile(r"^[A-Z][a-zA-Z]+/[^\s]+$")
    if "party" in resource:
        parties = resource["party"]
        if not isinstance(parties, list):
            raise ValueError("party must be an array")
        for i, p in enumerate(parties):
            if not isinstance(p, dict):
                raise ValueError(f"party[{i}] must be an object")
            # role is required: array of CodeableConcept
            if "role" not in p or not isinstance(p["role"], list) or not p["role"]:
                raise ValueError(f"party[{i}].role must be a non-empty array of CodeableConcept")
            for j, role in enumerate(p["role"]):
                if not isinstance(role, dict) or "coding" not in role:
                    raise ValueError(f"party[{i}].role[{j}] must have a 'coding' array")
            # reference is required: array of Reference
            if "reference" not in p or not isinstance(p["reference"], list) or not p["reference"]:
                raise ValueError(f"party[{i}].reference must be a non-empty array of Reference")
            for j, ref in enumerate(p["reference"]):
                if not isinstance(ref, dict) or "reference" not in ref:
                    raise ValueError(f"party[{i}].reference[{j}] must have a 'reference' field")
                if not _REF_PATTERN.match(ref["reference"]):
                    raise ValueError(f"party[{i}].reference[{j}].reference must match 'ResourceType/id' format")

    # Topic array (PlanDefinition references for work areas)
    if "topic" in resource:
        topics = resource["topic"]
        if not isinstance(topics, list):
            raise ValueError("topic must be an array of references")
        for i, ref in enumerate(topics):
            if not isinstance(ref, dict) or "reference" not in ref:
                raise ValueError(f"topic[{i}] must be an object with a 'reference' field")
            if not _REF_PATTERN.match(ref["reference"]):
                raise ValueError(f"topic[{i}].reference must match 'ResourceType/id' format")

    # Signer array — ticket #230. Each entry needs a type, a
    # well-shaped party reference, and a non-empty signature value.
    if "signer" in resource:
        signers = resource["signer"]
        if not isinstance(signers, list):
            raise ValueError("signer must be an array")
        for i, s in enumerate(signers):
            _validate_signer(s, i, _REF_PATTERN)

    # Term array — concept scope (optional, backward compatible)
    if "term" in resource:
        _validate_terms(resource["term"])

    return resource


def _validate_signer(s: Any, i: int, ref_pattern) -> None:
    """Structural validation for Contract.signer[i] (ticket #230).

    Validates:
      - signer is an object
      - signer.type is a CodeableConcept-shaped object
      - signer.party is a Reference (or list of Reference per the
        legacy shape contract.pdhc has historically accepted) whose
        ``reference`` matches the ``ResourceType/id`` form
      - signer.signature is a non-empty array of Signature objects;
        each must carry a non-empty ``data`` string

    Reference resolution (verifying the guid corresponds to a real
    user / org / patient) happens at the route layer, not here —
    catalogue lookup needs IPS/SSO calls and a config knob to skip
    in local dev.
    """
    if not isinstance(s, dict):
        raise ValueError(f"signer[{i}] must be an object")
    if "type" not in s:
        raise ValueError(
            f"signer[{i}].type is required (CodeableConcept)"
        )
    if not isinstance(s["type"], dict):
        raise ValueError(
            f"signer[{i}].type must be a CodeableConcept object"
        )
    if "party" not in s:
        raise ValueError(f"signer[{i}].party is required")

    party = s["party"]
    parties = party if isinstance(party, list) else [party]
    if not parties:
        raise ValueError(f"signer[{i}].party must not be empty")
    for j, p in enumerate(parties):
        if not isinstance(p, dict):
            raise ValueError(
                f"signer[{i}].party[{j}] must be an object"
            )
        ref = p.get("reference")
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError(
                f"signer[{i}].party[{j}].reference must be a "
                "non-empty string"
            )
        if not ref_pattern.match(ref):
            raise ValueError(
                f"signer[{i}].party[{j}].reference must match "
                "'ResourceType/id' format"
            )

    if "signature" not in s:
        raise ValueError(
            f"signer[{i}].signature is required and must be a "
            "non-empty array"
        )
    sigs = s["signature"]
    if not isinstance(sigs, list) or not sigs:
        raise ValueError(
            f"signer[{i}].signature must be a non-empty array "
            "of Signature objects"
        )
    for j, sig in enumerate(sigs):
        if not isinstance(sig, dict):
            raise ValueError(
                f"signer[{i}].signature[{j}] must be an object"
            )
        data = sig.get("data")
        if not isinstance(data, str) or not data.strip():
            raise ValueError(
                f"signer[{i}].signature[{j}].data must be a "
                "non-empty string"
            )


# ── Concept scope helpers ─────────────────────────────────────────────

_CONCEPT_URL_RE = re.compile(
    r"^https?://.+/api/v1/concepts/[0-9a-f\-]{36}$"
)

ALLOWED_TERM_TYPES = frozenset({"request_scope", "return_scope"})

ALLOWED_ASSET_TYPES = {
    "request_scope": frozenset({"outbound_concept"}),
    "return_scope": frozenset({"obligatory_return", "optional_return"}),
}


def _validate_terms(terms: Any) -> None:
    """Validate the term[] array for concept scope definitions."""
    if not isinstance(terms, list):
        raise ValueError("term must be an array")

    seen_types: set[str] = set()

    for i, term in enumerate(terms):
        if not isinstance(term, dict):
            raise ValueError(f"term[{i}] must be an object")

        # type.text is required and must be a known scope type
        term_type = (term.get("type") or {}).get("text")
        if not term_type:
            raise ValueError(f"term[{i}].type.text is required")
        if term_type not in ALLOWED_TERM_TYPES:
            raise ValueError(
                f"term[{i}].type.text must be one of: "
                f"{', '.join(sorted(ALLOWED_TERM_TYPES))}"
            )
        if term_type in seen_types:
            raise ValueError(f"duplicate term type '{term_type}'")
        seen_types.add(term_type)

        # offer.text is optional (human description)
        if "offer" in term:
            if not isinstance(term["offer"], dict):
                raise ValueError(f"term[{i}].offer must be an object")

        # asset[] is required and must contain valid concept references
        assets = term.get("asset")
        if not isinstance(assets, list) or not assets:
            raise ValueError(f"term[{i}].asset must be a non-empty array")

        allowed_types = ALLOWED_ASSET_TYPES[term_type]
        for j, asset in enumerate(assets):
            if not isinstance(asset, dict):
                raise ValueError(f"term[{i}].asset[{j}] must be an object")

            # asset type
            asset_types = asset.get("type")
            if not isinstance(asset_types, list) or not asset_types:
                raise ValueError(
                    f"term[{i}].asset[{j}].type must be a non-empty array"
                )
            asset_type_text = asset_types[0].get("text") if isinstance(asset_types[0], dict) else None
            if not asset_type_text:
                raise ValueError(
                    f"term[{i}].asset[{j}].type[0].text is required"
                )
            if asset_type_text not in allowed_types:
                raise ValueError(
                    f"term[{i}].asset[{j}].type[0].text must be one of: "
                    f"{', '.join(sorted(allowed_types))} "
                    f"(for term type '{term_type}')"
                )

            # typeReference[] — concept URLs
            refs = asset.get("typeReference")
            if not isinstance(refs, list) or not refs:
                raise ValueError(
                    f"term[{i}].asset[{j}].typeReference must be a non-empty array"
                )
            for k, ref in enumerate(refs):
                if not isinstance(ref, dict):
                    raise ValueError(
                        f"term[{i}].asset[{j}].typeReference[{k}] must be an object"
                    )
                reference = ref.get("reference", "")
                if not _CONCEPT_URL_RE.match(reference):
                    raise ValueError(
                        f"term[{i}].asset[{j}].typeReference[{k}].reference "
                        f"must be a valid concept URL "
                        f"(https://…/api/v1/concepts/<uuid>)"
                    )


def get_contract_scope(fhir_contract: dict[str, Any]) -> dict[str, Any] | None:
    """Extract structured concept scope from a FHIR Contract resource.

    Returns:
        {
            "request_scope": [
                {"concept_guid": "<uuid>", "concept_url": "https://…/api/v1/concepts/<uuid>"},
                ...
            ] or None,
            "return_scope": {
                "obligatory_return": [
                    {"concept_guid": "<uuid>", "concept_url": "https://…/api/v1/concepts/<uuid>"},
                    ...
                ],
                "optional_return": [
                    {"concept_guid": "<uuid>", "concept_url": "https://…/api/v1/concepts/<uuid>"},
                    ...
                ]
            } or None
        }
        Returns None if no term[] is defined (backward compatible = all permitted).
    """
    terms = fhir_contract.get("term")
    if not terms:
        return None

    result: dict[str, Any] = {"request_scope": None, "return_scope": None}
    found_any = False

    for term in terms:
        term_type = (term.get("type") or {}).get("text")
        if term_type not in ALLOWED_TERM_TYPES:
            continue

        concepts_by_asset_type: dict[str, list[dict[str, str]]] = {}
        for asset in term.get("asset", []):
            asset_type_text = (asset.get("type", [{}])[0] or {}).get("text", "")
            concept_entries: list[dict[str, str]] = []
            for ref in asset.get("typeReference", []):
                reference = ref.get("reference", "")
                if "/concepts/" in reference:
                    guid = reference.split("/concepts/")[-1]
                    concept_entries.append({
                        "concept_guid": guid,
                        "concept_url": reference,
                    })
            if concept_entries:
                concepts_by_asset_type[asset_type_text] = concept_entries

        if term_type == "request_scope":
            all_concepts: list[dict[str, str]] = []
            for c_list in concepts_by_asset_type.values():
                all_concepts.extend(c_list)
            if all_concepts:
                result["request_scope"] = all_concepts
                found_any = True

        elif term_type == "return_scope":
            return_scope = {
                "obligatory_return": concepts_by_asset_type.get("obligatory_return", []),
                "optional_return": concepts_by_asset_type.get("optional_return", []),
            }
            if return_scope["obligatory_return"] or return_scope["optional_return"]:
                result["return_scope"] = return_scope
                found_any = True

    return result if found_any else None

