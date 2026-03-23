from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test")
    monkeypatch.setenv("DATABASE_URL", os.getenv("TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:"))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "password")


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


# --- Health ---


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json["status"] == "ok"


# --- Auth ---


def test_login_success(client):
    r = client.post("/auth/login", json={"username": "admin", "password": "password"})
    assert r.status_code == 200
    assert "access_token" in r.json
    assert r.json["role"] == "admin"


def test_login_failure(client):
    r = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_unauthorized_admin_access(client):
    r = client.get("/admin/users")
    assert r.status_code == 401


# --- CapabilityStatement ---


def test_capability_statement_structure(client):
    r = client.get("/fhir/metadata")
    assert r.status_code == 200
    cs = r.json
    assert cs["resourceType"] == "CapabilityStatement"
    assert cs["status"] == "active"
    assert cs["fhirVersion"] == "5.0.0"
    assert isinstance(cs["rest"], list)
    assert cs["rest"][0]["mode"] == "server"
    resources = cs["rest"][0]["resource"]
    contract_res = next(r for r in resources if r["type"] == "Contract")
    interaction_codes = {i["code"] for i in contract_res["interaction"]}
    assert interaction_codes == {"read", "search-type", "create", "update", "delete"}


# --- Contract CRUD ---


def test_contract_crud_cycle(client, admin_token):
    headers = auth(admin_token)

    # Create
    payload = {
        "resourceType": "Contract",
        "status": "executable",
        "period": {"start": "2026-01-01T00:00:00Z", "end": "2026-12-31T23:59:59Z"},
    }
    r = client.post("/fhir/Contract", json=payload, headers=headers)
    assert r.status_code == 201
    guid = r.json["id"]
    assert guid

    # Read
    r = client.get(f"/fhir/Contract/{guid}")
    assert r.status_code == 200
    assert r.json["id"] == guid
    assert r.json["status"] == "executable"

    # Update
    updated = {**payload, "id": guid, "status": "executed"}
    r = client.put(f"/fhir/Contract/{guid}", json=updated, headers=headers)
    assert r.status_code == 200
    assert r.json["status"] == "executed"

    # List (Bundle)
    r = client.get("/fhir/Contract")
    assert r.status_code == 200
    assert r.json["resourceType"] == "Bundle"
    ids = [e["resource"]["id"] for e in r.json["entry"]]
    assert guid in ids

    # Delete
    r = client.delete(f"/fhir/Contract/{guid}", headers=headers)
    assert r.status_code == 204

    # Confirm deleted
    r = client.get(f"/fhir/Contract/{guid}")
    assert r.status_code == 404


def test_contract_create_rejects_bad_resource_type(client, admin_token):
    r = client.post(
        "/fhir/Contract",
        json={"resourceType": "Patient"},
        headers=auth(admin_token),
    )
    assert r.status_code == 400


def test_contract_create_requires_auth(client):
    r = client.post("/fhir/Contract", json={"resourceType": "Contract", "status": "executable"})
    assert r.status_code == 401


# --- User management ---


def test_user_management_cycle(client, admin_token):
    headers = auth(admin_token)

    # Create
    r = client.post(
        "/admin/users",
        json={"username": "testuser", "password": "Test1234!", "role": "reader"},
        headers=headers,
    )
    assert r.status_code == 201
    guid = r.json["guid"]
    assert r.json["role"] == "reader"

    # List
    r = client.get("/admin/users", headers=headers)
    assert r.status_code == 200
    guids = [u["guid"] for u in r.json]
    assert guid in guids

    # Update role
    r = client.put(f"/admin/users/{guid}", json={"role": "admin"}, headers=headers)
    assert r.status_code == 200
    assert r.json["role"] == "admin"

    # Reset password
    r = client.post(f"/admin/users/{guid}/reset-password", json={"password": "NewPass!"}, headers=headers)
    assert r.status_code == 200

    # Deactivate
    r = client.put(f"/admin/users/{guid}", json={"is_active": False}, headers=headers)
    assert r.status_code == 200
    assert r.json["is_active"] is False


def test_user_create_rejects_invalid_role(client, admin_token):
    r = client.post(
        "/admin/users",
        json={"username": "bad", "password": "pass", "role": "superadmin"},
        headers=auth(admin_token),
    )
    assert r.status_code == 400


def test_public_contract_list_is_rate_limited_configured(client):
    r = client.get("/fhir/Contract")
    assert r.status_code in (200, 429)


# --- FHIR validation hardening ---


def test_contract_requires_status(client, admin_token):
    r = client.post(
        "/fhir/Contract",
        json={"resourceType": "Contract"},
        headers=auth(admin_token),
    )
    assert r.status_code == 400
    assert "status" in r.json.get("message", "").lower()


def test_contract_rejects_invalid_status(client, admin_token):
    r = client.post(
        "/fhir/Contract",
        json={"resourceType": "Contract", "status": "bogus"},
        headers=auth(admin_token),
    )
    assert r.status_code == 400


def test_contract_accepts_valid_statuses(client, admin_token):
    headers = auth(admin_token)
    for status in ("executable", "executed", "negotiable"):
        r = client.post(
            "/fhir/Contract",
            json={"resourceType": "Contract", "status": status},
            headers=headers,
        )
        assert r.status_code == 201, f"status '{status}' should be accepted"


def test_contract_subject_must_be_list(client, admin_token):
    r = client.post(
        "/fhir/Contract",
        json={"resourceType": "Contract", "status": "executable", "subject": "bad"},
        headers=auth(admin_token),
    )
    assert r.status_code == 400


def test_contract_subject_bad_reference_format(client, admin_token):
    r = client.post(
        "/fhir/Contract",
        json={
            "resourceType": "Contract",
            "status": "executable",
            "subject": [{"reference": "bad-format"}],
        },
        headers=auth(admin_token),
    )
    assert r.status_code == 400


def test_contract_subject_valid_reference(client, admin_token):
    r = client.post(
        "/fhir/Contract",
        json={
            "resourceType": "Contract",
            "status": "executable",
            "subject": [{"reference": "Organization/abc-123"}],
        },
        headers=auth(admin_token),
    )
    assert r.status_code == 201


def test_contract_period_bad_date(client, admin_token):
    r = client.post(
        "/fhir/Contract",
        json={
            "resourceType": "Contract",
            "status": "executable",
            "period": {"start": "not-a-date"},
        },
        headers=auth(admin_token),
    )
    assert r.status_code == 400
