"""Tests for contract scope reforms (items 1.1–1.4)."""
from __future__ import annotations

import os
import uuid

import pytest


CONCEPT_A = str(uuid.uuid4())
CONCEPT_B = str(uuid.uuid4())
CONCEPT_C = str(uuid.uuid4())

CONCEPT_URL_A = f"https://plan.pdhc.se/api/v1/concepts/{CONCEPT_A}"
CONCEPT_URL_B = f"https://plan.pdhc.se/api/v1/concepts/{CONCEPT_B}"
CONCEPT_URL_C = f"https://plan.pdhc.se/api/v1/concepts/{CONCEPT_C}"

SERVICE_KEY = "test-service-key-12345"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test")
    monkeypatch.setenv("DATABASE_URL", os.getenv("TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:"))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    # The existing term[] tests use random concept GUIDs that don't
    # exist in plan.pdhc, and the test suite doesn't run plan.pdhc.
    # Turning STRICT off keeps them passing; the strict path has its
    # own dedicated tests below (TestScopeConceptValidation).
    monkeypatch.setenv("STRICT_SCOPE_CONCEPTS", "false")


@pytest.fixture()
def client():
    from app.main import create_app
    app = create_app()
    return app.test_client()


@pytest.fixture()
def admin_token(client):
    r = client.post("/auth/login", json={"username": "admin", "password": "password"})
    assert r.status_code == 200
    return r.json["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _make_scoped_contract():
    """Return a FHIR Contract with full request_scope + return_scope."""
    return {
        "resourceType": "Contract",
        "status": "executed",
        "term": [
            {
                "type": {"text": "request_scope"},
                "offer": {"text": "Concepts permitted in outbound ServiceRequests"},
                "asset": [
                    {
                        "type": [{"text": "outbound_concept"}],
                        "typeReference": [
                            {"reference": CONCEPT_URL_A, "display": "Spirometri"},
                            {"reference": CONCEPT_URL_B, "display": "ACQ"},
                        ],
                    }
                ],
            },
            {
                "type": {"text": "return_scope"},
                "offer": {"text": "Concepts the provider may submit observations for"},
                "asset": [
                    {
                        "type": [{"text": "obligatory_return"}],
                        "typeReference": [
                            {"reference": CONCEPT_URL_A, "display": "Spirometri"},
                        ],
                    },
                    {
                        "type": [{"text": "optional_return"}],
                        "typeReference": [
                            {"reference": CONCEPT_URL_B, "display": "ACQ"},
                            {"reference": CONCEPT_URL_C, "display": "Peak Flow"},
                        ],
                    },
                ],
            },
        ],
    }


def _create_contract(client, admin_token, payload):
    r = client.post("/fhir/Contract", json=payload, headers=auth(admin_token))
    assert r.status_code == 201, f"Create failed: {r.json}"
    return r.json["id"]


# ── 1.1 FHIR term[] validation ──────────────────────────────────────


class TestTermValidation:
    def test_contract_with_valid_scope_accepted(self, client, admin_token):
        guid = _create_contract(client, admin_token, _make_scoped_contract())
        r = client.get(f"/fhir/Contract/{guid}")
        assert r.status_code == 200
        assert "term" in r.json

    def test_contract_without_term_accepted(self, client, admin_token):
        """Backward compatible — no term[] is fine."""
        guid = _create_contract(client, admin_token, {
            "resourceType": "Contract", "status": "executable"
        })
        assert guid

    def test_term_must_be_array(self, client, admin_token):
        r = client.post("/fhir/Contract", json={
            "resourceType": "Contract", "status": "executable",
            "term": "bad",
        }, headers=auth(admin_token))
        assert r.status_code == 400
        assert "term must be an array" in r.json["message"]

    def test_unknown_term_type_rejected(self, client, admin_token):
        r = client.post("/fhir/Contract", json={
            "resourceType": "Contract", "status": "executable",
            "term": [{"type": {"text": "bogus_scope"}, "asset": []}],
        }, headers=auth(admin_token))
        assert r.status_code == 400
        assert "request_scope" in r.json["message"]

    def test_duplicate_term_type_rejected(self, client, admin_token):
        term = {
            "type": {"text": "request_scope"},
            "asset": [{
                "type": [{"text": "outbound_concept"}],
                "typeReference": [{"reference": CONCEPT_URL_A}],
            }],
        }
        r = client.post("/fhir/Contract", json={
            "resourceType": "Contract", "status": "executable",
            "term": [term, term],
        }, headers=auth(admin_token))
        assert r.status_code == 400
        assert "duplicate" in r.json["message"].lower()

    def test_missing_type_text_rejected(self, client, admin_token):
        r = client.post("/fhir/Contract", json={
            "resourceType": "Contract", "status": "executable",
            "term": [{"type": {}, "asset": []}],
        }, headers=auth(admin_token))
        assert r.status_code == 400
        assert "type.text is required" in r.json["message"]

    def test_empty_asset_rejected(self, client, admin_token):
        r = client.post("/fhir/Contract", json={
            "resourceType": "Contract", "status": "executable",
            "term": [{"type": {"text": "request_scope"}, "asset": []}],
        }, headers=auth(admin_token))
        assert r.status_code == 400
        assert "non-empty" in r.json["message"]

    def test_wrong_asset_type_for_term_rejected(self, client, admin_token):
        """request_scope should not accept obligatory_return assets."""
        r = client.post("/fhir/Contract", json={
            "resourceType": "Contract", "status": "executable",
            "term": [{
                "type": {"text": "request_scope"},
                "asset": [{
                    "type": [{"text": "obligatory_return"}],
                    "typeReference": [{"reference": CONCEPT_URL_A}],
                }],
            }],
        }, headers=auth(admin_token))
        assert r.status_code == 400
        assert "outbound_concept" in r.json["message"]

    def test_bad_concept_url_rejected(self, client, admin_token):
        r = client.post("/fhir/Contract", json={
            "resourceType": "Contract", "status": "executable",
            "term": [{
                "type": {"text": "request_scope"},
                "asset": [{
                    "type": [{"text": "outbound_concept"}],
                    "typeReference": [{"reference": "not-a-url"}],
                }],
            }],
        }, headers=auth(admin_token))
        assert r.status_code == 400
        assert "concept URL" in r.json["message"]

    def test_missing_typeReference_rejected(self, client, admin_token):
        r = client.post("/fhir/Contract", json={
            "resourceType": "Contract", "status": "executable",
            "term": [{
                "type": {"text": "request_scope"},
                "asset": [{
                    "type": [{"text": "outbound_concept"}],
                }],
            }],
        }, headers=auth(admin_token))
        assert r.status_code == 400
        assert "typeReference" in r.json["message"]


# ── 1.2 Scope extraction (get_contract_scope) ───────────────────────


class TestScopeExtraction:
    def test_extract_full_scope(self):
        from app.fhir import get_contract_scope
        contract = _make_scoped_contract()
        scope = get_contract_scope(contract)
        assert scope is not None
        req_guids = {e["concept_guid"] for e in scope["request_scope"]}
        assert req_guids == {CONCEPT_A, CONCEPT_B}
        # Verify each entry also has concept_url
        for entry in scope["request_scope"]:
            assert entry["concept_url"].endswith(f"/concepts/{entry['concept_guid']}")
        assert scope["return_scope"]["obligatory_return"] == [
            {"concept_guid": CONCEPT_A, "concept_url": CONCEPT_URL_A}
        ]
        opt_guids = {e["concept_guid"] for e in scope["return_scope"]["optional_return"]}
        assert opt_guids == {CONCEPT_B, CONCEPT_C}

    def test_extract_no_terms_returns_none(self):
        from app.fhir import get_contract_scope
        assert get_contract_scope({"resourceType": "Contract", "status": "executed"}) is None

    def test_extract_empty_terms_returns_none(self):
        from app.fhir import get_contract_scope
        assert get_contract_scope({"term": []}) is None

    def test_extract_request_scope_only(self):
        from app.fhir import get_contract_scope
        contract = {
            "term": [{
                "type": {"text": "request_scope"},
                "asset": [{
                    "type": [{"text": "outbound_concept"}],
                    "typeReference": [{"reference": CONCEPT_URL_A}],
                }],
            }]
        }
        scope = get_contract_scope(contract)
        assert scope is not None
        assert scope["request_scope"] == [
            {"concept_guid": CONCEPT_A, "concept_url": CONCEPT_URL_A}
        ]
        assert scope["return_scope"] is None

    def test_extract_return_scope_only(self):
        from app.fhir import get_contract_scope
        contract = {
            "term": [{
                "type": {"text": "return_scope"},
                "asset": [
                    {
                        "type": [{"text": "obligatory_return"}],
                        "typeReference": [{"reference": CONCEPT_URL_B}],
                    },
                ],
            }]
        }
        scope = get_contract_scope(contract)
        assert scope is not None
        assert scope["request_scope"] is None
        assert scope["return_scope"]["obligatory_return"] == [
            {"concept_guid": CONCEPT_B, "concept_url": CONCEPT_URL_B}
        ]
        assert scope["return_scope"]["optional_return"] == []


# ── 1.3 Scope endpoint (public) ─────────────────────────────────────


class TestScopeEndpointPublic:
    def test_scoped_contract(self, client, admin_token):
        guid = _create_contract(client, admin_token, _make_scoped_contract())
        r = client.get(f"/fhir/Contract/{guid}/scope")
        assert r.status_code == 200
        data = r.json
        assert data["contract_guid"] == guid
        assert data["status"] == "executed"
        assert data["scope_defined"] is True
        req_guids = {e["concept_guid"] for e in data["request_scope"]}
        assert req_guids == {CONCEPT_A, CONCEPT_B}
        assert data["return_scope"]["obligatory_return"] == [
            {"concept_guid": CONCEPT_A, "concept_url": CONCEPT_URL_A}
        ]
        opt_guids = {e["concept_guid"] for e in data["return_scope"]["optional_return"]}
        assert opt_guids == {CONCEPT_B, CONCEPT_C}

    def test_legacy_contract_no_scope(self, client, admin_token):
        guid = _create_contract(client, admin_token, {
            "resourceType": "Contract", "status": "executable"
        })
        r = client.get(f"/fhir/Contract/{guid}/scope")
        assert r.status_code == 200
        data = r.json
        assert data["scope_defined"] is False
        assert data["request_scope"] is None
        assert data["return_scope"] is None

    def test_revoked_contract_empty_scope(self, client, admin_token):
        # Create then revoke
        payload = _make_scoped_contract()
        guid = _create_contract(client, admin_token, payload)
        payload["id"] = guid
        payload["status"] = "revoked"
        r = client.put(f"/fhir/Contract/{guid}", json=payload, headers=auth(admin_token))
        assert r.status_code == 200

        r = client.get(f"/fhir/Contract/{guid}/scope")
        assert r.status_code == 200
        data = r.json
        assert data["status"] == "revoked"
        assert data["scope_defined"] is True
        assert data["request_scope"] == []
        assert data["return_scope"]["obligatory_return"] == []
        assert data["return_scope"]["optional_return"] == []

    def test_terminated_contract_empty_scope(self, client, admin_token):
        payload = _make_scoped_contract()
        payload["status"] = "terminated"
        guid = _create_contract(client, admin_token, payload)
        r = client.get(f"/fhir/Contract/{guid}/scope")
        assert r.status_code == 200
        assert r.json["status"] == "terminated"
        assert r.json["request_scope"] == []

    def test_not_found(self, client):
        r = client.get(f"/fhir/Contract/{uuid.uuid4()}/scope")
        assert r.status_code == 404


# ── 1.4 Internal service key auth ───────────────────────────────────


class TestInternalScopeEndpoint:
    def test_valid_service_key(self, client, admin_token):
        guid = _create_contract(client, admin_token, _make_scoped_contract())
        r = client.get(
            f"/internal/contract/{guid}/scope",
            headers={"X-Service-Key": SERVICE_KEY},
        )
        assert r.status_code == 200
        assert r.json["scope_defined"] is True
        req_guids = {e["concept_guid"] for e in r.json["request_scope"]}
        assert req_guids == {CONCEPT_A, CONCEPT_B}

    def test_missing_service_key(self, client, admin_token):
        guid = _create_contract(client, admin_token, _make_scoped_contract())
        r = client.get(f"/internal/contract/{guid}/scope")
        assert r.status_code == 401
        assert r.json["error"] == "unauthorized"

    def test_invalid_service_key(self, client, admin_token):
        guid = _create_contract(client, admin_token, _make_scoped_contract())
        r = client.get(
            f"/internal/contract/{guid}/scope",
            headers={"X-Service-Key": "wrong-key"},
        )
        assert r.status_code == 401

    def test_internal_not_found(self, client):
        r = client.get(
            f"/internal/contract/{uuid.uuid4()}/scope",
            headers={"X-Service-Key": SERVICE_KEY},
        )
        assert r.status_code == 404

    def test_internal_returns_parties(self, client, admin_token):
        """Internal scope endpoint must surface requesting + provider org guids
        from party[] so downstream services (gateway → dashboard) can
        org-scope inbound observations by the requesting org."""
        req_org = str(uuid.uuid4())
        prov_org = str(uuid.uuid4())
        payload = _make_scoped_contract()
        payload["party"] = [
            {
                "role": [{"coding": [{"code": "payer"}]}],
                "reference": [{"reference": f"Organization/{req_org}"}],
            },
            {
                "role": [{"coding": [{"code": "provider"}]}],
                "reference": [{"reference": f"Organization/{prov_org}"}],
            },
        ]
        guid = _create_contract(client, admin_token, payload)
        r = client.get(
            f"/internal/contract/{guid}/scope",
            headers={"X-Service-Key": SERVICE_KEY},
        )
        assert r.status_code == 200
        parties = r.json.get("parties") or {}
        assert parties.get("requesting_org_guid") == req_org
        assert prov_org in (parties.get("provider_org_guids") or [])

    def test_internal_revoked_contract(self, client, admin_token):
        payload = _make_scoped_contract()
        guid = _create_contract(client, admin_token, payload)
        payload["id"] = guid
        payload["status"] = "revoked"
        client.put(f"/fhir/Contract/{guid}", json=payload, headers=auth(admin_token))

        r = client.get(
            f"/internal/contract/{guid}/scope",
            headers={"X-Service-Key": SERVICE_KEY},
        )
        assert r.status_code == 200
        assert r.json["status"] == "revoked"
        assert r.json["request_scope"] == []


# ── Integration: round-trip ──────────────────────────────────────────


class TestScopeRoundTrip:
    def test_create_scoped_contract_then_query_scope(self, client, admin_token):
        """Create a contract with scope via API, then query scope via both endpoints."""
        guid = _create_contract(client, admin_token, _make_scoped_contract())

        # Public endpoint
        r1 = client.get(f"/fhir/Contract/{guid}/scope")
        assert r1.status_code == 200

        # Internal endpoint
        r2 = client.get(
            f"/internal/contract/{guid}/scope",
            headers={"X-Service-Key": SERVICE_KEY},
        )
        assert r2.status_code == 200

        # Both return the same scope
        assert r1.json["request_scope"] == r2.json["request_scope"]
        assert r1.json["return_scope"] == r2.json["return_scope"]
        assert r1.json["scope_defined"] == r2.json["scope_defined"]

    def test_update_scope_reflected_in_endpoint(self, client, admin_token):
        """Create contract, update to add scope, verify endpoint reflects change."""
        # Create without scope
        guid = _create_contract(client, admin_token, {
            "resourceType": "Contract", "status": "executed"
        })
        r = client.get(f"/fhir/Contract/{guid}/scope")
        assert r.json["scope_defined"] is False

        # Update with scope
        updated = _make_scoped_contract()
        updated["id"] = guid
        client.put(f"/fhir/Contract/{guid}", json=updated, headers=auth(admin_token))

        r = client.get(f"/fhir/Contract/{guid}/scope")
        assert r.json["scope_defined"] is True
        req_guids = {e["concept_guid"] for e in r.json["request_scope"]}
        assert CONCEPT_A in req_guids


# ── 1.5 Concept-existence validation against plan.pdhc (ticket #135) ──


class TestScopeConceptValidation:
    """STRICT_SCOPE_CONCEPTS=true: every concept GUID in term[] must
    exist in plan.pdhc. plan.pdhc is mocked here — we only verify the
    contract-side behaviour."""

    @pytest.fixture(autouse=True)
    def _strict(self, monkeypatch):
        monkeypatch.setenv("STRICT_SCOPE_CONCEPTS", "true")
        monkeypatch.setenv("PLAN_BASE_URL", "https://plan.test")

    def _mock_plan(self, monkeypatch, present):
        """Mock plan.pdhc to return 200 for `present` GUIDs, else 404."""
        import requests as _r
        present = set(present)

        class FakeResp:
            def __init__(self, status):
                self.status_code = status

            def json(self):
                return {}

        def fake_get(url, **kw):
            guid = url.rstrip("/").rsplit("/", 1)[-1]
            return FakeResp(200 if guid in present else 404)

        monkeypatch.setattr(_r, "get", fake_get)

    def test_accepts_when_all_concepts_exist(self, client, admin_token, monkeypatch):
        self._mock_plan(monkeypatch, [CONCEPT_A, CONCEPT_B, CONCEPT_C])
        r = client.post(
            "/fhir/Contract",
            json=_make_scoped_contract(),
            headers=auth(admin_token),
        )
        assert r.status_code == 201, r.json

    def test_rejects_with_422_when_concept_missing(self, client, admin_token, monkeypatch):
        # CONCEPT_C unknown to plan.pdhc
        self._mock_plan(monkeypatch, [CONCEPT_A, CONCEPT_B])
        r = client.post(
            "/fhir/Contract",
            json=_make_scoped_contract(),
            headers=auth(admin_token),
        )
        assert r.status_code == 422
        assert r.json["error"] == "scope_concept_missing"
        assert CONCEPT_C in r.json["missing_concept_guids"]

    def test_returns_503_when_plan_unreachable(self, client, admin_token, monkeypatch):
        import requests as _r

        def boom(*a, **kw):
            raise _r.ConnectionError("plan offline")

        monkeypatch.setattr(_r, "get", boom)
        r = client.post(
            "/fhir/Contract",
            json=_make_scoped_contract(),
            headers=auth(admin_token),
        )
        assert r.status_code == 503
        assert r.json["error"] == "scope_validation_unavailable"

    def test_contract_without_term_skips_validation(self, client, admin_token, monkeypatch):
        # plan.pdhc not even mocked — should never be called
        def must_not_call(*a, **kw):
            raise AssertionError("plan.pdhc should not be called for no-term contracts")

        import requests as _r
        monkeypatch.setattr(_r, "get", must_not_call)
        r = client.post(
            "/fhir/Contract",
            json={"resourceType": "Contract", "status": "executable"},
            headers=auth(admin_token),
        )
        assert r.status_code == 201
