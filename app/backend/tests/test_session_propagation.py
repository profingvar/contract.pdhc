"""X2 operator-session propagation (#423) — contract.pdhc adoption.

contract forwards the operator's X-Operator-Session-Id on its onward calls to
ips.pdhc (consent emit + signer resolve), plan.pdhc (scope validation) and
request.pdhc (auto-provision PAT). Self-contained (bare Flask app for the
request context) so it needs no DB.
"""
from unittest.mock import patch

from flask import Flask, session

from app.session_headers import current_session_id, outbound_session_headers
from app import consent_emitter

SID = "sess-contract-1"


def _ctx_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    return app


def test_helper_resolves_and_gates():
    app = _ctx_app()
    with app.test_request_context("/", headers={"X-Operator-Session-Id": SID}):
        assert current_session_id() == SID
        assert outbound_session_headers() == {"X-Operator-Session-Id": SID}
    with app.test_request_context("/"):
        assert outbound_session_headers() == {}


def test_from_session_blob_sid():
    app = _ctx_app()
    with app.test_request_context("/"):
        session["access_blob"] = {"session_id": SID}
        assert outbound_session_headers() == {"X-Operator-Session-Id": SID}


def test_ips_headers_carry_operator_session():
    """The shared ips onward-call header helper carries the operator session."""
    app = _ctx_app()
    with app.test_request_context("/", headers={"X-Operator-Session-Id": SID}):
        assert consent_emitter._ips_headers().get("X-Operator-Session-Id") == SID
    with app.test_request_context("/"):
        assert "X-Operator-Session-Id" not in consent_emitter._ips_headers()
