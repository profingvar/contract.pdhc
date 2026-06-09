"""Ticket #230 — Contract.signer[] structural validation +
reference resolution.

Two layers:

1. Structural validation in ``fhir.ensure_contract_shape``
   (pure, no I/O): signer[] must be a list of objects; each entry
   needs ``type``, ``party`` (well-shaped reference), and a
   non-empty ``signature[].data``.

2. Reference resolution in ``signer_resolver.verify_signer_references``
   called from the create/update routes: patient signers resolve via
   IPS (best-effort when IPS_BASE_URL is empty), User/Practitioner
   resolve via the local users table, Organization is accepted on
   shape alone for now (no SSO client in contract.pdhc yet).

Acceptance (from the ticket):
- A contract with an unresolvable signer is rejected.
- A contract with empty signature is rejected.
- Valid contracts unchanged.
"""
from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Env setup (mirrors test_health_and_contracts.py)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test")
    monkeypatch.setenv(
        "DATABASE_URL",
        os.getenv("TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:"),
    )
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", "test-service-key-12345")
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("STRICT_SCOPE_CONCEPTS", "false")
    # By default keep IPS resolution off so tests focus on shape +
    # local-user resolution. Specific tests opt in.
    monkeypatch.setenv("IPS_BASE_URL", "")
    # Default strict; specific tests opt out.
    monkeypatch.setenv("STRICT_SIGNER_VALIDATION", "true")


@pytest.fixture()
def app():
    from app.main import create_app
    return create_app()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin_token(client):
    r = client.post(
        "/auth/login",
        json={"username": "admin", "password": "password"},
    )
    assert r.status_code == 200
    return r.json["access_token"]


def auth(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signer(
    *,
    type_code="SELF",
    party_ref="Patient/pat-1",
    sig_data="aGVsbG8=",  # base64 'hello'
):
    """Build a minimal valid signer object."""
    return {
        "type": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/contractsignertypecodes",
                "code": type_code,
            }],
        },
        "party": {"reference": party_ref},
        "signature": [{
            "type": [{"system": "x", "code": "y"}],
            "when": "2026-06-09T10:00:00Z",
            "who": {"reference": party_ref},
            "data": sig_data,
        }],
    }


def _contract(*, signers=None, status="executable"):
    body = {
        "resourceType": "Contract",
        "status": status,
        "period": {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-12-31T23:59:59Z",
        },
    }
    if signers is not None:
        body["signer"] = signers
    return body


# ---------------------------------------------------------------------------
# Layer 1 — structural validation
# ---------------------------------------------------------------------------

class TestSignerShape:
    def test_valid_signer_passes(self, app):
        from app.fhir import ensure_contract_shape
        with app.app_context():
            ensure_contract_shape(_contract(signers=[_signer()]))

    def test_no_signer_section_passes(self, app):
        """Signer is optional. A contract with no signer field is
        valid."""
        from app.fhir import ensure_contract_shape
        with app.app_context():
            ensure_contract_shape(_contract())

    def test_empty_signer_array_passes(self, app):
        """signer: [] is also fine — explicitly empty list."""
        from app.fhir import ensure_contract_shape
        with app.app_context():
            ensure_contract_shape(_contract(signers=[]))

    def test_non_array_signer_fails(self, app):
        from app.fhir import ensure_contract_shape
        with pytest.raises(ValueError, match="signer must be an array"):
            ensure_contract_shape({**_contract(), "signer": "x"})

    def test_signer_missing_type_fails(self, app):
        from app.fhir import ensure_contract_shape
        s = _signer()
        s.pop("type")
        with pytest.raises(ValueError, match="signer\\[0\\].type"):
            ensure_contract_shape(_contract(signers=[s]))

    def test_signer_type_not_object_fails(self, app):
        from app.fhir import ensure_contract_shape
        s = _signer()
        s["type"] = "SELF"  # string instead of CodeableConcept
        with pytest.raises(
            ValueError, match="signer\\[0\\].type must be a CodeableConcept"
        ):
            ensure_contract_shape(_contract(signers=[s]))

    def test_signer_missing_party_fails(self, app):
        from app.fhir import ensure_contract_shape
        s = _signer()
        s.pop("party")
        with pytest.raises(
            ValueError, match="signer\\[0\\].party is required"
        ):
            ensure_contract_shape(_contract(signers=[s]))

    def test_signer_party_empty_reference_fails(self, app):
        from app.fhir import ensure_contract_shape
        s = _signer()
        s["party"] = {"reference": "   "}
        with pytest.raises(ValueError, match="reference must be"):
            ensure_contract_shape(_contract(signers=[s]))

    def test_signer_party_bad_reference_shape_fails(self, app):
        from app.fhir import ensure_contract_shape
        s = _signer()
        s["party"] = {"reference": "not-a-resource-ref"}
        with pytest.raises(
            ValueError, match="ResourceType/id"
        ):
            ensure_contract_shape(_contract(signers=[s]))

    def test_signer_signature_missing_fails(self, app):
        from app.fhir import ensure_contract_shape
        s = _signer()
        s.pop("signature")
        with pytest.raises(
            ValueError, match="signer\\[0\\].signature is required"
        ):
            ensure_contract_shape(_contract(signers=[s]))

    def test_signer_signature_empty_array_fails(self, app):
        from app.fhir import ensure_contract_shape
        s = _signer()
        s["signature"] = []
        with pytest.raises(
            ValueError, match="non-empty array of Signature"
        ):
            ensure_contract_shape(_contract(signers=[s]))

    def test_signer_signature_data_missing_fails(self, app):
        from app.fhir import ensure_contract_shape
        s = _signer()
        s["signature"][0].pop("data")
        with pytest.raises(
            ValueError, match="signature\\[0\\].data must be"
        ):
            ensure_contract_shape(_contract(signers=[s]))

    def test_signer_signature_data_empty_fails(self, app):
        from app.fhir import ensure_contract_shape
        s = _signer()
        s["signature"][0]["data"] = "   "
        with pytest.raises(
            ValueError, match="signature\\[0\\].data must be"
        ):
            ensure_contract_shape(_contract(signers=[s]))


# ---------------------------------------------------------------------------
# Layer 2 — reference resolution
# ---------------------------------------------------------------------------

class TestPatientReferenceResolution:
    def test_ips_unset_skips_resolution(
        self, app, client, admin_token,
    ):
        """Local dev: IPS_BASE_URL=='' → patient resolution is a noop;
        contract with a 'fake' patient signer is accepted."""
        # IPS_BASE_URL is "" by default in the test env.
        r = client.post(
            "/fhir/Contract",
            json=_contract(signers=[
                _signer(party_ref="Patient/never-existed"),
            ]),
            headers=auth(admin_token),
        )
        assert r.status_code == 201, r.get_json()

    def test_ips_returns_404_rejects_contract(
        self, app, client, admin_token, monkeypatch,
    ):
        monkeypatch.setenv("IPS_BASE_URL", "http://ips.test")
        fake = MagicMock()
        fake.status_code = 404
        with patch(
            "app.signer_resolver.http_requests.get",
            return_value=fake,
        ):
            r = client.post(
                "/fhir/Contract",
                json=_contract(signers=[
                    _signer(party_ref="Patient/missing"),
                ]),
                headers=auth(admin_token),
            )
        assert r.status_code == 400
        body = r.get_json()
        assert body["error"] == "signer_unresolved"
        assert any(
            "Patient/missing" in f for f in body["failures"]
        )

    def test_ips_returns_200_accepts_contract(
        self, app, client, admin_token, monkeypatch,
    ):
        monkeypatch.setenv("IPS_BASE_URL", "http://ips.test")
        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {"resourceType": "Patient"}
        with patch(
            "app.signer_resolver.http_requests.get",
            return_value=fake,
        ):
            r = client.post(
                "/fhir/Contract",
                json=_contract(signers=[
                    _signer(party_ref="Patient/exists"),
                ]),
                headers=auth(admin_token),
            )
        assert r.status_code == 201

    def test_strict_off_downgrades_404(
        self, app, client, admin_token, monkeypatch,
    ):
        monkeypatch.setenv("IPS_BASE_URL", "http://ips.test")
        monkeypatch.setenv("STRICT_SIGNER_VALIDATION", "false")
        fake = MagicMock()
        fake.status_code = 404
        with patch(
            "app.signer_resolver.http_requests.get",
            return_value=fake,
        ):
            r = client.post(
                "/fhir/Contract",
                json=_contract(signers=[
                    _signer(party_ref="Patient/missing"),
                ]),
                headers=auth(admin_token),
            )
        # Lenient mode: write proceeds.
        assert r.status_code == 201

    def test_network_error_in_strict_mode_rejects(
        self, app, client, admin_token, monkeypatch,
    ):
        monkeypatch.setenv("IPS_BASE_URL", "http://ips.test")
        import requests as http_requests
        with patch(
            "app.signer_resolver.http_requests.get",
            side_effect=http_requests.ConnectionError("nope"),
        ):
            r = client.post(
                "/fhir/Contract",
                json=_contract(signers=[
                    _signer(party_ref="Patient/x"),
                ]),
                headers=auth(admin_token),
            )
        assert r.status_code == 400
        assert "IPS unreachable" in str(r.get_json()["failures"])


class TestUserPractitionerResolution:
    def _make_user(self, client, admin_token, *, role="reader"):
        """Create a user through the same admin endpoint the tests use
        — guarantees we hit the same DB the running app sees."""
        import uuid as _uuid
        r = client.post(
            "/admin/users",
            json={
                "username": f"u-{_uuid.uuid4().hex[:8]}",
                "password": "Test1234!",
                "role": role,
            },
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 201, r.get_json()
        return r.get_json()["guid"]

    def test_unresolvable_user_rejects_in_strict_mode(
        self, app, client, admin_token,
    ):
        r = client.post(
            "/fhir/Contract",
            json=_contract(signers=[
                _signer(party_ref="User/00000000-0000-0000-0000-000000000000"),
            ]),
            headers=auth(admin_token),
        )
        assert r.status_code == 400
        body = r.get_json()
        assert "User" in str(body["failures"])

    def test_resolvable_user_accepts(self, app, client, admin_token):
        guid = self._make_user(client, admin_token)
        r = client.post(
            "/fhir/Contract",
            json=_contract(signers=[
                _signer(party_ref=f"User/{guid}"),
            ]),
            headers=auth(admin_token),
        )
        assert r.status_code == 201

    def test_lenient_mode_accepts_unresolvable_user(
        self, app, client, admin_token, monkeypatch,
    ):
        monkeypatch.setenv("STRICT_SIGNER_VALIDATION", "false")
        r = client.post(
            "/fhir/Contract",
            json=_contract(signers=[
                _signer(party_ref="Practitioner/ghost-99"),
            ]),
            headers=auth(admin_token),
        )
        assert r.status_code == 201


class TestOrganisationSignerAcceptedOnShape:
    def test_org_signer_passes_without_sso_client(
        self, app, client, admin_token,
    ):
        """Organization signers are accepted on shape alone until
        an SSO client lands in contract.pdhc. Documented in
        signer_resolver.py module docstring."""
        r = client.post(
            "/fhir/Contract",
            json=_contract(signers=[
                _signer(party_ref="Organization/unverified-org"),
            ]),
            headers=auth(admin_token),
        )
        assert r.status_code == 201


# ---------------------------------------------------------------------------
# Combined: route-level rejection messages carry field-level detail
# ---------------------------------------------------------------------------

class TestRoute400Shape:
    def test_shape_error_returns_400_with_field_message(
        self, app, client, admin_token,
    ):
        bad = _signer()
        bad.pop("signature")
        r = client.post(
            "/fhir/Contract",
            json=_contract(signers=[bad]),
            headers=auth(admin_token),
        )
        assert r.status_code == 400
        body = r.get_json()
        assert body["error"] == "validation"
        assert "signer[0].signature" in body["message"]

    def test_resolution_error_returns_failures_list(
        self, app, client, admin_token,
    ):
        r = client.post(
            "/fhir/Contract",
            json=_contract(signers=[
                _signer(party_ref="User/never-existed"),
            ]),
            headers=auth(admin_token),
        )
        assert r.status_code == 400
        body = r.get_json()
        assert body["error"] == "signer_unresolved"
        assert isinstance(body["failures"], list)
        assert len(body["failures"]) >= 1
