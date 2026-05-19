from __future__ import annotations

import os
import secrets
import time
from datetime import timedelta

from flask import Flask, jsonify, redirect, request, session
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, get_jwt, jwt_required
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from .config import Config
from .db import make_engine, make_session_factory
from .fhir import build_capability_statement, ensure_contract_shape
from .models import Base, ContractRecord, User
from .security import hash_password, verify_password
from .sso import get_sso_login_url, validate_token


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
        # JSON body cross-origin so it can drive real status/DB dots.
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

    @app.post("/fhir/Contract")
    @require_role("admin")
    def create_contract():
        resource = request.get_json(force=True, silent=True) or {}
        try:
            resource = ensure_contract_shape(resource)
        except ValueError as e:
            return jsonify({"error": "validation", "message": str(e)}), 400

        guid = resource["id"]
        with db_session() as s:
            if s.get(ContractRecord, guid):
                return jsonify({"error": "conflict", "message": "Contract id already exists"}), 409
            s.add(ContractRecord(guid=guid, fhir_contract=resource))
            s.commit()
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
        with db_session() as s:
            row = s.get(ContractRecord, guid)
            if not row:
                return jsonify({"error": "not_found"}), 404
            row.fhir_contract = resource
            s.commit()
        return jsonify(resource)

    @app.delete("/fhir/Contract/<guid>")
    @require_role("admin")
    def delete_contract(guid: str):
        with db_session() as s:
            row = s.get(ContractRecord, guid)
            if not row:
                return jsonify({"error": "not_found"}), 404
            s.delete(row)
            s.commit()
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

    return app


app = create_app()
