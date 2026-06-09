"""Ticket #231 — contract.pdhc emits PatientConsent rows.

Three layers:

1. Pure FHIR parsing (_extract_patient_signers,
   _extract_provider_org_guids, _extract_expires_at).
2. HTTP layer (_list_active_consents, _post_grant, _post_revoke) —
   exercised through emit_patient_consents / revoke_patient_consents
   with the ``requests`` module mocked.
3. End-to-end via the Flask routes (POST/PUT/DELETE /fhir/Contract)
   — mocked at the same boundary; verifies that the route invokes the
   emitter and that the emitter does the right thing.

Acceptance (from the ticket):
- A contract signed by a patient produces a PatientConsent row.
- Re-signing is idempotent.
- Cancelling the contract revokes the consent.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Env setup (mirrors test_health_and_contracts.py so create_app works)
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
    # The emitter is a noop unless IPS_BASE_URL is set.
    monkeypatch.setenv("IPS_BASE_URL", "http://ips.test")
    monkeypatch.setenv("IPS_API_KEY", "test-ips-key")
    # Don't fail the contract write because plan.pdhc isn't reachable
    # from the test harness.
    monkeypatch.setenv("STRICT_SCOPE_CONCEPTS", "false")
    # #230: signer resolution would try to reach IPS — these tests
    # focus on the emitter, not the resolver.
    monkeypatch.setenv("STRICT_SIGNER_VALIDATION", "false")


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
    return {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Fixtures: mock the http_requests module the emitter calls
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_ips():
    """Patch the emitter's http_requests so the test owns IPS replies.

    Provides:
      mock_ips.active_consents  — list returned from GET active list
      mock_ips.grants           — list of (patient_guid, body) captured
                                  on POST grant
      mock_ips.revokes          — list of (patient_guid, consent_guid,
                                  body) captured on POST revoke
      mock_ips.grant_status     — int -> POST grant response code
      mock_ips.revoke_status    — int -> POST revoke response code
    """
    state = SimpleNamespace(
        active_consents=[],
        grants=[],
        revokes=[],
        grant_status=201,
        revoke_status=200,
    )

    def fake_get(url, params=None, headers=None, timeout=None):
        # GET /api/v1/patients/<guid>/consents?active=true
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "items": list(state.active_consents),
            "total": len(state.active_consents),
        }
        return resp

    def fake_post(url, json=None, headers=None, timeout=None):
        # POST /api/v1/patients/<patient_guid>/consents
        # or   POST /api/v1/patients/<p>/consents/<c>/revoke
        if "/revoke" in url:
            # Pull patient + consent guids out of the URL.
            parts = url.split("/api/v1/patients/", 1)[1].split("/")
            patient_guid = parts[0]
            consent_guid = parts[2]
            state.revokes.append((patient_guid, consent_guid, json))
            resp = MagicMock(status_code=state.revoke_status)
            resp.json.return_value = {"revoked": True}
            resp.text = ""
            return resp
        # Grant path.
        patient_guid = url.split(
            "/api/v1/patients/", 1,
        )[1].split("/", 1)[0]
        state.grants.append((patient_guid, json))
        resp = MagicMock(status_code=state.grant_status)
        resp.json.return_value = {"guid": "new-consent"}
        resp.text = ""
        return resp

    with patch(
        "app.consent_emitter.http_requests"
    ) as m:
        m.get.side_effect = fake_get
        m.post.side_effect = fake_post
        m.RequestException = Exception
        yield state


# ---------------------------------------------------------------------------
# FHIR parsing
# ---------------------------------------------------------------------------

class TestExtractors:
    def test_patient_signers_single(self):
        from app.consent_emitter import _extract_patient_signers
        contract = {
            "signer": [
                {"party": {"reference": "Patient/pat-a"}},
            ],
        }
        assert _extract_patient_signers(contract) == ["pat-a"]

    def test_patient_signers_list_form(self):
        from app.consent_emitter import _extract_patient_signers
        contract = {
            "signer": [
                {"party": [{"reference": "Patient/pat-a"},
                            {"reference": "Patient/pat-b"}]},
            ],
        }
        assert _extract_patient_signers(contract) == ["pat-a", "pat-b"]

    def test_patient_signers_dedup(self):
        from app.consent_emitter import _extract_patient_signers
        contract = {
            "signer": [
                {"party": {"reference": "Patient/pat-a"}},
                {"party": {"reference": "Patient/pat-a"}},
            ],
        }
        assert _extract_patient_signers(contract) == ["pat-a"]

    def test_patient_signers_ignores_non_patients(self):
        from app.consent_emitter import _extract_patient_signers
        contract = {
            "signer": [
                {"party": {"reference": "Practitioner/p-1"}},
                {"party": {"reference": "Organization/org-1"}},
            ],
        }
        assert _extract_patient_signers(contract) == []

    def test_provider_orgs(self):
        from app.consent_emitter import _extract_provider_org_guids
        contract = {
            "party": [
                {"role": [{"coding": [{"code": "provider"}]}],
                 "reference": [{"reference": "Organization/org-A"}]},
                {"role": [{"coding": [{"code": "payer"}]}],
                 "reference": [{"reference": "Organization/org-B"}]},
            ],
        }
        assert _extract_provider_org_guids(contract) == ["org-A"]

    def test_expires_at_present(self):
        from app.consent_emitter import _extract_expires_at
        contract = {"period": {"end": "2026-12-31T23:59:59Z"}}
        assert _extract_expires_at(contract) == "2026-12-31T23:59:59Z"

    def test_expires_at_missing(self):
        from app.consent_emitter import _extract_expires_at
        assert _extract_expires_at({"period": {}}) is None
        assert _extract_expires_at({}) is None


# ---------------------------------------------------------------------------
# Emit + revoke (service layer)
# ---------------------------------------------------------------------------

def _signed_contract(
    contract_id="c-1",
    status="executed",
    patients=("pat-a",),
    providers=("org-A",),
    period_end="2026-12-31T23:59:59Z",
):
    return {
        "resourceType": "Contract",
        "id": contract_id,
        "status": status,
        "period": {
            "start": "2026-01-01T00:00:00Z",
            "end": period_end,
        },
        # Well-shaped signers per #230: type + party + non-empty
        # signature[].data.
        "signer": [
            {
                "type": {"coding": [{"code": "SELF"}]},
                "party": {"reference": f"Patient/{p}"},
                "signature": [{
                    "type": [{"code": "1.2.840.10065.1.12.1.1"}],
                    "when": "2026-06-09T10:00:00Z",
                    "who": {"reference": f"Patient/{p}"},
                    "data": "aGVsbG8=",
                }],
            }
            for p in patients
        ],
        "party": [
            {
                "role": [{"coding": [{"code": "provider"}]}],
                "reference": [
                    {"reference": f"Organization/{o}"} for o in providers
                ],
            },
        ],
    }


class TestEmitConsents:
    def test_active_contract_posts_grant(self, app, mock_ips):
        from app.consent_emitter import emit_patient_consents
        with app.app_context():
            summary = emit_patient_consents(_signed_contract())
        assert summary == {
            "posted": 1, "skipped": 0, "attempted": 1, "status": "ok",
        }
        assert len(mock_ips.grants) == 1
        patient, body = mock_ips.grants[0]
        assert patient == "pat-a"
        assert body["grantee_caregiver_guid"] == "org-A"
        assert body["granted_via"] == "contract"
        assert body["contract_guid"] == "c-1"
        assert body["expires_at"] == "2026-12-31T23:59:59Z"

    def test_multi_signer_emits_per_patient_per_grantee(
        self, app, mock_ips,
    ):
        from app.consent_emitter import emit_patient_consents
        contract = _signed_contract(
            patients=("pat-a", "pat-b"),
            providers=("org-A", "org-B"),
        )
        with app.app_context():
            summary = emit_patient_consents(contract)
        # 2 patients x 2 grantees = 4 grants.
        assert summary["posted"] == 4
        assert summary["attempted"] == 4
        # Each (patient, grantee) appears exactly once.
        seen = {
            (p, b["grantee_caregiver_guid"])
            for p, b in mock_ips.grants
        }
        assert seen == {
            ("pat-a", "org-A"), ("pat-a", "org-B"),
            ("pat-b", "org-A"), ("pat-b", "org-B"),
        }

    def test_idempotent_against_existing_consent(self, app, mock_ips):
        """If IPS already holds a consent linked to this contract for
        this grantee, the emitter skips it."""
        from app.consent_emitter import emit_patient_consents
        mock_ips.active_consents = [
            {
                "guid": "existing-1",
                "patient_guid": "pat-a",
                "grantee_caregiver_guid": "org-A",
                "contract_guid": "c-1",
                "is_active": True,
            },
        ]
        with app.app_context():
            summary = emit_patient_consents(_signed_contract())
        assert summary == {
            "posted": 0, "skipped": 1, "attempted": 1, "status": "ok",
        }
        assert mock_ips.grants == []

    def test_409_treated_as_success(self, app, mock_ips):
        """Server-side duplicate-active 409 is fine — count as posted."""
        from app.consent_emitter import emit_patient_consents
        mock_ips.grant_status = 409
        with app.app_context():
            summary = emit_patient_consents(_signed_contract())
        assert summary["posted"] == 1

    def test_inactive_status_is_noop(self, app, mock_ips):
        from app.consent_emitter import emit_patient_consents
        with app.app_context():
            summary = emit_patient_consents(
                _signed_contract(status="cancelled"),
            )
        assert summary == {
            "posted": 0, "skipped": 0, "attempted": 0, "status": "noop",
        }
        assert mock_ips.grants == []

    def test_no_patient_signer_is_noop(self, app, mock_ips):
        from app.consent_emitter import emit_patient_consents
        contract = _signed_contract(patients=())
        # No patient signers means signer[] is empty.
        with app.app_context():
            summary = emit_patient_consents(contract)
        assert summary["status"] == "noop"
        assert mock_ips.grants == []

    def test_no_provider_org_is_noop(self, app, mock_ips):
        from app.consent_emitter import emit_patient_consents
        contract = _signed_contract(providers=())
        with app.app_context():
            summary = emit_patient_consents(contract)
        assert summary["status"] == "noop"
        assert mock_ips.grants == []


class TestRevokeConsents:
    def test_revokes_matching_contract_guid(self, app, mock_ips):
        from app.consent_emitter import revoke_patient_consents
        mock_ips.active_consents = [
            {
                "guid": "c-row-1",
                "patient_guid": "pat-a",
                "grantee_caregiver_guid": "org-A",
                "contract_guid": "c-1",
                "is_active": True,
            },
            {
                # Unrelated consent — must NOT be revoked.
                "guid": "c-row-2",
                "patient_guid": "pat-a",
                "grantee_caregiver_guid": "org-X",
                "contract_guid": "different-contract",
                "is_active": True,
            },
        ]
        with app.app_context():
            summary = revoke_patient_consents(
                _signed_contract(status="cancelled"),
                reason="contract_status:cancelled",
            )
        assert summary["revoked"] == 1
        assert len(mock_ips.revokes) == 1
        patient, consent, body = mock_ips.revokes[0]
        assert patient == "pat-a"
        assert consent == "c-row-1"
        assert body["reason"] == "contract_status:cancelled"

    def test_no_match_is_noop(self, app, mock_ips):
        from app.consent_emitter import revoke_patient_consents
        mock_ips.active_consents = [
            {
                "guid": "c-row-X",
                "patient_guid": "pat-a",
                "grantee_caregiver_guid": "org-A",
                "contract_guid": "different-contract",
                "is_active": True,
            },
        ]
        with app.app_context():
            summary = revoke_patient_consents(
                _signed_contract(), reason="x",
            )
        assert summary["revoked"] == 0


# ---------------------------------------------------------------------------
# Route-level wiring
# ---------------------------------------------------------------------------

class TestRouteIntegration:
    def test_create_executed_contract_emits_consent(
        self, client, admin_token, mock_ips,
    ):
        contract = _signed_contract(contract_id="route-c-1")
        r = client.post(
            "/fhir/Contract", json=contract, headers=auth(admin_token),
        )
        assert r.status_code == 201, r.json
        # One patient, one provider -> exactly one grant.
        assert len(mock_ips.grants) == 1
        assert mock_ips.grants[0][1]["contract_guid"] == "route-c-1"

    def test_update_to_cancelled_revokes_consents(
        self, client, admin_token, mock_ips,
    ):
        # Step 1 — create signed.
        contract = _signed_contract(contract_id="route-c-2")
        r = client.post(
            "/fhir/Contract", json=contract, headers=auth(admin_token),
        )
        assert r.status_code == 201
        assert len(mock_ips.grants) == 1

        # Step 2 — pretend that consent is now active in IPS.
        mock_ips.active_consents = [
            {
                "guid": "issued-1",
                "patient_guid": "pat-a",
                "grantee_caregiver_guid": "org-A",
                "contract_guid": "route-c-2",
                "is_active": True,
            },
        ]

        # Step 3 — flip to cancelled.
        cancelled = {**contract, "status": "cancelled"}
        r = client.put(
            "/fhir/Contract/route-c-2",
            json=cancelled,
            headers=auth(admin_token),
        )
        assert r.status_code == 200
        # Revoke fired exactly once for the matching consent.
        assert len(mock_ips.revokes) == 1
        patient, consent, body = mock_ips.revokes[0]
        assert patient == "pat-a"
        assert consent == "issued-1"
        assert body["reason"] == "contract_status:cancelled"

    def test_delete_revokes_consents(
        self, client, admin_token, mock_ips,
    ):
        contract = _signed_contract(contract_id="route-c-3")
        r = client.post(
            "/fhir/Contract", json=contract, headers=auth(admin_token),
        )
        assert r.status_code == 201
        # Active consent is now in IPS.
        mock_ips.active_consents = [
            {
                "guid": "issued-2",
                "patient_guid": "pat-a",
                "grantee_caregiver_guid": "org-A",
                "contract_guid": "route-c-3",
                "is_active": True,
            },
        ]
        r = client.delete(
            "/fhir/Contract/route-c-3", headers=auth(admin_token),
        )
        assert r.status_code == 204
        assert len(mock_ips.revokes) == 1
        assert mock_ips.revokes[0][1] == "issued-2"

    def test_resign_same_contract_is_idempotent(
        self, client, admin_token, mock_ips,
    ):
        contract = _signed_contract(contract_id="route-c-4")
        # First sign creates the consent.
        r = client.post(
            "/fhir/Contract", json=contract, headers=auth(admin_token),
        )
        assert r.status_code == 201
        assert len(mock_ips.grants) == 1

        # IPS now holds the consent.
        mock_ips.active_consents = [
            {
                "guid": "issued-3",
                "patient_guid": "pat-a",
                "grantee_caregiver_guid": "org-A",
                "contract_guid": "route-c-4",
                "is_active": True,
            },
        ]

        # Re-PUT the same contract (same id, same status). Emitter
        # should observe the existing consent and skip the POST.
        r = client.put(
            "/fhir/Contract/route-c-4",
            json=contract,
            headers=auth(admin_token),
        )
        assert r.status_code == 200
        assert len(mock_ips.grants) == 1  # unchanged
