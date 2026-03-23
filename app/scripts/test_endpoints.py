from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import requests


def iso_utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def main() -> int:
    api_base = os.getenv("API_BASE", "http://localhost:9021")
    admin_user = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "change-me")

    out_dir = os.getenv("RESULTS_DIR", "")
    if not out_dir:
        out_dir = os.path.join(os.getcwd(), "results", f"{iso_utc_timestamp()}_results")

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "endpoint-test.json")

    results: dict[str, object] = {"api_base": api_base, "checks": [], "passed": 0, "failed": 0}
    token: str = ""

    def record(name: str, r: requests.Response, expect_status: int | None = None):
        try:
            body = r.json()
        except Exception:
            body = r.text
        ok = r.ok if expect_status is None else (r.status_code == expect_status)
        results["checks"].append(
            {"name": name, "status_code": r.status_code, "ok": ok, "body": body}
        )
        label = "PASS" if ok else "FAIL"
        results["passed" if ok else "failed"] += 1
        print(f"  [{label}] {name} -> {r.status_code}")

    print("=== contract.pdhc endpoint tests ===\n")

    # --- 1. Health ---
    record("health", requests.get(f"{api_base}/health", timeout=5))

    # --- 2. CapabilityStatement structure ---
    r = requests.get(f"{api_base}/fhir/metadata", timeout=5)
    record("capability_statement", r)
    cs = r.json() if r.ok else {}
    cs_ok = (
        cs.get("resourceType") == "CapabilityStatement"
        and cs.get("status") == "active"
        and cs.get("fhirVersion") == "5.0.0"
        and isinstance(cs.get("rest"), list)
        and len(cs["rest"]) > 0
        and any(
            res.get("type") == "Contract"
            for res in cs["rest"][0].get("resource", [])
        )
    )
    results["checks"].append({"name": "capability_statement_structure", "ok": cs_ok})
    results["passed" if cs_ok else "failed"] += 1
    print(f"  [{'PASS' if cs_ok else 'FAIL'}] capability_statement_structure")

    # --- 3. Public contract list ---
    record("public_list_contracts", requests.get(f"{api_base}/fhir/Contract", timeout=5))

    # --- 4. Auth: login success ---
    r = requests.post(
        f"{api_base}/auth/login",
        json={"username": admin_user, "password": admin_pass},
        timeout=5,
    )
    record("login_success", r, expect_status=200)
    if r.status_code == 200:
        token = r.json().get("access_token", "")

    # --- 5. Auth: login failure ---
    record(
        "login_failure",
        requests.post(
            f"{api_base}/auth/login",
            json={"username": "nobody", "password": "wrong"},
            timeout=5,
        ),
        expect_status=401,
    )

    # --- 6. Unauthorized admin access ---
    record(
        "unauthorized_admin_access",
        requests.get(f"{api_base}/admin/users", timeout=5),
        expect_status=401,
    )

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # --- 7. Contract CRUD cycle ---
    contract_payload = {
        "resourceType": "Contract",
        "status": "executable",
        "period": {"start": "2026-01-01T00:00:00Z", "end": "2026-12-31T23:59:59Z"},
    }

    r = requests.post(f"{api_base}/fhir/Contract", json=contract_payload, headers=headers, timeout=5)
    record("contract_create", r, expect_status=201)
    contract_guid = r.json().get("id", "") if r.status_code == 201 else ""

    if contract_guid:
        record(
            "contract_read",
            requests.get(f"{api_base}/fhir/Contract/{contract_guid}", timeout=5),
        )

        updated = {**contract_payload, "id": contract_guid, "status": "executed"}
        record(
            "contract_update",
            requests.put(f"{api_base}/fhir/Contract/{contract_guid}", json=updated, headers=headers, timeout=5),
        )

        r = requests.get(f"{api_base}/fhir/Contract", timeout=5)
        record("contract_list_after_create", r)
        bundle = r.json() if r.ok else {}
        has_entry = any(
            e.get("resource", {}).get("id") == contract_guid
            for e in bundle.get("entry", [])
        )
        results["checks"].append({"name": "contract_in_bundle", "ok": has_entry})
        results["passed" if has_entry else "failed"] += 1
        print(f"  [{'PASS' if has_entry else 'FAIL'}] contract_in_bundle")

        record(
            "contract_delete",
            requests.delete(f"{api_base}/fhir/Contract/{contract_guid}", headers=headers, timeout=5),
            expect_status=204,
        )

        record(
            "contract_read_after_delete",
            requests.get(f"{api_base}/fhir/Contract/{contract_guid}", timeout=5),
            expect_status=404,
        )

    # --- 8. User management cycle ---
    test_user = {"username": "testuser_ep", "password": "TestPass123!", "role": "reader"}
    r = requests.post(f"{api_base}/admin/users", json=test_user, headers=headers, timeout=5)
    record("user_create", r, expect_status=201)
    user_guid = r.json().get("guid", "") if r.status_code == 201 else ""

    record("user_list", requests.get(f"{api_base}/admin/users", headers=headers, timeout=5))

    if user_guid:
        record(
            "user_update_role",
            requests.put(
                f"{api_base}/admin/users/{user_guid}",
                json={"role": "admin"},
                headers=headers,
                timeout=5,
            ),
        )

        record(
            "user_reset_password",
            requests.post(
                f"{api_base}/admin/users/{user_guid}/reset-password",
                json={"password": "NewPass456!"},
                headers=headers,
                timeout=5,
            ),
        )

        # Deactivate to clean up
        requests.put(
            f"{api_base}/admin/users/{user_guid}",
            json={"is_active": False},
            headers=headers,
            timeout=5,
        )

    # --- Summary ---
    print(f"\n=== Results: {results['passed']} passed, {results['failed']} failed ===")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Results written to: {out_path}")
    return 1 if results["failed"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
