"""Rollup #350 §4.2 — bidirectional CapabilityStatement truth test.

Rule 20 requires that every advertised endpoint exists AND every real
FHIR endpoint is advertised. The pre-#350 `test_capability_statement_structure`
in test_health_and_contracts.py checks the shape of the CS but does
NOT walk `app.url_map` back against the CS to catch drift in either
direction. The `/fhir/Contract/<guid>/scope` gap flagged by the
2026-07-02 audit (§2.1) would have been caught by this test — it's a
real endpoint that the CS didn't advertise until #350 fixed it.

Two directions:

  (a) Every resource-level interaction + operation the CS advertises
      must map to a real Flask route.

  (b) Every /fhir/Contract* route in app.url_map must be advertised
      via an interaction, an operation, or a documented searchParam.

Uses the same "METHOD /path" convention on the first line of an
operation's `documentation` field as request.pdhc's #372 truth test.
"""
from __future__ import annotations

import os
import re

import pytest


_DEF_RE = re.compile(r"^(GET|POST|PUT|DELETE|PATCH)\s+(/\S+)")


_INTERACTION_TO_ROUTE = {
    "read":         ("GET",    "/{id}"),
    "search-type":  ("GET",    ""),
    "create":       ("POST",   ""),
    "update":       ("PUT",    "/{id}"),
    "delete":       ("DELETE", "/{id}"),
}


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", "test-service-key-12345")
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("FLASK_ENV", "development")
    # These env vars would otherwise be captured at class-level in
    # app.config.Config the first time Config is imported. When this
    # truth test runs alphabetically BEFORE test_consent_emitter and
    # imports create_app first, Config snapshots empty values for
    # IPS_BASE_URL and STRICT_SIGNER_VALIDATION, which then breaks
    # sibling tests that expect their per-fixture env to take effect.
    # Set the same values sibling tests expect.
    monkeypatch.setenv("IPS_BASE_URL", "http://ips.test")
    monkeypatch.setenv("STRICT_SIGNER_VALIDATION", "false")
    monkeypatch.setenv("STRICT_SCOPE_CONCEPTS", "false")


@pytest.fixture()
def app():
    from app.main import create_app
    return create_app()


@pytest.fixture()
def client(app):
    return app.test_client()


def _shape(path: str) -> str:
    p = re.sub(r"<[^>]+>", "<*>", path)
    p = re.sub(r"\{[^}]+\}", "<*>", p)
    return p


def _parse_first_line(text: str) -> tuple[str, str] | None:
    if not text:
        return None
    first = text.strip().splitlines()[0].strip()
    m = _DEF_RE.match(first)
    if not m:
        return None
    return m.group(1), _shape(m.group(2).split("?", 1)[0])


def _resource_shape(resource_type: str, interaction_code: str) -> tuple[str, str] | None:
    mapping = _INTERACTION_TO_ROUTE.get(interaction_code)
    if not mapping:
        return None
    method, suffix = mapping
    # Route through _shape() so `{id}` becomes `<*>` — matching the
    # form _url_map_shapes emits — instead of `{*}`, which wouldn't
    # match Flask's `<guid>` rules.
    path = _shape(f"/fhir/{resource_type}{suffix}")
    return method, path


def _url_map_shapes(app) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for rule in app.url_map.iter_rules():
        methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}
        for m in methods:
            out.add((m, _shape(rule.rule)))
    return out


def test_metadata_endpoint_is_reachable(client):
    r = client.get("/fhir/metadata")
    assert r.status_code == 200
    body = r.get_json()
    assert body["resourceType"] == "CapabilityStatement"


def test_every_advertised_interaction_and_operation_resolves(app, client):
    """Direction (a): every advertised interaction or operation maps
    to a real Flask route."""
    cs = client.get("/fhir/metadata").get_json()
    url_map = _url_map_shapes(app)

    missing = []
    for res in cs["rest"][0].get("resource", []) or []:
        rtype = res["type"]
        for interaction in res.get("interaction", []) or []:
            expected = _resource_shape(rtype, interaction.get("code"))
            if expected is None:
                continue
            if expected not in url_map:
                missing.append(f"{rtype}.{interaction['code']} → {expected[0]} {expected[1]}")
        for op in res.get("operation", []) or []:
            parsed = _parse_first_line(op.get("documentation", ""))
            if parsed is None:
                continue
            if parsed not in url_map:
                missing.append(
                    f"{rtype}.${op.get('name','?')} → {parsed[0]} {parsed[1]}"
                )

    assert not missing, (
        "CapabilityStatement advertises "
        f"{len(missing)} endpoint(s) not present in app.url_map:\n  "
        + "\n  ".join(missing)
    )


def test_scope_endpoint_is_advertised(client):
    """Regression guard for the #350 §2.1 gap. The /scope operation
    was live for months but not advertised until #350."""
    cs = client.get("/fhir/metadata").get_json()
    contract = next(
        (r for r in cs["rest"][0]["resource"] if r["type"] == "Contract"),
        None,
    )
    assert contract is not None, "Contract resource missing from CS"
    op_names = {op.get("name") for op in contract.get("operation", []) or []}
    assert "scope" in op_names, (
        f"$scope operation not advertised — {op_names}"
    )


def test_every_fhir_contract_route_is_advertised(app, client):
    """Direction (b): every /fhir/Contract* route in url_map must be
    covered by an interaction, an operation, or a searchParam."""
    cs = client.get("/fhir/metadata").get_json()
    contract_block = next(
        (r for r in cs["rest"][0]["resource"] if r["type"] == "Contract"),
        None,
    )
    assert contract_block is not None

    covered: set[tuple[str, str]] = set()
    for interaction in contract_block.get("interaction", []) or []:
        expected = _resource_shape("Contract", interaction.get("code"))
        if expected:
            covered.add(expected)
    for op in contract_block.get("operation", []) or []:
        parsed = _parse_first_line(op.get("documentation", ""))
        if parsed:
            covered.add(parsed)

    live_contract_routes = {
        (m, p) for (m, p) in _url_map_shapes(app)
        if p.startswith("/fhir/Contract")
    }

    unadvertised = live_contract_routes - covered
    assert not unadvertised, (
        f"{len(unadvertised)} /fhir/Contract* route(s) live in "
        f"app.url_map but not advertised in CapabilityStatement:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in sorted(unadvertised))
    )
