"""Tests for the consent reconciler — ticket #243.

The reconciler walks ContractRecord rows and re-calls the existing
emit / revoke helpers (which are already idempotent at the IPS side).
We mock the IPS HTTP boundary the same way test_consent_emitter does
so the tests own all server replies and we can assert on the
reconciler's accounting.

Coverage:
  - empty DB → all-zero summary
  - one grant-status contract, IPS already has matching consent →
    grants_re_emitted == 0 (idempotency at work)
  - one grant-status contract, IPS does NOT have the consent →
    grants_re_emitted == 1 (the recovery case)
  - one revoke-status contract whose consent is still active on IPS →
    revokes_re_called == 1
  - one revoke-status contract whose consent already revoked →
    revokes_re_called == 0
  - non-lifecycle status (draft) → skipped (not counted in `checked`)
  - poison row (emitter raises) → errors += 1, sweep keeps going
  - second run within the same window → all zero (idempotency soak)
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Env (mirrors test_consent_emitter.py)
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
    monkeypatch.setenv("FLASK_ENV", "development")  # #350 §5.1 guard
    monkeypatch.setenv("IPS_BASE_URL", "http://ips.test")
    monkeypatch.setenv("IPS_API_KEY", "test-ips-key")
    monkeypatch.setenv("STRICT_SCOPE_CONCEPTS", "false")
    monkeypatch.setenv("STRICT_SIGNER_VALIDATION", "false")


@pytest.fixture()
def app():
    from app.main import create_app
    return create_app()


@pytest.fixture()
def db_session(app):
    """Yield a fresh SQLAlchemy session inside the app context."""
    from app.main import app as global_app  # noqa: F401  (ensures init)
    # The app's session factory is set up by create_app; use it directly.
    from app.db import make_engine, make_session_factory
    from app.models import Base

    engine = make_engine(app.config["DATABASE_URL"])
    Base.metadata.create_all(engine)
    SessionLocal = make_session_factory(engine)
    with app.app_context():
        with SessionLocal() as s:
            yield s


# ---------------------------------------------------------------------------
# IPS HTTP mock (same shape as test_consent_emitter)
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_ips():
    state = SimpleNamespace(
        active_consents_by_patient={},  # patient_guid -> [consent dicts]
        grants=[],
        revokes=[],
        grant_status=201,
        revoke_status=200,
    )

    def fake_get(url, params=None, headers=None, timeout=None):
        # GET /api/v1/patients/<guid>/consents?active=true
        patient_guid = url.split("/api/v1/patients/", 1)[1].split("/", 1)[0]
        items = list(state.active_consents_by_patient.get(patient_guid, []))
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"items": items, "total": len(items)}
        return resp

    def fake_post(url, json=None, headers=None, timeout=None):
        if "/revoke" in url:
            parts = url.split("/api/v1/patients/", 1)[1].split("/")
            patient_guid = parts[0]
            consent_guid = parts[2]
            state.revokes.append((patient_guid, consent_guid, json))
            resp = MagicMock(status_code=state.revoke_status)
            resp.json.return_value = {"revoked": True}
            resp.text = ""
            return resp
        patient_guid = url.split(
            "/api/v1/patients/", 1,
        )[1].split("/", 1)[0]
        state.grants.append((patient_guid, json))
        resp = MagicMock(status_code=state.grant_status)
        resp.json.return_value = {"guid": "new-consent"}
        resp.text = ""
        return resp

    with patch("app.consent_emitter.http_requests") as m:
        m.get.side_effect = fake_get
        m.post.side_effect = fake_post
        m.RequestException = Exception
        yield state


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_contract(*, guid, status, patient_guids=("pat-a",),
                   provider_orgs=("org-x",), expires=None):
    """A minimal FHIR Contract dict shaped like the emitter expects."""
    party = [{
        "role": [{"coding": [{"code": "provider"}]}],
        "reference": [{"reference": f"Organization/{o}"} for o in provider_orgs],
    }]
    signer = [
        {"party": {"reference": f"Patient/{p}"}} for p in patient_guids
    ]
    contract = {
        "resourceType": "Contract",
        "id": guid,
        "status": status,
        "party": party,
        "signer": signer,
    }
    if expires:
        contract["period"] = {"end": expires}
    return contract


def _insert_contract(session, contract):
    from app.models import ContractRecord
    row = ContractRecord(guid=contract["id"], fhir_contract=contract)
    session.add(row)
    session.commit()
    return row


# ---------------------------------------------------------------------------
# Empty DB
# ---------------------------------------------------------------------------

def test_empty_db_is_noop(db_session, mock_ips):
    from app.consent_reconciler import reconcile
    summary = reconcile(db_session)
    assert summary == {
        "checked": 0, "grants_re_emitted": 0, "revokes_re_called": 0,
        "grant_attempts": 0, "revoke_attempts": 0, "errors": 0,
    }
    assert mock_ips.grants == []
    assert mock_ips.revokes == []


# ---------------------------------------------------------------------------
# Grant-side recovery
# ---------------------------------------------------------------------------

def test_grant_already_present_is_noop(db_session, mock_ips):
    """IPS already has the matching PatientConsent; reconciler does
    not re-post. This is the steady-state happy path."""
    from app.consent_reconciler import reconcile

    contract = _make_contract(guid="contract-001", status="executed")
    _insert_contract(db_session, contract)

    # IPS already holds the matching consent.
    mock_ips.active_consents_by_patient["pat-a"] = [
        {
            "guid": "existing-consent-1",
            "contract_guid": "contract-001",
            "grantee_caregiver_guid": "org-x",
        },
    ]

    summary = reconcile(db_session)
    assert summary["checked"] == 1
    assert summary["grants_re_emitted"] == 0
    assert summary["grant_attempts"] == 1  # attempted = (pat, grantee) pairs walked
    assert mock_ips.grants == []  # no POST happened


def test_grant_missing_is_recovered(db_session, mock_ips):
    """IPS has NO matching consent (the original emission silently
    dropped); reconciler re-emits. This is the failure-recovery case
    the ticket exists for."""
    from app.consent_reconciler import reconcile

    contract = _make_contract(guid="contract-002", status="executed")
    _insert_contract(db_session, contract)

    # IPS has nothing for this patient.
    # mock_ips.active_consents_by_patient stays empty.

    summary = reconcile(db_session)
    assert summary["checked"] == 1
    assert summary["grants_re_emitted"] == 1
    assert summary["errors"] == 0
    # Verify the grant POST hit IPS with the right contract linkback.
    assert len(mock_ips.grants) == 1
    _, body = mock_ips.grants[0]
    assert body["contract_guid"] == "contract-002"
    assert body["granted_via"] == "contract"


def test_multiple_grant_statuses_each_handled(db_session, mock_ips):
    """All four grant-statuses (executed/executable/offered/renewed)
    are walked."""
    from app.consent_reconciler import reconcile

    for i, status in enumerate(
        ("executed", "executable", "offered", "renewed"), start=1
    ):
        _insert_contract(
            db_session,
            _make_contract(
                guid=f"c-{i}", status=status,
                patient_guids=(f"pat-{i}",),
            ),
        )

    summary = reconcile(db_session)
    assert summary["checked"] == 4
    assert summary["grants_re_emitted"] == 4


# ---------------------------------------------------------------------------
# Revoke-side recovery
# ---------------------------------------------------------------------------

def test_revoke_still_active_is_recovered(db_session, mock_ips):
    """Contract is in a revoke-status, but IPS still holds an active
    consent linked to it — reconciler re-calls revoke."""
    from app.consent_reconciler import reconcile

    contract = _make_contract(guid="contract-r1", status="cancelled")
    _insert_contract(db_session, contract)

    mock_ips.active_consents_by_patient["pat-a"] = [
        {
            "guid": "stale-consent",
            "contract_guid": "contract-r1",
            "grantee_caregiver_guid": "org-x",
        },
    ]

    summary = reconcile(db_session)
    assert summary["checked"] == 1
    assert summary["revokes_re_called"] == 1
    assert mock_ips.revokes
    _, consent_guid, body = mock_ips.revokes[0]
    assert consent_guid == "stale-consent"
    assert "reconcile" in body["reason"]


def test_revoke_already_done_is_noop(db_session, mock_ips):
    """Contract is in revoke-status and IPS no longer holds any active
    consent for this contract — reconciler does nothing."""
    from app.consent_reconciler import reconcile

    contract = _make_contract(guid="contract-r2", status="terminated")
    _insert_contract(db_session, contract)

    # IPS has no active consents for this patient.

    summary = reconcile(db_session)
    assert summary["checked"] == 1
    assert summary["revokes_re_called"] == 0
    assert mock_ips.revokes == []


def test_revoke_ignores_unrelated_consents(db_session, mock_ips):
    """A revoke-status contract must not touch consents that link to a
    DIFFERENT contract — that would be a cross-contract data-loss bug.
    """
    from app.consent_reconciler import reconcile

    contract = _make_contract(guid="contract-r3", status="cancelled")
    _insert_contract(db_session, contract)

    # IPS holds a consent for the patient but it belongs to a DIFFERENT
    # contract — reconciler must not touch it.
    mock_ips.active_consents_by_patient["pat-a"] = [
        {
            "guid": "unrelated-consent",
            "contract_guid": "some-other-contract",
            "grantee_caregiver_guid": "org-x",
        },
    ]

    summary = reconcile(db_session)
    assert summary["revokes_re_called"] == 0
    assert mock_ips.revokes == []


# ---------------------------------------------------------------------------
# Status skipping
# ---------------------------------------------------------------------------

def test_non_lifecycle_status_is_skipped(db_session, mock_ips):
    """Drafts / proposals are not walked at all."""
    from app.consent_reconciler import reconcile

    _insert_contract(
        db_session,
        _make_contract(guid="draft-1", status="draft"),
    )
    _insert_contract(
        db_session,
        _make_contract(guid="proposed-1", status="proposed"),
    )

    summary = reconcile(db_session)
    assert summary["checked"] == 0
    assert summary["grants_re_emitted"] == 0
    assert mock_ips.grants == []


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

def test_poison_row_does_not_stall_sweep(db_session, mock_ips):
    """One contract raises; the next contract still runs. errors += 1."""
    from app.consent_reconciler import reconcile

    _insert_contract(
        db_session,
        _make_contract(guid="good-1", status="executed"),
    )
    _insert_contract(
        db_session,
        _make_contract(guid="poison", status="executed"),
    )
    _insert_contract(
        db_session,
        _make_contract(guid="good-2", status="executed",
                       patient_guids=("pat-b",)),
    )

    # Patch emit_patient_consents to raise on the poison id.
    real_emit = None
    from app import consent_reconciler as cr_mod
    real_emit = cr_mod.emit_patient_consents

    def buggy_emit(resource):
        if resource.get("id") == "poison":
            raise RuntimeError("boom")
        return real_emit(resource)

    with patch.object(cr_mod, "emit_patient_consents", buggy_emit):
        summary = reconcile(db_session)

    assert summary["checked"] == 3
    assert summary["errors"] == 1
    # The two good contracts each emitted one grant.
    assert summary["grants_re_emitted"] == 2


# ---------------------------------------------------------------------------
# Idempotency soak — second run is ~zero work
# ---------------------------------------------------------------------------

def test_second_run_is_idempotent_soak(db_session, mock_ips):
    """Run the reconciler twice with no changes between; the second
    run must do approximately zero work. Models the operator running
    the cron and finding the same outcome."""
    from app.consent_reconciler import reconcile

    _insert_contract(
        db_session,
        _make_contract(guid="contract-soak", status="executed"),
    )

    # First run: posts (because IPS is empty).
    first = reconcile(db_session)
    assert first["grants_re_emitted"] == 1

    # Simulate the consent now being persisted on IPS.
    mock_ips.active_consents_by_patient["pat-a"] = [
        {
            "guid": "now-persisted",
            "contract_guid": "contract-soak",
            "grantee_caregiver_guid": "org-x",
        },
    ]

    # Second run: should be a noop.
    second = reconcile(db_session)
    assert second["grants_re_emitted"] == 0
    assert second["checked"] == 1


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------

def test_cli_command_registered(app):
    """`flask reconcile-consents` is wired up by create_app."""
    runner = app.test_cli_runner()
    result = runner.invoke(args=["reconcile-consents"])
    assert result.exit_code == 0, result.output
    assert "reconcile-consents" in result.output
    assert "checked=" in result.output
    assert "grants_re_emitted=" in result.output
