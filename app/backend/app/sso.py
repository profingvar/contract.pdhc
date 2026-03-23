"""SSO client — validates tokens and fetches data from sso.pdhc."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlencode

import requests
from flask import current_app

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_TIMEOUT = 10  # seconds


def _sso_cfg() -> dict:
    return {
        "base_url": current_app.config["SSO_BASE_URL"].rstrip("/"),
        "client_id": current_app.config["SSO_CLIENT_ID"],
        "client_secret": current_app.config["SSO_CLIENT_SECRET"],
        "callback_url": current_app.config["SSO_CALLBACK_URL"],
    }


def get_sso_login_url(state: str) -> str:
    """Build the H1 redirect URL for the SSO login page."""
    cfg = _sso_cfg()
    params = urlencode({"next": cfg["callback_url"], "state": state})
    return f"{cfg['base_url']}/login?{params}"


def validate_token(token: str) -> dict | None:
    """H4 — call /api/auth/me/service to validate a JWT and get the access blob.

    Returns the access blob dict on success, None on failure.
    """
    cfg = _sso_cfg()
    url = f"{cfg['base_url']}/api/auth/me/service"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-SSO-Client-Id": cfg["client_id"],
        "X-SSO-Client-Secret": cfg["client_secret"],
    }

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 401:
                logger.warning("SSO token validation returned 401 (expired/revoked)")
                return None
            if resp.status_code == 403:
                logger.error("SSO returned 403 — invalid service credentials")
                return None
            logger.warning("SSO returned %d on attempt %d", resp.status_code, attempt)
        except requests.ConnectionError:
            logger.warning("SSO unreachable on attempt %d/%d", attempt, _MAX_RETRIES)
        except requests.Timeout:
            logger.warning("SSO timeout on attempt %d/%d", attempt, _MAX_RETRIES)

        if attempt < _MAX_RETRIES:
            time.sleep(0.5 * attempt)

    logger.error("SSO validation failed after %d attempts", _MAX_RETRIES)
    return None


def fetch_organisations() -> list | None:
    """Fetch the canonical organisation list from SSO (public endpoint)."""
    cfg = _sso_cfg()
    url = f"{cfg['base_url']}/api/public/organisations"
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
    except (requests.ConnectionError, requests.Timeout):
        logger.warning("Failed to fetch organisations from SSO")
    return None
