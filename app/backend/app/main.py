from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
from datetime import timedelta

import requests as http_requests
from flask import Flask, jsonify, redirect, request, session
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, get_jwt, jwt_required
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from .config import Config
from .consent_emitter import (
    emit_patient_consents,
    revoke_patient_consents,
    _LIFECYCLE_REVOKE_STATUSES,
)
from .consent_reconciler import reconcile as reconcile_consents
from .db import make_engine, make_session_factory
from .fhir import build_capability_statement, ensure_contract_shape, get_contract_scope
from .reform_identity import care_unit_guids_from_blob
from .scope_validation import extract_scope_concept_guids, verify_concepts_exist
from .signer_resolver import verify_signer_references
from .models import Base, ContractRecord, User
from .security import hash_password, verify_password
from .sso import get_sso_login_url, validate_token

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    CORS(app, origins=os.getenv("CORS_ORIGINS", "*"))

    engine = make_engine(app.config["DATABASE_URL"])
    SessionLocal = make_session_factory(engine)

    def wait_for_db(timeout_s: int = 30) -> None:
        deadline = time.time() + timeout_s
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            except (OperationalError, Exception) as e:
                last_err = e
                time.sleep(1)
        raise RuntimeError("Database not ready after waiting") from last_err

    wait_for_db(int(os.getenv("DB_WAIT_TIMEOUT_S", "30")))
    Base.metadata.create_all(engine)

    jwt = JWTManager(app)

    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        storage_uri=os.getenv("LIMITER_STORAGE_URI", "memory://"),
    )

    def db_session():
        return SessionLocal()

    # ── Role mapping ──────────────────────────────────────────────
    # SSO access blob → local role:
    #   is_su_admin=True           → admin
    #   professional user          → admin  (can CRUD contracts)
    #   any other authenticated    → reader

    def _role_from_blob(blob: dict) -> str:
        if blob.get("is_su_admin"):
            return "admin"
        if blob.get("user_type") == "professional":
            return "admin"
        return "reader"

    def require_role(*roles: str):
        def decorator(fn):
            @jwt_required()
            def wrapper(*args, **kwargs):
                claims = get_jwt()
                role = claims.get("role")
                if role not in roles:
                    return jsonify({"error": "forbidden"}), 403
                return fn(*args, **kwargs)

            wrapper.__name__ = fn.__name__
            return wrapper

        return decorator

    def require_service_key(fn):
        """Validate X-Service-Key header for internal service-to-service calls."""
        def wrapper(*args, **kwargs):
            key = app.config.get("INTERNAL_SERVICE_KEY")
            if not key:
                # Not configured → reject all. No details leaked.
                return jsonify({"error": "unauthorized"}), 401
            provided = request.headers.get("X-Service-Key", "")
            if not provided or not hmac.compare_digest(provided, key):
                return jsonify({"error": "unauthorized"}), 401
            return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        return wrapper

    def seed_admin_if_needed():
        if not app.config.get("AUTH_DISABLED"):
            return
        username = os.getenv("BOOTSTRAP_ADMIN_USERNAME")
        password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
        if not username or not password:
            return
        with db_session() as s:
            existing = s.scalar(select(User).where(User.username == username))
            if existing:
                return
            s.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    role="admin",
                    is_active=True,
                )
            )
            s.commit()

    seed_admin_if_needed()

    # ── Party extraction helper (used by internal scope endpoint) ──

    def _extract_contract_parties(contract_resource):
        """Pull requesting + provider org guids out of FHIR Contract.party[].

        Role conventions (matches contract.pdhc UI):
          payer    → requesting/ordering organisation
          provider → fulfilling organisation
        """
        requesting = None
        providers: list[str] = []
        for party in contract_resource.get("party", []) or []:
            role_codes = []
            for role in party.get("role", []) or []:
                for c in role.get("coding", []) or []:
                    if c.get("code"):
                        role_codes.append(c["code"])
            if not role_codes:
                continue
            org_guids = []
            for ref in party.get("reference", []) or []:
                ref_str = (ref or {}).get("reference", "")
                if ref_str.startswith("Organization/"):
                    org_guids.append(ref_str.split("/", 1)[1])
            if "payer" in role_codes and org_guids and requesting is None:
                requesting = org_guids[0]
            if "provider" in role_codes:
                providers.extend(org_guids)
        return {
            "requesting_org_guid": requesting,
            "provider_org_guids": providers,
        }

    # ── Auto-provision PAT helper ────────────────────────────────

    def _validate_scope_concepts(resource):
        """Verify every concept referenced in term[] exists in plan.pdhc.

        Returns a Flask response tuple on rejection, or None to proceed
        (#135). When STRICT_SCOPE_CONCEPTS is false, a plan.pdhc outage
        falls through to None — useful for local dev where plan isn't
        running. Audit logs the verdict either way.
        """
        guids = extract_scope_concept_guids(resource)
        ok, info = verify_concepts_exist(guids)
        app.logger.info(
            'contract scope concept validation: contract_id=%s guids=%d ok=%s info=%s',
            resource.get('id', '?'), len(guids), ok, info,
        )
        if ok:
            return None
        if info.get('missing'):
            return jsonify({
                'error': 'scope_concept_missing',
                'message': "One or more concept GUIDs in term[] do not exist in plan.pdhc",
                'missing_concept_guids': info['missing'],
            }), 422
        return jsonify({
            'error': 'scope_validation_unavailable',
            'message': 'plan.pdhc unreachable; cannot verify scope concepts',
            'detail': info.get('detail'),
        }), 503

    def _auto_provision_pat(contract_resource):
        """Extract provider org from contract party[] and ask request.pdhc to auto-provision a PAT."""
        request_base = app.config.get("REQUEST_BASE_URL", "").rstrip("/")
        service_key = app.config.get("INTERNAL_SERVICE_KEY", "")
        if not request_base or not service_key:
            return

        contract_guid = contract_resource.get("id", "")
        status = contract_resource.get("status", "")
        if status not in ("executed", "executable", "offered", "renewed"):
            return

        # Find provider org GUIDs from party[] with role "provider"
        for party in contract_resource.get("party", []):
            role_codings = []
            for role in party.get("role", []):
                role_codings.extend(role.get("coding", []))
            is_provider = any(c.get("code") == "provider" for c in role_codings)
            if not is_provider:
                continue

            for ref in party.get("reference", []):
                ref_str = ref.get("reference", "")
                if ref_str.startswith("Organization/"):
                    provider_org_guid = ref_str.split("/", 1)[1]
                    try:
                        resp = http_requests.post(
                            f"{request_base}/api/v1/internal/auto-provision-pat",
                            json={
                                "provider_org_guid": provider_org_guid,
                                "contract_guid": contract_guid,
                            },
                            headers={"X-Service-Key": service_key},
                            timeout=10,
                        )
                        logger.info(
                            "Auto-provision PAT for org=%s contract=%s → %s",
                            provider_org_guid, contract_guid, resp.status_code,
                        )
                    except http_requests.RequestException as e:
                        logger.warning(
                            "Failed to auto-provision PAT for org=%s: %s",
                            provider_org_guid, e,
                        )

    def _emit_consents_for_lifecycle(contract_resource):
        """Dispatch to the consent emitter on status change.

        - Active statuses -> emit grants for each patient signer.
        - End-of-life statuses (cancelled/terminated/revoked) -> revoke
          any consents that linked back to this contract.

        Best-effort; ips-side failures are logged but never propagate
        to the contract write."""
        try:
            status = (contract_resource.get("status") or "").lower()
            if status in _LIFECYCLE_REVOKE_STATUSES:
                summary = revoke_patient_consents(
                    contract_resource,
                    reason=f"contract_status:{status}",
                )
                logger.info(
                    "contract consent revoke contract=%s status=%s -> %s",
                    contract_resource.get("id", "?"), status, summary,
                )
            else:
                summary = emit_patient_consents(contract_resource)
                logger.info(
                    "contract consent emit contract=%s status=%s -> %s",
                    contract_resource.get("id", "?"), status, summary,
                )
        except Exception:
            logger.warning(
                "consent emitter raised for contract %s",
                contract_resource.get("id", "?"), exc_info=True,
            )

    # ── Health ────────────────────────────────────────────────────

    @app.get("/health")
    def health():
        # Shape per CLAUDE.md §10 — matches cgm.pdhc / plan.pdhc.
        db_ok = False
        try:
            with db_session() as s:
                s.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            pass
        status = "ok" if db_ok else "degraded"
        code = 200 if db_ok else 503
        resp = jsonify({
            "status": status,
            "database": "connected" if db_ok else "unavailable",
            "service": "contract.pdhc",
            "version": os.environ.get("APP_VERSION", "dev"),
        })
        # Ticket #70 / CLAUDE.md §10: let www.pdhc.se/services.html read the
        # JSON body cross-origin so it can drive real status/DB dots. Specific
        # origin + Vary: Origin (not "*") keeps future Allow-Credentials
        # spec-compliant.
        resp.headers["Access-Control-Allow-Origin"] = "https://www.pdhc.se"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Vary"] = "Origin"
        resp.headers["Cache-Control"] = "no-store"
        return resp, code

    # ── SSO Auth (H1–H4) ─────────────────────────────────────────

    @app.get("/api/v1/auth/login")
    def sso_login():
        """H1 — redirect to SSO for authentication."""
        if app.config.get("AUTH_DISABLED"):
            return jsonify({"message": "Auth disabled — use /auth/login for local auth"}), 200
        state = secrets.token_urlsafe(32)
        session["sso_state"] = state
        return redirect(get_sso_login_url(state))

    @app.get("/api/v1/auth/callback")
    def sso_callback():
        """H3→H4 — receive JWT from SSO redirect, validate, issue local JWT."""
        if app.config.get("AUTH_DISABLED"):
            return jsonify({"error": "Auth disabled"}), 400

        # Check for SSO error response
        error = request.args.get("error")
        if error:
            desc = request.args.get("error_description", "Authentication failed")
            return redirect(f"/?sso_error={desc}")

        # CSRF state validation
        state = request.args.get("state", "")
        expected_state = session.pop("sso_state", None)
        if not state or state != expected_state:
            return redirect("/?sso_error=CSRF+state+mismatch")

        token = request.args.get("token", "")
        if not token:
            return redirect("/?sso_error=No+token+received")

        # H4 — validate token with SSO
        blob = validate_token(token)
        if blob is None:
            return redirect("/?sso_error=Token+validation+failed")

        # Ticket #55 / SSO #43: refuse to mint a local JWT while SSO requires
        # a password change. Bounce the user to SSO's change-password page;
        # once cleared there, a second SSO login will land here with the
        # flag off and minting proceeds normally.
        if blob.get("must_change_password"):
            sso_base = app.config["SSO_BASE_URL"].rstrip("/")
            return redirect(f"{sso_base}/change-password")

        # Map SSO access blob to local JWT claims
        role = _role_from_blob(blob)
        local_token = create_access_token(
            identity=blob.get("user_guid", "sso-user"),
            additional_claims={
                "role": role,
                "email": blob.get("email", ""),
                "user_type": blob.get("user_type", ""),
                "user_guid": blob.get("user_guid", ""),
                "is_su_admin": blob.get("is_su_admin", False),
                "effective_phases": blob.get("effective_phases", []),
                "organization_ids": blob.get("organization_ids", []),
                # Reform identity (M0 #414), dual with the legacy fields above.
                "session_phases": blob.get("session_phases", []),
                "care_unit_guids": care_unit_guids_from_blob(blob),
                "sso": True,
            },
            expires_delta=timedelta(hours=8),
        )

        # Redirect to SPA with the JWT in query param
        base_url = os.getenv("PUBLIC_WEB_URL", "")
        return redirect(f"{base_url}/?sso_token={local_token}")

    @app.get("/api/v1/auth/logout")
    def sso_logout():
        """Clear session."""
        session.pop("sso_state", None)
        return redirect("/")

    @app.get("/api/v1/auth/me")
    @jwt_required()
    def sso_me():
        """Return current user claims from JWT."""
        claims = get_jwt()
        return jsonify({
            "user_guid": claims.get("user_guid", claims.get("sub", "")),
            "email": claims.get("email", claims.get("username", "")),
            "role": claims.get("role", "reader"),
            "user_type": claims.get("user_type", ""),
            "is_su_admin": claims.get("is_su_admin", False),
            "effective_phases": claims.get("effective_phases", []),
            "organization_ids": claims.get("organization_ids", []),
            "session_phases": claims.get("session_phases", []),
            "care_unit_guids": claims.get("care_unit_guids", []),
            "sso": claims.get("sso", False),
        })

    # ── Local auth (fallback for AUTH_DISABLED mode) ──────────────

    @app.post("/auth/login")
    def login():
        if not app.config.get("AUTH_DISABLED"):
            return jsonify({"error": "Local auth disabled — use SSO", "sso_login_url": "/api/v1/auth/login"}), 400
        data = request.get_json(force=True, silent=True) or {}
        username = data.get("username", "")
        password = data.get("password", "")
        with db_session() as s:
            user = s.scalar(select(User).where(User.username == username))
            if not user or not user.is_active or not verify_password(password, user.password_hash):
                return jsonify({"error": "invalid_credentials"}), 401

            token = create_access_token(
                identity=user.guid,
                additional_claims={"role": user.role, "username": user.username},
                expires_delta=timedelta(hours=8),
            )
            return jsonify({"access_token": token, "role": user.role})

    # ── FHIR endpoints ────────────────────────────────────────────

    @app.get("/fhir/metadata")
    @limiter.limit(app.config["READ_RATE_LIMIT"])
    def capability_statement():
        return jsonify(build_capability_statement()), 200, {"Content-Type": "application/fhir+json"}

    @app.get("/fhir/Contract")
    @limiter.limit(app.config["READ_RATE_LIMIT"])
    def list_contracts():
        with db_session() as s:
            rows = s.scalars(select(ContractRecord).order_by(ContractRecord.updated_at.desc())).all()
            return jsonify(
                {
                    "resourceType": "Bundle",
                    "type": "searchset",
                    "entry": [{"resource": r.fhir_contract} for r in rows],
                }
            )

    @app.get("/fhir/Contract/<guid>")
    @limiter.limit(app.config["READ_RATE_LIMIT"])
    def get_contract(guid: str):
        with db_session() as s:
            row = s.get(ContractRecord, guid)
            if not row:
                return jsonify({"error": "not_found"}), 404
            return jsonify(row.fhir_contract)

    @app.get("/fhir/Contract/<guid>/scope")
    @limiter.limit(app.config["READ_RATE_LIMIT"])
    def get_contract_scope_endpoint(guid: str):
        """Lightweight scope endpoint — returns concept scope + contract status.
        Public + rate-limited, or via X-Service-Key (bypasses rate limit on /internal path).
        """
        with db_session() as s:
            row = s.get(ContractRecord, guid)
            if not row:
                return jsonify({"error": "not_found"}), 404

            contract = row.fhir_contract
            status = contract.get("status", "unknown")

            # Revoked/terminated/cancelled contracts → empty scope, all submissions rejected
            if status in ("revoked", "terminated", "cancelled"):
                return jsonify({
                    "contract_guid": guid,
                    "status": status,
                    "scope_defined": True,
                    "request_scope": [],
                    "return_scope": {"obligatory_return": [], "optional_return": []},
                })

            scope = get_contract_scope(contract)

            if scope is None:
                return jsonify({
                    "contract_guid": guid,
                    "status": status,
                    "scope_defined": False,
                    "request_scope": None,
                    "return_scope": None,
                })

            return jsonify({
                "contract_guid": guid,
                "status": status,
                "scope_defined": True,
                "request_scope": scope.get("request_scope"),
                "return_scope": scope.get("return_scope"),
            })

    @app.get("/internal/contract/<guid>/scope")
    @require_service_key
    def get_contract_scope_internal(guid: str):
        """Internal scope endpoint — same logic, X-Service-Key auth, no rate limit."""
        with db_session() as s:
            row = s.get(ContractRecord, guid)
            if not row:
                return jsonify({"error": "not_found"}), 404

            contract = row.fhir_contract
            status = contract.get("status", "unknown")
            parties = _extract_contract_parties(contract)

            if status in ("revoked", "terminated", "cancelled"):
                return jsonify({
                    "contract_guid": guid,
                    "status": status,
                    "scope_defined": True,
                    "request_scope": [],
                    "return_scope": {"obligatory_return": [], "optional_return": []},
                    "parties": parties,
                })

            scope = get_contract_scope(contract)

            if scope is None:
                return jsonify({
                    "contract_guid": guid,
                    "status": status,
                    "scope_defined": False,
                    "request_scope": None,
                    "return_scope": None,
                    "parties": parties,
                })

            return jsonify({
                "contract_guid": guid,
                "status": status,
                "scope_defined": True,
                "request_scope": scope.get("request_scope"),
                "return_scope": scope.get("return_scope"),
                "parties": parties,
            })

    def _verify_signers(resource):
        """Ticket #230 — resolve every signer reference against the
        relevant catalogue. Returns a Flask response tuple on
        rejection or ``None`` to proceed."""
        with db_session() as s:
            failures = verify_signer_references(resource, session=s)
        if failures:
            return jsonify({
                "error": "signer_unresolved",
                "message": (
                    "One or more signer references could not be "
                    "resolved"
                ),
                "failures": failures,
            }), 400
        return None

    @app.post("/fhir/Contract")
    @require_role("admin")
    def create_contract():
        resource = request.get_json(force=True, silent=True) or {}
        try:
            resource = ensure_contract_shape(resource)
        except ValueError as e:
            return jsonify({"error": "validation", "message": str(e)}), 400

        resp = _validate_scope_concepts(resource)
        if resp is not None:
            return resp

        resp = _verify_signers(resource)
        if resp is not None:
            return resp

        guid = resource["id"]
        with db_session() as s:
            if s.get(ContractRecord, guid):
                return jsonify({"error": "conflict", "message": "Contract id already exists"}), 409
            s.add(ContractRecord(guid=guid, fhir_contract=resource))
            s.commit()
        _auto_provision_pat(resource)
        _emit_consents_for_lifecycle(resource)
        return jsonify(resource), 201

    @app.put("/fhir/Contract/<guid>")
    @require_role("admin")
    def update_contract(guid: str):
        resource = request.get_json(force=True, silent=True) or {}
        try:
            resource = ensure_contract_shape(resource)
        except ValueError as e:
            return jsonify({"error": "validation", "message": str(e)}), 400

        resource["id"] = guid
        resp = _validate_scope_concepts(resource)
        if resp is not None:
            return resp

        resp = _verify_signers(resource)
        if resp is not None:
            return resp

        with db_session() as s:
            row = s.get(ContractRecord, guid)
            if not row:
                return jsonify({"error": "not_found"}), 404
            row.fhir_contract = resource
            s.commit()
        _auto_provision_pat(resource)
        _emit_consents_for_lifecycle(resource)
        return jsonify(resource)

    @app.delete("/fhir/Contract/<guid>")
    @require_role("admin")
    def delete_contract(guid: str):
        with db_session() as s:
            row = s.get(ContractRecord, guid)
            if not row:
                return jsonify({"error": "not_found"}), 404
            existing_resource = dict(row.fhir_contract or {})
            existing_resource["id"] = guid
            s.delete(row)
            s.commit()
        # Best-effort revoke of any auto-emitted consents linked back
        # to this contract.
        try:
            revoke_patient_consents(
                existing_resource, reason=f"contract_deleted:{guid}",
            )
        except Exception:
            logger.warning(
                "consent revoke after delete failed for contract %s",
                guid, exc_info=True,
            )
        return "", 204

    # ── Admin: Users (only available in AUTH_DISABLED mode) ───────

    @app.get("/admin/users")
    @require_role("admin")
    def list_users():
        with db_session() as s:
            users = s.scalars(select(User).order_by(User.created_at.desc())).all()
            return jsonify(
                [
                    {
                        "guid": u.guid,
                        "username": u.username,
                        "role": u.role,
                        "is_active": u.is_active,
                        "created_at": u.created_at.isoformat(),
                    }
                    for u in users
                ]
            )

    @app.post("/admin/users")
    @require_role("admin")
    def create_user():
        data = request.get_json(force=True, silent=True) or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        role = data.get("role") or "reader"
        if role not in ("admin", "reader"):
            return jsonify({"error": "validation", "message": "role must be admin|reader"}), 400
        if not username or not password:
            return jsonify({"error": "validation", "message": "username and password required"}), 400
        with db_session() as s:
            if s.scalar(select(User).where(User.username == username)):
                return jsonify({"error": "conflict", "message": "username already exists"}), 409
            u = User(username=username, password_hash=hash_password(password), role=role, is_active=True)
            s.add(u)
            s.commit()
            return jsonify({"guid": u.guid, "username": u.username, "role": u.role, "is_active": u.is_active}), 201

    @app.put("/admin/users/<guid>")
    @require_role("admin")
    def update_user(guid: str):
        data = request.get_json(force=True, silent=True) or {}
        with db_session() as s:
            user = s.get(User, guid)
            if not user:
                return jsonify({"error": "not_found"}), 404
            if "role" in data:
                if data["role"] not in ("admin", "reader"):
                    return jsonify({"error": "validation", "message": "role must be admin|reader"}), 400
                user.role = data["role"]
            if "is_active" in data:
                user.is_active = bool(data["is_active"])
            s.commit()
            return jsonify({"guid": user.guid, "username": user.username, "role": user.role, "is_active": user.is_active})

    @app.post("/admin/users/<guid>/reset-password")
    @require_role("admin")
    def reset_user_password(guid: str):
        data = request.get_json(force=True, silent=True) or {}
        new_password = data.get("password", "")
        if not new_password:
            return jsonify({"error": "validation", "message": "password required"}), 400
        with db_session() as s:
            user = s.get(User, guid)
            if not user:
                return jsonify({"error": "not_found"}), 404
            user.password_hash = hash_password(new_password)
            s.commit()
            return jsonify({"ok": True, "guid": user.guid, "username": user.username})

    # ── Reconciler CLI (#243) ─────────────────────────────────────
    @app.cli.command("reconcile-consents")
    def _reconcile_consents_cli():  # noqa: D401
        """Walk every contract in a lifecycle status, re-emit / re-revoke
        the consents IPS should be holding. Recovers from the silent-drop
        failure mode documented in consent_emitter.py."""
        import click as _click
        with db_session() as s:
            summary = reconcile_consents(s)
        _click.echo(
            "reconcile-consents "
            f"checked={summary['checked']} "
            f"grants_re_emitted={summary['grants_re_emitted']} "
            f"revokes_re_called={summary['revokes_re_called']} "
            f"grant_attempts={summary['grant_attempts']} "
            f"revoke_attempts={summary['revoke_attempts']} "
            f"errors={summary['errors']}"
        )

    return app


app = create_app()
