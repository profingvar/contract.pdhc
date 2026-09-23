"""Ticket #599 — the contract.pdhc enablers for onboard.pdhc OB-7.

Four items:
  1. server-side ?party= and ?status= on GET /fhir/Contract
  2. an `onboarding_terms` term[] that stores the non-scope agreements
     verbatim and is ignored by every scope reader
  3. `X-Skip-Auto-Provision: 1` on POST /fhir/Contract (OB-13 decision 1c)
  4. the accepted signer party types, documented in signer_resolver
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest


PAYER = str(uuid.uuid4())
PROVIDER = str(uuid.uuid4())
OTHER_ORG = str(uuid.uuid4())
CONCEPT_URL = f"https://plan.pdhc.se/api/v1/concepts/{uuid.uuid4()}"

SERVICE_KEY = "test-service-key-12345"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test")
    monkeypatch.setenv("DATABASE_URL", os.getenv("TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:"))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("STRICT_SCOPE_CONCEPTS", "false")


@pytest.fixture()
def client():
    from app.main import create_app
    return create_app().test_client()


@pytest.fixture()
def admin_token(client):
    r = client.post("/auth/login", json={"username": "admin", "password": "password"})
    assert r.status_code == 200
    return r.json["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _contract(*, status="executed", payer=PAYER, provider=PROVIDER, terms=None,
              cid=None):
    return {
        "resourceType": "Contract",
        "id": cid or str(uuid.uuid4()),
        "status": status,
        "party": [
            {
                "reference": [{"reference": f"Organization/{payer}"}],
                "role": [{"coding": [{"code": "payer"}]}],
            },
            {
                "reference": [{"reference": f"Organization/{provider}"}],
                "role": [{"coding": [{"code": "provider"}]}],
            },
        ],
        "term": terms if terms is not None else [
            {
                "type": {"text": "request_scope"},
                "offer": {"text": "Permitted concepts"},
                "asset": [{
                    "type": [{"text": "outbound_concept"}],
                    "typeReference": [{"reference": CONCEPT_URL}],
                }],
            },
        ],
    }


ONBOARDING_TERM = {
    "type": {"text": "onboarding_terms"},
    "offer": {"text": "Delivery by push to the provider's inbox; go live 2026-10-01."},
    "asset": [
        {"type": [{"text": "delivery_mode"}], "valueString": "push"},
        {"type": [{"text": "webhook_url"}],
         "valueString": "https://provider.example.se/pdhc/inbox"},
        {"type": [{"text": "ops_contact"}], "valueString": "drift@provider.example.se"},
    ],
    "valuedItem": [{"effectiveTime": "2026-10-01"}],
}


def _post(client, token, resource, headers=None):
    h = auth(token)
    h.update(headers or {})
    return client.post("/fhir/Contract", json=resource, headers=h)


# ── item 2: onboarding_terms ──────────────────────────────────────────

class TestOnboardingTerms:

    def test_onboarding_term_is_accepted(self, client, admin_token):
        r = _post(client, admin_token,
                  _contract(terms=[_contract()["term"][0], ONBOARDING_TERM]))
        assert r.status_code == 201, r.json

    def test_stored_verbatim(self, client, admin_token):
        r = _post(client, admin_token,
                  _contract(terms=[_contract()["term"][0], ONBOARDING_TERM]))
        guid = r.json["id"]
        got = client.get(f"/fhir/Contract/{guid}").json
        stored = [t for t in got["term"]
                  if t["type"]["text"] == "onboarding_terms"][0]
        assert stored == ONBOARDING_TERM

    def test_contributes_no_concept_scope(self, client, admin_token):
        """The whole point: it rides along without widening what the
        provider may be asked for or must return."""
        with_ob = _post(client, admin_token,
                        _contract(terms=[_contract()["term"][0], ONBOARDING_TERM]))
        scope = client.get(f"/fhir/Contract/{with_ob.json['id']}/scope").json
        assert len(scope["request_scope"]) == 1   # the one real concept, no more

    def test_onboarding_term_alone_needs_no_assets(self, client, admin_token):
        """Scope terms require a non-empty asset[]; onboarding terms do not —
        a bare agreement with only offer.text is valid."""
        bare = {"type": {"text": "onboarding_terms"},
                "offer": {"text": "Agreed on the call, 2026-09-23."}}
        r = _post(client, admin_token, _contract(terms=[bare]))
        assert r.status_code == 201, r.json

    def test_non_concept_references_are_allowed(self, client, admin_token):
        """A scope term would reject this — its typeReference must be a
        concept URL. An onboarding term references webhooks and people."""
        r = _post(client, admin_token, _contract(terms=[ONBOARDING_TERM]))
        assert r.status_code == 201, r.json

    def test_scope_terms_are_still_strict(self, client, admin_token):
        """The loosening must not leak into the security boundary."""
        bad = {"type": {"text": "request_scope"},
               "asset": [{"type": [{"text": "outbound_concept"}],
                          "typeReference": [{"reference": "https://example.com/not-a-concept"}]}]}
        r = _post(client, admin_token, _contract(terms=[bad]))
        assert r.status_code == 400

    def test_unknown_term_type_still_rejected(self, client, admin_token):
        r = _post(client, admin_token,
                  _contract(terms=[{"type": {"text": "whatever_terms"},
                                    "asset": []}]))
        assert r.status_code == 400

    def test_onboarding_assets_are_not_sent_to_plan_for_verification(self):
        """extract_scope_concept_guids must skip the onboarding term, or
        plan.pdhc would be asked to verify a webhook URL as a concept."""
        from app.scope_validation import extract_scope_concept_guids
        guids = extract_scope_concept_guids(
            _contract(terms=[_contract()["term"][0], ONBOARDING_TERM])
        )
        assert len(guids) == 1


# ── item 1: party + status filters ────────────────────────────────────

class TestPartyAndStatusFilters:

    def _seed(self, client, token):
        for kw in (
            dict(payer=PAYER, provider=PROVIDER),
            dict(payer=PAYER, provider=OTHER_ORG, status="offered"),
            dict(payer=OTHER_ORG, provider=OTHER_ORG),
        ):
            r = _post(client, token, _contract(**kw))
            assert r.status_code == 201, (kw, r.status_code, r.json)

    def test_unfiltered_returns_all(self, client, admin_token):
        self._seed(client, admin_token)
        b = client.get("/fhir/Contract").json
        assert b["total"] == 3
        assert b["type"] == "searchset"

    def test_filter_by_party_full_reference(self, client, admin_token):
        self._seed(client, admin_token)
        b = client.get(f"/fhir/Contract?party=Organization/{PROVIDER}").json
        assert b["total"] == 1
        assert len(b["entry"]) == 1

    def test_filter_by_bare_guid(self, client, admin_token):
        self._seed(client, admin_token)
        b = client.get(f"/fhir/Contract?party={PROVIDER}").json
        assert b["total"] == 1

    def test_party_matches_either_role(self, client, admin_token):
        self._seed(client, admin_token)
        b = client.get(f"/fhir/Contract?party=Organization/{PAYER}").json
        assert b["total"] == 2      # payer on two of the three

    def test_filter_by_status(self, client, admin_token):
        self._seed(client, admin_token)
        b = client.get("/fhir/Contract?status=offered").json
        assert b["total"] == 1

    def test_party_and_status_combine(self, client, admin_token):
        self._seed(client, admin_token)
        b = client.get(
            f"/fhir/Contract?party=Organization/{PAYER}&status=offered"
        ).json
        assert b["total"] == 1

    def test_unknown_party_returns_empty_searchset(self, client, admin_token):
        self._seed(client, admin_token)
        b = client.get(f"/fhir/Contract?party=Organization/{uuid.uuid4()}").json
        assert b["total"] == 0
        assert b["entry"] == []

    def test_total_reflects_the_filter_not_the_table(self, client, admin_token):
        """Regression guard: filtering after paging would report the
        unfiltered total and page the wrong window."""
        self._seed(client, admin_token)
        b = client.get(
            f"/fhir/Contract?party=Organization/{PROVIDER}&_count=1"
        ).json
        assert b["total"] == 1
        assert len(b["entry"]) == 1

    def test_paging_still_applies_to_a_filtered_set(self, client, admin_token):
        self._seed(client, admin_token)
        page = client.get(
            f"/fhir/Contract?party=Organization/{PAYER}&_count=1&_offset=0"
        ).json
        nxt = client.get(
            f"/fhir/Contract?party=Organization/{PAYER}&_count=1&_offset=1"
        ).json
        assert page["total"] == nxt["total"] == 2
        assert len(page["entry"]) == len(nxt["entry"]) == 1
        assert page["entry"][0]["resource"]["id"] != nxt["entry"][0]["resource"]["id"]


# ── item 3: X-Skip-Auto-Provision ─────────────────────────────────────

class TestSkipAutoProvision:

    def test_auto_provision_runs_by_default(self, client, admin_token):
        from app import main as main_mod
        with patch.object(main_mod, "http_requests") as req:
            r = _post(client, admin_token, _contract())
            assert r.status_code == 201
            assert req.post.called

    def test_header_skips_provisioning(self, client, admin_token):
        """With the header, request.pdhc is never called — so onboard.pdhc's
        own PAT is the only one that exists (OB-13 decision 1c)."""
        from app import main as main_mod
        with patch.object(main_mod, "http_requests") as req:
            r = _post(client, admin_token, _contract(),
                      headers={"X-Skip-Auto-Provision": "1"})
            assert r.status_code == 201
            assert not req.post.called

    def test_header_accepts_true_and_yes(self, client, admin_token):
        from app import main as main_mod
        for val in ("true", "yes", "TRUE"):
            with patch.object(main_mod, "http_requests") as req:
                r = _post(client, admin_token, _contract(),
                          headers={"X-Skip-Auto-Provision": val})
                assert r.status_code == 201
                assert not req.post.called

    def test_other_values_do_not_skip(self, client, admin_token):
        from app import main as main_mod
        with patch.object(main_mod, "http_requests") as req:
            r = _post(client, admin_token, _contract(),
                      headers={"X-Skip-Auto-Provision": "0"})
            assert r.status_code == 201
            assert req.post.called


# ── item 4: documented signer contract ────────────────────────────────

class TestSignerPartyTypes:

    def test_organization_signer_is_accepted_for_both_parties(self):
        """OB-7 signs as Organization/<payer> and Organization/<provider>.
        Confirms that works today — accepted on shape alone."""
        from app.signer_resolver import verify_signer_references
        resource = {"signer": [
            {"party": [{"reference": f"Organization/{PAYER}"}]},
            {"party": [{"reference": f"Organization/{PROVIDER}"}]},
        ]}
        assert verify_signer_references(resource, session=None) == []

    def test_accepted_party_types_are_documented(self):
        """#599 item 4 is a documentation deliverable — keep it honest."""
        from app import signer_resolver
        doc = signer_resolver.__doc__
        for token in ("Patient/", "Practitioner/", "User/", "Organization/"):
            assert token in doc
        assert "SHAPE ALONE" in doc.upper()
