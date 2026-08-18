"""
Backend API data fetcher for test expected values.

Fetches current backend data (subscription plans, pricing, etc.) before tests run,
so expected values stay in sync with the backend instead of being hardcoded.

GENERIC ENGINE — per-API config lives in config/api_responses/<api_name>/:
    schema.json       → required_fields + required_deep_paths
    known_fields.json → field path tracking (new field detection)
    response.json     → fallback response (API down → uses this)

Adding a new API:
    1. Add endpoint to config/api_endpoints.json
    2. Create config/api_responses/<api_name>/schema.json
    3. Create src/utils/transforms/<api_name>.py with SCREEN_TRANSFORMS
    4. Done — this engine auto-discovers everything.

VALIDATION CHAIN (per endpoint, in order):
    1. Hash change detection     → detects "something changed"
    2. schema.json validation    → detects "this named field/path is missing"
    3. Pydantic contract check   → detects "field type changed / renamed" ← NEW
    4. transforms/*.py           → builds expected screen values
    5. expected_screens fixture  → exposed to tests

Usage:
    from src.utils.api_fetcher import fetch_all_endpoints
    data = fetch_all_endpoints()  # Returns dict keyed by endpoint name
"""

import hashlib
import json
import logging
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Fixed OTP for demo/test users. Environment specific and deliberately not
# defaulted to a literal: a working bypass code in a public repo is a live
# weakness in whatever else shares that auth path. Set TEST_OTP.
DEFAULT_TEST_OTP = os.getenv("TEST_OTP", "")


# Cached JWT token (session-level, thread-safe)
_jwt_token_cache: dict[str, str] = {}
_jwt_lock = threading.Lock()

# Paths relative to project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
ENDPOINTS_FILE = CONFIG_DIR / "api_endpoints.json"
HASHES_FILE = CONFIG_DIR / "response_hashes.json"
CHANGE_LOG_FILE = CONFIG_DIR / "change_log.json"
API_RESPONSES_DIR = CONFIG_DIR / "api_responses"

# Max entries in change_log.json before trimming oldest
CHANGE_LOG_MAX_ENTRIES = 100


# ── JSON helpers ──────────────────────────────────────────────────────────


def _load_json(path: Path, default=None):
    """Load JSON file, returning default if missing or invalid."""
    try:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load {path}: {e}")
    return default if default is not None else {}


def _save_json(path: Path, data) -> None:
    """Save data as JSON with pretty-print."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Per-API file paths ────────────────────────────────────────────────────


def _api_dir(endpoint_name: str) -> Path:
    """Get the per-API config directory: config/api_responses/<endpoint_name>/"""
    return API_RESPONSES_DIR / endpoint_name


def _schema_path(endpoint_name: str) -> Path:
    """Per-API schema: config/api_responses/<n>/<n>_schema.json"""
    return _api_dir(endpoint_name) / f"{endpoint_name}_schema.json"


def _known_fields_path(endpoint_name: str) -> Path:
    """Per-API known fields: config/api_responses/<n>/<n>_known_fields.json"""
    return _api_dir(endpoint_name) / f"{endpoint_name}_known_fields.json"


def _fallback_path(endpoint_name: str) -> Path:
    """Per-API fallback: config/api_responses/<n>/<n>_response.json"""
    return _api_dir(endpoint_name) / f"{endpoint_name}_response.json"


# ── Core utilities ────────────────────────────────────────────────────────


def compute_hash(data: dict) -> str:
    """Compute SHA-256 hash of JSON-serialized data for change detection."""
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _resolve_path(data, path: str):
    """
    Resolve a dot-notation path in nested data.

    Supports:
        "planAmount"                       → data["planAmount"]
        "extraInfo.sideButton"             → data["extraInfo"]["sideButton"]
        "[0].planAmount"                   → data[0]["planAmount"]
        "subscriptionPlans[0].planAmount"  → data["subscriptionPlans"][0]["planAmount"]

    Returns:
        (True, value) if found, (False, None) if not.
    """
    parts = path.replace("[", ".[").split(".")
    current = data
    for part in parts:
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            idx = int(part[1:-1])
            if isinstance(current, list) and len(current) > idx:
                current = current[idx]
            else:
                return False, None
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def _extract_all_field_paths(data, prefix="") -> set:
    """
    Extract all field paths from a nested dict/list structure.

    Used for new field detection: compare current paths vs known paths.
    Only inspects first element of arrays (schema detection, not data scan).

    Returns set of dot-notation paths like:
        {"planId", "planAmount", "extraInfo", "extraInfo.sideButton", ...}
    """
    paths = set()
    if isinstance(data, dict):
        for key, value in data.items():
            full_path = f"{prefix}.{key}" if prefix else key
            paths.add(full_path)
            paths.update(_extract_all_field_paths(value, full_path))
    elif isinstance(data, list) and len(data) > 0:
        # Only inspect first element for schema detection
        paths.update(_extract_all_field_paths(data[0], f"{prefix}[0]"))
    return paths


# ── Schema validation ─────────────────────────────────────────────────────


def _load_schema(endpoint_name: str) -> dict:
    """Load per-API schema from config/api_responses/<n>/schema.json.

    Returns:
        {"required_fields": [...], "required_deep_paths": [...]}
        Empty dict if no schema file exists (validation skipped).
    """
    schema = _load_json(_schema_path(endpoint_name), default={})
    if schema:
        logger.debug(
            f"[{endpoint_name}] Loaded schema: "
            f"{len(schema.get('required_fields', []))} fields, "
            f"{len(schema.get('required_deep_paths', []))} deep paths"
        )
    return schema


def validate_schema(endpoint_name: str, data: dict | list) -> dict:
    """
    Validate API response schema — checks THREE things:

    1. Required top-level fields exist (prevents KeyError in transforms)
    2. Required deep paths exist (prevents nested access crashes)
    3. New fields detected (early awareness of backend additions)

    Config is read from config/api_responses/<n>/schema.json.
    If no schema file exists, validation is skipped (all valid).

    Args:
        endpoint_name: API endpoint name (e.g., "subscription_plans")
        data: The API response data (after response_key extraction)

    Returns:
        {
            "valid": bool,
            "missing_fields": [],
            "missing_paths": [],
            "new_fields": [],
            "removed_fields": [],
        }
    """
    result = {
        "valid": True,
        "missing_fields": [],
        "missing_paths": [],
        "new_fields": [],
        "removed_fields": [],
    }

    schema = _load_schema(endpoint_name)

    # ── 1. Check required top-level fields ──
    required = schema.get("required_fields", [])
    if required:
        sample = data[0] if isinstance(data, list) and data else data
        if isinstance(sample, dict):
            for field in required:
                if field not in sample:
                    result["missing_fields"].append(field)
                    result["valid"] = False
        elif not data:
            logger.warning(f"[{endpoint_name}] Empty response — cannot validate schema")
            result["valid"] = False

    # ── 2. Check required deep paths ──
    deep_paths = schema.get("required_deep_paths", [])
    for path in deep_paths:
        exists, _ = _resolve_path(data, path)
        if not exists:
            result["missing_paths"].append(path)
            result["valid"] = False

    # ── 3. Detect new fields (per-API known_fields.json) ──
    known_fields_file = _known_fields_path(endpoint_name)
    known_fields = set(_load_json(known_fields_file, default=[]))
    current_fields = _extract_all_field_paths(data)

    if known_fields:
        new_fields = current_fields - known_fields
        if new_fields:
            result["new_fields"] = sorted(new_fields)
        removed_fields = known_fields - current_fields
        if removed_fields:
            result["removed_fields"] = sorted(removed_fields)
    # else: first run, everything is "new" but don't flag

    # Update per-API known fields (always save current state)
    _save_json(known_fields_file, sorted(current_fields))

    # ── Log results ──
    if result["missing_fields"]:
        logger.warning(f"[{endpoint_name}] Missing required fields: {result['missing_fields']}")
    if result["missing_paths"]:
        logger.warning(f"[{endpoint_name}] Missing deep paths: {result['missing_paths']}")
    if result["new_fields"]:
        logger.info(f"[{endpoint_name}] New fields detected: {result['new_fields']}")
    if result["removed_fields"]:
        logger.warning(f"[{endpoint_name}] Fields removed: {result['removed_fields']}")

    return result


# ── Change log + Slack ────────────────────────────────────────────────────


def log_change(endpoint_name: str, event_type: str, details: dict) -> None:
    """
    Append a change entry to the centralized change log.

    Args:
        endpoint_name: Which API endpoint changed
        event_type: One of "value_change", "new_fields", "missing_fields",
                    "baseline", "contract_violation"
        details: Extra info (old_hash, new_hash, fields list, errors, etc.)
    """
    change_log = _load_json(CHANGE_LOG_FILE, default=[])
    if not isinstance(change_log, list):
        change_log = []

    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "endpoint": endpoint_name,
        "type": event_type,
        "ci_run": os.environ.get("GITHUB_RUN_NUMBER", "local"),
        **details,
    }
    change_log.append(entry)

    if len(change_log) > CHANGE_LOG_MAX_ENTRIES:
        change_log = change_log[-CHANGE_LOG_MAX_ENTRIES:]

    _save_json(CHANGE_LOG_FILE, change_log)
    logger.info(f"Change logged: {endpoint_name} — {event_type}")


def notify_slack(message: str) -> None:
    """Send Slack notification. Non-blocking — failures are silently ignored."""
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return
    try:
        requests.post(webhook_url, json={"text": message}, timeout=5)
    except Exception:
        pass  # Non-blocking


# ── Fallback management (per-API folders) ─────────────────────────────────


def load_fallback(endpoint_name: str) -> dict | list | None:
    """Load cached fallback response from config/api_responses/<n>/response.json."""
    path = _fallback_path(endpoint_name)
    data = _load_json(path, default=None)
    if data is not None:
        logger.info(f"[{endpoint_name}] Using fallback response from {path}")
    return data


def save_fallback(endpoint_name: str, data: dict | list) -> None:
    """Save API response as fallback to config/api_responses/<n>/response.json."""
    path = _fallback_path(endpoint_name)
    _save_json(path, data)
    logger.info(f"[{endpoint_name}] Saved fallback response to {path}")


# ── JWT token via OTP login ───────────────────────────────────────────────


def _get_real_jwt_token(_base_url: str = "") -> str | None:
    """Obtain JWT for the standard test user (TEST_PHONE_NUMBER / TEST_OTP).

    Delegates to get_jwt_for_phone. The _base_url param is unused but kept
    for backward compatibility with existing callers.
    """
    return get_jwt_for_phone(os.getenv("TEST_PHONE_NUMBER", "5550000000"), DEFAULT_TEST_OTP)


def get_jwt_for_phone(phone: str, otp: str = DEFAULT_TEST_OTP) -> str | None:
    """Obtain a JWT token for a specific phone number via OTP login.

    Tokens are cached per-phone for the lifetime of the process.

    Args:
        phone: Phone number to login with.
        otp: OTP code. Defaults to TEST_OTP from the environment.

    Returns:
        JWT token string, or None if login fails.
    """
    cache_key = f"jwt_{phone}"
    with _jwt_lock:
        if cache_key in _jwt_token_cache:
            return _jwt_token_cache[cache_key]

    try:
        from src.utils.user_account_manager import UserAccountManager

        logger.info(f"Obtaining JWT token for {phone} via OTP login...")

        UserAccountManager.trigger_otp(phone)
        response = UserAccountManager.verify_otp(phone, otp)

        jwt_token = response.get("jwtToken", "")
        if jwt_token:
            with _jwt_lock:
                _jwt_token_cache[cache_key] = jwt_token
            logger.info(f"JWT token obtained for {phone}: {jwt_token[:20]}...")
            return jwt_token
        else:
            logger.warning(f"OTP verify succeeded for {phone} but no jwtToken in response")
            return None

    except Exception as e:
        logger.warning(f"Failed to obtain JWT token for {phone}: {e}")
        return None


# ── Dot-path resolution ───────────────────────────────────────────────────


def _resolve_dot_path(data, dot_path: str):
    """Resolve a dot-notation path against nested data.

    Examples:
        _resolve_dot_path(data, "reportTemplates.0.id") → data["reportTemplates"][0]["id"]
        _resolve_dot_path(data, "name") → data["name"]
    """
    current = data
    for part in dot_path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (IndexError, ValueError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


# ── Single endpoint fetch ─────────────────────────────────────────────────


def _fetch_endpoint(
    endpoint_name: str,
    endpoint_config: dict,
    base_url: str,
    headers: dict,
    hashes: dict,
) -> dict | list | None:
    """Fetch a single endpoint, validate schema + contract, handle fallback."""
    path = endpoint_config["path"]
    method = endpoint_config.get("method", "GET").upper()

    # Resolve path params: /reports/template/{templateId}/detail → /reports/template/18/detail
    for key, val in endpoint_config.get("path_params", {}).items():
        path = path.replace(f"{{{key}}}", str(val))

    url = f"{base_url}{path}"
    response_key = endpoint_config.get("response_key", "")
    body = endpoint_config.get("body")

    # Resolve dynamic placeholders in body (e.g. {today_date}, {first_tag})
    if body is not None:
        body_str = json.dumps(body)
        if "{today_date}" in body_str:
            body_str = body_str.replace("{today_date}", datetime.now().strftime("%Y-%m-%d"))
        # Resolve body_params from dependency data (e.g. {first_tag} → "Yearly")
        for key, val in endpoint_config.get("body_params", {}).items():
            body_str = body_str.replace(f"{{{key}}}", str(val))
        body = json.loads(body_str)

    try:
        # Merge per-endpoint extra headers (e.g., relationshipId)
        merged_headers = {**headers, **endpoint_config.get("extra_headers", {})}
        logger.info(f"[{endpoint_name}] Fetching {method} {url}")
        kwargs: dict = {"headers": merged_headers, "timeout": 15}
        if body is not None and method in ("POST", "PUT", "PATCH"):
            kwargs["json"] = body

        # Retry: 3 on CI (flaky network), 1 locally (fast fail)
        max_retries = 3 if os.environ.get("CI") else 1
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.request(method, url, **kwargs)
                response.raise_for_status()
                break
            except requests.exceptions.RequestException as retry_err:
                last_error = retry_err
                if attempt < max_retries:
                    wait_secs = attempt * 2  # 2s, 4s
                    logger.warning(
                        f"[{endpoint_name}] Attempt {attempt}/{max_retries} failed: {retry_err}. "
                        f"Retrying in {wait_secs}s..."
                    )
                    time.sleep(wait_secs)
                else:
                    # `from last_error` keeps the original traceback rather
                    # than making it look like a failure inside the handler.
                    raise last_error from last_error

        full_response = response.json()

        # ── Extract nested key FIRST (before hashing) ──
        # Hash only the actual data, not the response wrapper which may
        # contain timestamps/session IDs that change every call.
        data = full_response
        if response_key and isinstance(full_response, dict):
            data = full_response.get(response_key, full_response)
            logger.info(f"[{endpoint_name}] Extracted '{response_key}' from response")

        # ── Hash extracted data for change detection ──
        new_hash = compute_hash(data)
        old_hash = hashes.get(endpoint_name, {}).get("hash", "")
        now = datetime.now(UTC).isoformat()

        if old_hash and old_hash != new_hash:
            logger.info(
                f"[{endpoint_name}] Data changed (hash: {old_hash[:12]}... → {new_hash[:12]}...)"
            )
            log_change(
                endpoint_name,
                "value_change",
                {
                    "old_hash": old_hash[:16] + "...",
                    "new_hash": new_hash[:16] + "...",
                },
            )
            hashes[endpoint_name] = {
                "hash": new_hash,
                "last_checked": now,
                "last_changed": now,
                "change_count": hashes.get(endpoint_name, {}).get("change_count", 0) + 1,
            }
        elif not old_hash:
            logger.info(f"[{endpoint_name}] Baseline hash established")
            log_change(endpoint_name, "baseline", {"hash": new_hash[:16] + "..."})
            hashes[endpoint_name] = {
                "hash": new_hash,
                "last_checked": now,
                "last_changed": now,
                "change_count": 0,
            }
        else:
            hashes[endpoint_name]["last_checked"] = now
            logger.debug(f"[{endpoint_name}] No change")

        # Empty list protection: preserve existing fallback
        if isinstance(data, list) and len(data) == 0:
            logger.warning(f"[{endpoint_name}] API returned empty list, using fallback")
            return load_fallback(endpoint_name)

        # ── Schema validation (reads from per-API schema.json) ──
        schema_result = validate_schema(endpoint_name, data)

        if not schema_result["valid"]:
            all_missing = schema_result["missing_fields"] + schema_result["missing_paths"]
            logger.warning(f"[{endpoint_name}] Schema validation FAILED: {all_missing}")
            log_change(endpoint_name, "missing_fields", {"fields": all_missing})

            fallback = load_fallback(endpoint_name)
            if fallback:
                logger.info(f"[{endpoint_name}] Using fallback due to schema failure")
                return fallback
            else:
                logger.warning(
                    f"[{endpoint_name}] No fallback available, returning data as-is "
                    f"(transform may crash)"
                )

        # ── Log new / removed fields ──
        api_path = endpoint_config.get("path", "")
        if schema_result["new_fields"]:
            log_change(
                endpoint_name,
                "new_fields",
                {
                    "api_path": api_path,
                    "fields": schema_result["new_fields"],
                },
            )
        if schema_result.get("removed_fields"):
            log_change(
                endpoint_name,
                "removed_fields",
                {
                    "api_path": api_path,
                    "fields": schema_result["removed_fields"],
                },
            )

        # ── Pydantic contract validation ──────────────────────────────────────
        # Runs AFTER schema.json checks (which catch missing fields/paths).
        # Catches what schema.json misses: type changes, renames not in schema,
        # nested model structure violations.
        # Does NOT trigger fallback — violations are LOGGED so you see the real
        # backend problem instead of silently running tests on stale data.
        # ─────────────────────────────────────────────────────────────────────
        if os.environ.get("DISABLE_API_CONTRACTS", "").lower() not in ("true", "1", "yes"):
            try:
                from src.utils.api_contract_validator import validate_contract

                contract_result = validate_contract(endpoint_name, data)
                if not contract_result.valid and contract_result.summary not in (
                    "no_contract",
                    "null_data",
                    "empty_list",
                ):
                    log_change(
                        endpoint_name,
                        "contract_violation",
                        {
                            "error_count": contract_result.error_count,
                            "errors": contract_result.errors[:10],
                            "summary": contract_result.summary,
                        },
                    )
                    # Don't fall back — preserve the real data so transforms can
                    # still run (partial results are better than stale ones).
                    # conftest.backend_data fixture will surface violations before tests.
            except Exception as contract_exc:
                # Never let contract validation break the fetch itself
                logger.warning(
                    f"[{endpoint_name}] Contract validation error (non-fatal): {contract_exc}"
                )
        # ─────────────────────────────────────────────────────────────────────

        # Save fallback (only when schema valid — don't overwrite good with bad)
        if schema_result["valid"]:
            save_fallback(endpoint_name, data)

        return data

    except requests.exceptions.RequestException as e:
        logger.warning(f"[{endpoint_name}] API request failed: {e}")
        return load_fallback(endpoint_name)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"[{endpoint_name}] Invalid response: {e}")
        return load_fallback(endpoint_name)


# ── Shared config helpers ─────────────────────────────────────────────────


def _build_headers(config: dict, token: str) -> dict:
    """Build request headers with auth token from api_endpoints.json config."""
    auth_config = config.get("auth", {})
    headers = dict(auth_config.get("headers", {}))
    if token:
        auth_type = auth_config.get("type", "bearer")
        if auth_type == "custom_header":
            header_name = auth_config.get("token_header", "auth_token")
            headers[header_name] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
    return headers


# ── Per-user endpoint fetch ───────────────────────────────────────────────


def fetch_endpoint_for_user(
    endpoint_name: str,
    jwt_token: str,
    body_override: dict | None = None,
) -> dict | list | None:
    """Fetch a single endpoint using a specific user's JWT token.

    Reads endpoint config from api_endpoints.json, uses the same base URL
    and headers but overrides the auth token with the provided JWT.
    Skips hash tracking, schema validation, and fallback saving.

    Args:
        endpoint_name: Key from api_endpoints.json (e.g., "deposit_suggestions")
        jwt_token: Real JWT token for the target user
        body_override: Optional custom body to replace the config body
            (e.g., Delhi coordinates for new-user Almanac).

    Returns:
        API response data (parsed JSON), or None on failure.
    """
    config = _load_json(ENDPOINTS_FILE)
    if not config:
        logger.warning(f"No endpoint config found at {ENDPOINTS_FILE}")
        return None

    ep_config = config.get("endpoints", {}).get(endpoint_name)
    if not ep_config:
        logger.warning(f"Endpoint '{endpoint_name}' not found in config")
        return None

    base_url = os.getenv(config.get("base_url_env", ""), "")
    if not base_url:
        logger.error("No API base URL configured")
        return None

    headers = _build_headers(config, jwt_token)
    path = ep_config["path"]
    method = ep_config.get("method", "GET").upper()
    body = body_override if body_override is not None else ep_config.get("body")

    try:
        kwargs: dict = {"headers": headers, "timeout": 10}
        if body is not None and method in ("POST", "PUT", "PATCH"):
            kwargs["json"] = body
        response = requests.request(method, f"{base_url}{path}", **kwargs)
        response.raise_for_status()
        data = response.json()

        response_key = ep_config.get("response_key", "")
        if response_key and isinstance(data, dict):
            data = data.get(response_key, data)

        logger.info(f"[{endpoint_name}] Fetched for user (JWT ...{jwt_token[-8:]})")
        return data

    except Exception as e:
        logger.warning(f"[{endpoint_name}] Per-user fetch failed: {e}")
        return None


# ── Main entry point ──────────────────────────────────────────────────────


def fetch_single_endpoint(endpoint_name: str) -> dict | list | None:
    """Fetch a single endpoint by name. Used by tests that need on-demand data."""
    config = _load_json(ENDPOINTS_FILE)
    if not config:
        return None
    ep_config = config.get("endpoints", {}).get(endpoint_name)
    if not ep_config:
        logger.warning(f"Endpoint '{endpoint_name}' not found in config")
        return None
    base_url_env = config.get("base_url_env", "")
    base_url = os.getenv(base_url_env, "")
    if not base_url:
        return None
    auth_config = config.get("auth", {})
    token = os.getenv(auth_config.get("token_env", ""), "")
    if not token:
        real_jwt = _get_real_jwt_token(base_url)
        if real_jwt:
            token = real_jwt
    headers = _build_headers(config, token)
    hashes = _load_json(HASHES_FILE, default={})
    result = _fetch_endpoint(endpoint_name, ep_config, base_url, headers, hashes)
    _save_json(HASHES_FILE, hashes)
    return result


def fetch_all_endpoints(session_only: bool = True) -> dict[str, dict | list | None]:
    """
    Fetch configured API endpoints and return their data.

    Args:
        session_only: If True (default), only fetch endpoints with "session_fetch": true.
            Set to False to fetch all endpoints (used by CI full suite).

    This is the main entry point called by the pytest session fixture.

    For each endpoint:
    1. Call API
    2. Hash FULL response for change detection
    3. Extract response_key if configured
    4. Validate schema (from per-API schema.json)
    5. Detect new fields (from per-API known_fields.json)
    6. Pydantic contract validation (from src/data/models/api_contracts.py)
    7. Save fallback (to per-API response.json, only if schema valid)
    8. Log changes and notify Slack

    Returns:
        Dict keyed by endpoint name, values are API response data.
        If an endpoint fails and no fallback exists, its value is None.
    """
    config = _load_json(ENDPOINTS_FILE)
    if not config:
        logger.warning(f"No endpoint config found at {ENDPOINTS_FILE}")
        return {}

    # Resolve base URL
    base_url_env = config.get("base_url_env", "")
    base_url = os.getenv(base_url_env, "")
    if not base_url:
        logger.error("No API base URL configured")
        return {}

    # Resolve auth token
    auth_config = config.get("auth", {})
    token_env = auth_config.get("token_env", "")
    token = os.getenv(token_env, "") if token_env else ""

    # No token configured: fall back to an OTP login for a real JWT.
    if not token:
        real_jwt = _get_real_jwt_token(base_url)
        if real_jwt:
            token = real_jwt
            logger.info("Using real JWT token from OTP login")
        elif not token:
            logger.warning("No API token available (env empty, OTP login failed)")

    headers = _build_headers(config, token)

    # Load existing hashes (centralized)
    hashes = _load_json(HASHES_FILE, default={})

    # Fetch endpoints in two passes: independent first (parallel), then dependent
    from concurrent.futures import ThreadPoolExecutor, as_completed
    # Thread safety: each thread writes to hashes[endpoint_name] — unique key per thread.
    # Python dict key assignment is atomic under GIL, no lock needed.

    all_endpoints = config.get("endpoints", {})
    # Filter to session_fetch=true endpoints unless fetching all
    if session_only:
        endpoints = {
            name: ep_config
            for name, ep_config in all_endpoints.items()
            if ep_config.get("session_fetch", False)
        }
        logger.info(f"Session-only mode: fetching {len(endpoints)}/{len(all_endpoints)} endpoints")
    else:
        endpoints = all_endpoints
    results = {}

    # Pass 1: Endpoints with no dependencies — fetch in PARALLEL
    independent = {
        name: ep_config for name, ep_config in endpoints.items() if "depends_on" not in ep_config
    }
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(_fetch_endpoint, name, ep_config, base_url, headers, hashes): name
            for name, ep_config in independent.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                logger.warning(f"[{name}] Parallel fetch failed: {e}")
                results[name] = None

    # Pass 2: Endpoints with depends_on — resolve path_params from prior results
    for name, ep_config in endpoints.items():
        if "depends_on" in ep_config:
            dep_name = ep_config["depends_on"]
            dep_data = results.get(dep_name)
            if dep_data is None:
                logger.warning(f"[{name}] Dependency '{dep_name}' returned no data, skipping")
                results[name] = None
                continue
            # Resolve path_params_from and body_params_from against dependency data
            resolved_path = {}
            for param_key, dot_path in ep_config.get("path_params_from", {}).items():
                val = _resolve_dot_path(dep_data, dot_path)
                if val is not None:
                    resolved_path[param_key] = val
                else:
                    logger.warning(f"[{name}] Could not resolve path '{dot_path}' from {dep_name}")
            resolved_body = {}
            for param_key, dot_path in ep_config.get("body_params_from", {}).items():
                val = _resolve_dot_path(dep_data, dot_path)
                if val is not None:
                    resolved_body[param_key] = val
                else:
                    logger.warning(f"[{name}] Could not resolve body '{dot_path}' from {dep_name}")
            ep_config_copy = {
                **ep_config,
                "path_params": resolved_path,
                "body_params": resolved_body,
            }
            results[name] = _fetch_endpoint(name, ep_config_copy, base_url, headers, hashes)

    # Save updated hashes (centralized)
    _save_json(HASHES_FILE, hashes)

    # Slack notification if changes detected this run
    recent_log = _load_json(CHANGE_LOG_FILE, default=[])
    if isinstance(recent_log, list):
        ci_run = os.environ.get("GITHUB_RUN_NUMBER", "local")
        _ = [e for e in recent_log if e.get("ci_run") == ci_run and e.get("type") != "baseline"]
        # API monitor Slack notification disabled — test failures already
        # surface mismatches via verify_*_from_api(). Uncomment if needed.
        # if this_run_changes:
        #     lines = []
        #     for c in this_run_changes:
        #         ctype = c.get("type", "unknown")
        #         ep = c.get("endpoint", "?")
        #         if ctype == "value_change":
        #             lines.append(f"{ep}: values changed")
        #         elif ctype == "new_fields":
        #             lines.append(f"{ep}: new fields {c.get('fields', [])}")
        #         elif ctype == "missing_fields":
        #             lines.append(f"{ep}: missing fields {c.get('fields', [])}")
        #         elif ctype == "contract_violation":
        #             lines.append(f"{ep}: contract violated {c.get('errors', [])[:2]}")
        #     if lines:
        #         notify_slack("the app API Monitor:\n" + "\n".join(lines))

    logger.info(f"API fetch complete: {len(results)}/{len(endpoints)} endpoints loaded")
    return results
