"""The SSO entry point must REDIRECT, never render or return a body.

contract's browser UI used to show a local card with a "Sign in with SSO"
button. Every sibling with a browser UI — analyse, rosetta, dashboard, onboard,
plan — does the opposite: an unauthenticated request to a protected page 302s
to its own login route, which immediately redirects to sso.pdhc and comes back
authenticated. rosetta/app/routes/auth.py is the shortest statement of it:

    @bp.get("/login")
    def login():
        ...
        return redirect(initiate_sso_login(next_url, state))

No intermediate page, no button. The SPA now navigates straight here, which
makes what this route RETURNS part of the contract rather than a detail: a JSON
body at a URL the browser is sent to puts raw JSON on screen with no way back.

That is exactly what the AUTH_DISABLED branch used to do, and it is what these
tests pin.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _base_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test")
    monkeypatch.setenv("DATABASE_URL",
                       os.getenv("TEST_DATABASE_URL",
                                 "sqlite+pysqlite:///:memory:"))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", "test-service-key-12345")
    monkeypatch.setenv("PUBLIC_WEB_URL", "https://contract.test")
    monkeypatch.setenv("SSO_BASE_URL", "https://sso.test")
    monkeypatch.setenv("SSO_CLIENT_ID", "cid")
    monkeypatch.setenv("SSO_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SSO_CALLBACK_URL",
                       "https://contract.test/api/v1/auth/callback")


def _client(monkeypatch, *, auth_disabled: bool):
    """A client whose AUTH_DISABLED is what the ROUTE will actually read.

    Setting the env var does not work here, and the first version of this file
    was wrong because of it: `app/config.py` evaluates `os.getenv` in the
    CLASS BODY of `Config`, so the values are fixed the first time the module
    is imported and every later `monkeypatch.setenv` is inert. The
    AUTH_DISABLED tests silently exercised the SSO branch and failed with a
    redirect to sso.test — which was the harness lying, not the code being
    wrong. (Same family as the AUTH_MODE env clobber in
    infra_pdhc_test_harness_gotchas.)

    The route reads `app.config.get("AUTH_DISABLED")` per request, so setting
    it on the built app exercises the branch for real.
    """
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("FLASK_ENV", "production")
    from app.main import create_app
    app = create_app()
    # Set every value the handshake reads, for the same reason: `_sso_cfg()`
    # takes them from `current_app.config`, which was baked at first import.
    # Relying on the env here made this file pass ALONE and fail in the full
    # suite, because another module had imported config first with different
    # values -- a test that depends on collection order is not a test.
    app.config.update(
        TESTING=True,
        AUTH_DISABLED=bool(auth_disabled),
        SSO_BASE_URL="https://sso.test",
        SSO_CLIENT_ID="cid",
        SSO_CLIENT_SECRET="csecret",
        SSO_CALLBACK_URL="https://contract.test/api/v1/auth/callback",
    )
    return app.test_client()


class TestLoginRedirectsToSso:
    def test_it_is_a_redirect_to_sso_not_a_page(self, monkeypatch):
        """The whole point of the fix: a 302 at sso.pdhc, not a body."""
        c = _client(monkeypatch, auth_disabled=False)
        r = c.get("/api/v1/auth/login", follow_redirects=False)
        assert r.status_code in (301, 302, 303, 307, 308), (
            "expected a redirect to SSO, got %s — the SPA navigates here, so "
            "a body lands in the browser window" % r.status_code)
        loc = r.headers["Location"]
        assert loc.startswith("https://sso.test"), loc

    def test_it_carries_a_csrf_state(self, monkeypatch):
        """The state is what makes the callback able to reject a replay."""
        c = _client(monkeypatch, auth_disabled=False)
        loc = c.get("/api/v1/auth/login").headers["Location"]
        assert "state=" in loc, loc

    def test_the_body_is_never_json(self, monkeypatch):
        c = _client(monkeypatch, auth_disabled=False)
        r = c.get("/api/v1/auth/login")
        assert r.get_json(silent=True) is None, (
            "a JSON body here is rendered as text by the browser the SPA "
            "sent there")


class TestAuthDisabledIsNotADeadEnd:
    """Local dev must not land on raw JSON.

    This branch returned `{"message": "Auth disabled — use /auth/login for
    local auth"}, 200`. That was harmless while a human clicked a button and a
    dead end once the SPA started navigating here automatically: the browser
    would show that object and offer no way back to the app.
    """

    def test_it_redirects_to_the_spa_stop_surface(self, monkeypatch):
        c = _client(monkeypatch, auth_disabled=True)
        r = c.get("/api/v1/auth/login", follow_redirects=False)
        assert r.status_code in (301, 302, 303, 307, 308), r.status_code
        loc = r.headers["Location"]
        assert loc.startswith("https://contract.test"), loc
        # The SPA already knows how to display `sso_error`; reusing it means
        # no new surface had to be invented for this case.
        assert "sso_error=" in loc, loc

    def test_it_does_not_return_json(self, monkeypatch):
        c = _client(monkeypatch, auth_disabled=True)
        r = c.get("/api/v1/auth/login")
        assert r.get_json(silent=True) is None, (
            "the AUTH_DISABLED dead end is back")

    def test_it_says_why(self, monkeypatch):
        """A redirect with an empty reason is its own dead end."""
        c = _client(monkeypatch, auth_disabled=True)
        loc = c.get("/api/v1/auth/login").headers["Location"]
        assert "Auth+disabled" in loc or "Auth%20disabled" in loc, loc
