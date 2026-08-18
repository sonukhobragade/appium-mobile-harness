"""Fetch the /translations payload — the runtime source of truth for UI strings.

The app resolves every user-visible label through i18next, which is fed by this
endpoint (keyed by the Accept-Language header). Backend config blobs such as
/client/config → reportsConfig.templateDetailsCta are no longer read by FE.
"""

from __future__ import annotations

import requests

from src.utils.user_account_manager import UserAccountManager

DEFAULT_LOCALE = "en-IN"


def fetch_translations(locale: str = DEFAULT_LOCALE, auth_token: str = "") -> dict:
    """Return the raw /translations response: {"hashValue": ..., "translation": {...}}."""
    headers = {**UserAccountManager.DEFAULT_HEADERS, "Accept-Language": locale}
    if auth_token:
        headers["auth_token"] = auth_token
    resp = requests.get(f"{UserAccountManager.BASE_URL}/translations", headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()
