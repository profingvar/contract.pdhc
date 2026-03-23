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


def build_capability_statement() -> dict[str, Any]:
    """Return a full FHIR R5 CapabilityStatement for this Contract server."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
        "date": now,
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
                                    "system": "http://terminology.hl7.org/CodeSystem/restful-security-service",
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
                        "searchParam": [],
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

    return resource

