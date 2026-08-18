"""
Web Token Manager — Real JWT tokens for web test automation.

OTP login once → cache JWT + userId → refresh expired tokens via /auth/token.
Eliminates fake mock tokens and OTP rate limit issues.

Flow:
    1. Check cache (artifacts/web/jwt_cache.json) → valid? → return
    2. Expired? → POST /auth/token (no OTP needed) → update cache → return
    3. No token? → OTP login via UserAccountManager → save to cache → return
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

import requests

from src.utils.user_account_manager import UserAccountManager

logger = logging.getLogger(__name__)

# Fixed OTP for demo/test users. Environment specific and deliberately not
# defaulted to a literal: a working bypass code in a public repo is a live
# weakness in whatever else shares that auth path. Set TEST_OTP.
DEFAULT_TEST_OTP = os.getenv("TEST_OTP", "")


_CACHE_PATH = Path("artifacts/web/jwt_cache.json")
_CACHE_LOCK = threading.Lock()

# Same base URL as UserAccountManager
_API_BASE = UserAccountManager.BASE_URL
_REFRESH_ENDPOINT = f"{_API_BASE}/auth/token"


def _load_cache() -> dict:
    """Load JWT cache from disk. Returns empty dict if not found."""
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"JWT cache corrupted, starting fresh: {e}")
    return {}


def _save_cache(cache: dict) -> None:
    """Save JWT cache to disk (thread-safe)."""
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _is_token_valid(entry: dict) -> bool:
    """Check if a cached token is still valid (not expired).

    jwt_expiry is a Unix timestamp in MILLISECONDS (e.g., "1773320366929").
    We add a 60-second buffer to avoid using tokens that are about to expire.
    """
    expiry_ms = int(entry.get("jwt_expiry", "0"))
    now_ms = int(time.time() * 1000)
    buffer_ms = 60_000  # 60 seconds buffer
    return expiry_ms > (now_ms + buffer_ms)


def _refresh_token(user_id: int, phone: str) -> dict | None:
    """Refresh an expired JWT token via /auth/token.

    This endpoint does NOT require OTP — just userId + phoneNumber.
    Returns {jwtToken, jwtExpiry} or None on failure.
    """
    headers = {
        **UserAccountManager.DEFAULT_HEADERS,
        "userId": str(user_id),
        "phoneNumber": phone,
    }
    try:
        logger.info(f"Refreshing JWT for user {user_id} (phone: {phone})")
        resp = requests.post(_REFRESH_ENDPOINT, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        jwt_token = data.get("jwtToken")
        jwt_expiry = data.get("jwtExpiry") or data.get("jwtTokenExpiry")
        if jwt_token and jwt_expiry:
            logger.info(f"JWT refreshed for user {user_id}")
            return {"jwtToken": jwt_token, "jwtExpiry": jwt_expiry}
        logger.warning(f"Refresh response missing token/expiry: {data}")
        return None
    except Exception as e:
        logger.warning(f"JWT refresh failed for user {user_id}: {e}")
        return None


def _otp_login(phone: str, otp: str) -> dict | None:
    """Login via OTP and return {jwt_token, jwt_expiry, user_id, user_data}.

    This is the slow path — only called when there's NO cached token at all.
    """
    try:
        logger.info(f"OTP login for {phone} (first-time token acquisition)")
        UserAccountManager.trigger_otp(phone)
        verify = UserAccountManager.verify_otp(phone, otp)

        jwt_token = verify.get("jwtToken")
        jwt_expiry = verify.get("jwtTokenExpiry")
        user = verify.get("user", {})
        user_id = user.get("id")

        if jwt_token and user_id:
            logger.info(f"OTP login success: user_id={user_id}, phone={phone}")
            return {
                "jwt_token": jwt_token,
                "jwt_expiry": jwt_expiry,
                "user_id": user_id,
                "user_data": user,
                "new_user": verify.get("newUser", False),
            }
        logger.error(f"OTP login response missing token/user_id: {verify}")
        return None
    except Exception as e:
        logger.error(f"OTP login failed for {phone}: {e}")
        return None


def get_valid_token(phone: str, otp: str = DEFAULT_TEST_OTP) -> dict:
    """Get a valid JWT token for the given phone number.

    Returns:
        dict with keys: jwt_token, jwt_expiry, user_id
        Raises RuntimeError if all methods fail.

    Flow:
        1. Cache hit + valid → return immediately
        2. Cache hit + expired → refresh via /auth/token
        3. No cache → OTP login (only happens once per user)
    """
    with _CACHE_LOCK:
        cache = _load_cache()
        entry = cache.get(phone)

        # ── Fast path: cached and valid ──
        if entry and _is_token_valid(entry):
            logger.info(f"Cache hit for {phone} (user_id={entry['user_id']}) — token valid")
            return {
                "jwt_token": entry["jwt_token"],
                "jwt_expiry": entry["jwt_expiry"],
                "user_id": entry["user_id"],
            }

        # ── Medium path: cached but expired → refresh (no OTP needed) ──
        if entry and entry.get("user_id"):
            refreshed = _refresh_token(entry["user_id"], phone)
            if refreshed:
                entry["jwt_token"] = refreshed["jwtToken"]
                entry["jwt_expiry"] = refreshed["jwtExpiry"]
                cache[phone] = entry
                _save_cache(cache)
                logger.info(f"Token refreshed for {phone} (user_id={entry['user_id']})")
                return {
                    "jwt_token": entry["jwt_token"],
                    "jwt_expiry": entry["jwt_expiry"],
                    "user_id": entry["user_id"],
                }
            logger.warning(f"Refresh failed for {phone} — falling through to OTP login")

        # ── Slow path: no cache or refresh failed → OTP login ──
        result = _otp_login(phone, otp)
        if result:
            cache[phone] = {
                "jwt_token": result["jwt_token"],
                "jwt_expiry": result["jwt_expiry"],
                "user_id": result["user_id"],
            }
            _save_cache(cache)
            return {
                "jwt_token": result["jwt_token"],
                "jwt_expiry": result["jwt_expiry"],
                "user_id": result["user_id"],
            }

        raise RuntimeError(
            f"All token methods failed for {phone}. "
            f"Check: (1) OTP backend status, (2) Redis rate limits, (3) network."
        )


def mock_otp_with_real_jwt(page, phone: str, otp: str = DEFAULT_TEST_OTP, new_user: bool = False, user_data: dict = None) -> dict:
    """Install OTP mocks that return a REAL JWT token on a Playwright page.

    This is the key function — it:
    1. Gets a real JWT (from cache or refresh or OTP login)
    2. Mocks /otp/trigger (always succeeds, no rate limit)
    3. Mocks /otp/verify to return the REAL JWT + real user data

    The SPA stores the real JWT → all backend APIs (cities, user, etc.) work.

    Args:
        new_user: If True, response has newUser=true → SPA redirects to /onboarding.
                  If False (default), newUser=false → SPA redirects to /app or /.
                  Use mock_otp_for_new_user() for onboarding tests instead.

    Returns:
        dict with jwt_token, jwt_expiry, user_id
    """
    token_data = get_valid_token(phone, otp)

    # Mock OTP trigger — always succeeds
    page.route("**/otp/trigger", lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body='{"success": true, "message": "OTP sent", "status": "SUCCESS"}',
    ))

    # Mock OTP verify — returns REAL JWT
    # Build user profile from provided data or use defaults
    _ud = user_data or {}
    _mock_user = {
        "id": token_data["user_id"],
        "phone": phone,
        "name": _ud.get("name"),
        "dob": _ud.get("dob"),
        "tob": _ud.get("tob"),
        "pob": _ud.get("pob"),
        "issues": [],
        "details": None,
        "createdAt": "2026-01-01",
        "languages": [],
        "country": "IN",
        "gender": _ud.get("gender"),
        "profileResponse": None,
        "latitude": _ud.get("latitude", "0"),
        "longitude": _ud.get("longitude", "0"),
        "type": "",
        "cohort": None,
        "relationshipCount": 0,
        "hasRelationships": False,
        "campaignTag": "DEFAULT",
        "segment": None,
        "primaryCategory": None,
        "baseline": None,
    }

    def _verify_handler(route):
        import json as _json

        route.fulfill(
            status=200,
            content_type="application/json",
            body=_json.dumps({
                "success": True,
                "message": None,
                "status": "SUCCESS",
                "jwtToken": token_data["jwt_token"],
                "jwtTokenExpiry": token_data["jwt_expiry"],
                "user": _mock_user,
                "newUser": new_user,
            }),
        )

    page.route("**/otp/verify", _verify_handler)

    logger.info(
        f"OTP mocks installed with REAL JWT for {phone} "
        f"(user_id={token_data['user_id']})"
    )
    return token_data


def mock_otp_for_new_user(page, phone: str) -> None:
    """Mock OTP endpoints for a NEW user (no real JWT needed).

    Use this when the test only checks redirect behavior (e.g., new user → /onboarding)
    and doesn't need real backend API access. Avoids OTP rate limit issues.

    The mock returns newUser=true so the SPA redirects to /onboarding.
    """
    import json as _json

    # Dummy JWT — SPA stores it but we don't make real API calls
    dummy_jwt = "test_new_user_dummy_jwt_token"
    dummy_expiry = str(int(time.time() * 1000) + 3600000)  # 1 hour from now

    page.route("**/otp/trigger", lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body='{"success": true, "message": "OTP sent", "status": "SUCCESS"}',
    ))

    def _verify_handler(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=_json.dumps({
                "success": True,
                "message": None,
                "status": "SUCCESS",
                "jwtToken": dummy_jwt,
                "jwtTokenExpiry": dummy_expiry,
                "user": {
                    "id": 0,
                    "phone": phone,
                    "name": None, "dob": None, "tob": None, "pob": None,
                    "issues": [], "details": None, "createdAt": "2026-01-01",
                    "languages": [], "country": "IN", "gender": None,
                    "profileResponse": None, "latitude": "0", "longitude": "0",
                    "type": "", "cohort": None, "relationshipCount": 0,
                    "hasRelationships": False, "campaignTag": "DEFAULT",
                    "segment": None, "primaryCategory": None, "baseline": None,
                },
                "newUser": True,
            }),
        )

    page.route("**/otp/verify", _verify_handler)
    logger.info(f"OTP mocks installed for NEW USER {phone} (dummy JWT, no backend)")


def unmock_otp(page) -> None:
    """Remove OTP mocks from a Playwright page."""
    page.unroute("**/otp/trigger")
    page.unroute("**/otp/verify")


def invalidate_cache(phone: str) -> None:
    """Remove a phone's cached token (e.g., after user deletion)."""
    with _CACHE_LOCK:
        cache = _load_cache()
        if phone in cache:
            del cache[phone]
            _save_cache(cache)
            logger.info(f"Cache invalidated for {phone}")


def sync_cache_to_csv() -> int:
    """Sync JWT cache back to web_user_pool.csv.

    Updates jwt_token, jwt_expiry, api_user_id columns for all cached phones.
    Called automatically after test runs, or manually via script.

    Returns:
        Number of rows updated.
    """
    import csv
    import io

    csv_path = Path("tests/data/web_user_pool.csv")
    if not csv_path.exists():
        logger.warning("web_user_pool.csv not found — skipping sync")
        return 0

    cache = _load_cache()
    if not cache:
        return 0

    # Read CSV properly using csv module (handles quoted fields like tags)
    lines = csv_path.read_text().splitlines()
    comment_lines = {}  # line_index → comment text
    data_lines = []

    for i, line in enumerate(lines):
        if line.startswith("#"):
            comment_lines[i] = line
        else:
            data_lines.append((i, line))

    if not data_lines:
        return 0

    # Parse with csv.DictReader
    reader = csv.DictReader(
        [line for _, line in data_lines],
    )
    rows = list(reader)
    fieldnames = reader.fieldnames or []

    # Ensure new columns exist
    for col in ("jwt_token", "jwt_expiry", "api_user_id"):
        if col not in fieldnames:
            fieldnames.append(col)

    # Update rows with cached tokens
    updated = 0
    for row in rows:
        phone = row.get("phone", "").strip()
        entry = cache.get(phone)
        if entry:
            row["jwt_token"] = entry["jwt_token"]
            row["jwt_expiry"] = str(entry["jwt_expiry"])
            row["api_user_id"] = str(entry["user_id"])
            updated += 1

    # Write back, reinserting comments at original positions
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    result_lines = [output.getvalue().strip()]  # header
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")

    for row in rows:
        output.seek(0)
        output.truncate()
        writer.writerow(row)
        result_lines.append(output.getvalue().strip())

    # Rebuild with comments in original positions
    final_lines = []
    data_idx = 0
    for i in range(max(len(lines), len(result_lines) + len(comment_lines))):
        if i in comment_lines:
            final_lines.append(comment_lines[i])
        elif data_idx < len(result_lines):
            final_lines.append(result_lines[data_idx])
            data_idx += 1

    # Add any remaining data lines
    while data_idx < len(result_lines):
        final_lines.append(result_lines[data_idx])
        data_idx += 1

    csv_path.write_text("\n".join(final_lines) + "\n")
    logger.info(f"Synced {updated} JWT tokens to web_user_pool.csv")
    return updated
