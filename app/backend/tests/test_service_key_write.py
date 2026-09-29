"""#706 — a sibling service may write a Contract with X-Service-Key.

A service cannot obtain a contract.pdhc JWT at all: the only issuer is
`/api/v1/auth/callback`, a browser redirect flow with CSRF state in a
session. onboard.pdhc negotiates an agreement and then has to write it, and
it had nowhere to authenticate — `require_service_key` and
`INTERNAL_SERVICE_KEY` both already existed here and were applied to nothing.

The key is an ALTERNATIVE to the JWT on routes that opt in. These tests pin
that it does not become a replacement, and does not widen anything else.
"""
from __future__ import annotations

import os
import uuid

import pytest

KEY = "test-service-key-12345"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    monkeypatch.setenv("JWT_SECRET_KEY", "test")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", KEY)
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("STRICT_SCOPE_CONCEPTS", "false")
    monkeypatch.setenv("STRICT_SIGNER_VALIDATION", "false")
    monkeypatch.setenv("IPS_BASE_URL", "")


@pytest.fixture()
def client():
    from app.main import create_app
    return create_app().test_client()


def _contract():
    return {
        "resourceType": "Contract",
        "id": str(uuid.uuid4()),
        "status": "offered",
        "term": [],
    }


class TestTheServiceKeyPathWorks:

    def test_a_valid_key_may_create_a_contract(self, client):
        r = client.post("/fhir/Contract", json=_contract(),
                        headers={"X-Service-Key": KEY})
        assert r.status_code in (200, 201), r.data[:300]

    def test_without_any_credential_it_is_still_refused(self, client):
        r = client.post("/fhir/Contract", json=_contract())
        assert r.status_code == 401

    def test_a_wrong_key_is_refused(self, client):
        r = client.post("/fhir/Contract", json=_contract(),
                        headers={"X-Service-Key": "not-the-key"})
        assert r.status_code == 401


class TestItDoesNotWidenAnythingElse:

    def test_the_key_does_not_open_routes_that_did_not_opt_in(self, client):
        """require_role gained an opt-in flag, not a global back door. A
        route that did not ask for it must still refuse the key."""
        r = client.delete(f"/fhir/Contract/{uuid.uuid4()}",
                          headers={"X-Service-Key": KEY})
        assert r.status_code in (401, 404, 405), r.status_code
        assert r.status_code != 200

    def test_an_unconfigured_key_refuses_rather_than_admits(self, client,
                                                            monkeypatch):
        """If INTERNAL_SERVICE_KEY is empty, an empty header must not match
        it — the classic ''=='' hole."""
        monkeypatch.setenv("INTERNAL_SERVICE_KEY", "")
        from app.main import create_app
        c = create_app().test_client()
        r = c.post("/fhir/Contract", json=_contract(),
                   headers={"X-Service-Key": ""})
        assert r.status_code == 401
