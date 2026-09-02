"""
pytest configuration and fixtures for mobile test automation
"""

import faulthandler
import json
import logging
import os
import re
import sys
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ========== CRITICAL: Load .env BEFORE any other imports ==========
# This ensures IMPLICIT_WAIT_SECONDS, EXPLICIT_WAIT_SECONDS are available
# when appium_client.py and base_page.py are imported
from dotenv import load_dotenv

load_dotenv()  # Load .env into os.environ

import allure  # noqa: E402
import pytest  # noqa: E402

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent))

from src.data.parsers.config_manager import ConfigManager  # noqa: E402
from src.reporting.allure_enhancements import setup_allure_metadata  # noqa: E402
from src.reporting.screen_timing import (  # noqa: E402
    end_timing_session,
    start_timing_session,
)
from src.storage.postgres_storage import PostgresStorage  # noqa: E402

# Configure logging
# Ensure logs directory exists
Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(f"logs/test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
        logging.StreamHandler(),
    ],
)

# Suppress noisy urllib3 connection pool warnings (clutters Allure reports)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

_worker_fault_log = None
_worker_fault_log_path: Path | None = None


def _current_worker_id(config) -> str:
    workerinput = getattr(config, "workerinput", None)
    if workerinput:
        return workerinput.get("workerid", "worker")
    return "controller"


def _install_worker_crash_diagnostics(config) -> None:
    """Enable low-level crash diagnostics for each pytest process."""
    global _worker_fault_log, _worker_fault_log_path

    worker_id = _current_worker_id(config)
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    _worker_fault_log_path = logs_dir / f"{worker_id}_crash_diagnostics.log"
    _worker_fault_log = open(_worker_fault_log_path, "a", buffering=1, encoding="utf-8")

    try:
        faulthandler.enable(file=_worker_fault_log, all_threads=True)
    except Exception as e:
        logging.warning(f"Failed to enable faulthandler for {worker_id}: {e}")

    def _thread_excepthook(args):
        _worker_fault_log.write(
            f"\n[{datetime.now(UTC).isoformat()}] Unhandled thread exception "
            f"in {args.thread.name}\n"
        )
        traceback.print_exception(
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
            file=_worker_fault_log,
        )
        _worker_fault_log.flush()

    threading.excepthook = _thread_excepthook
    _worker_fault_log.write(
        f"\n[{datetime.now(UTC).isoformat()}] Crash diagnostics enabled for {worker_id} "
        f"(pid={os.getpid()})\n"
    )
    _worker_fault_log.flush()


def _write_worker_breadcrumb(config, phase: str, nodeid: str) -> None:
    """Persist the last active test so worker crashes leave context behind."""
    worker_id = _current_worker_id(config)
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    breadcrumb = logs_dir / f"{worker_id}_active_test.txt"
    breadcrumb.write_text(
        f"time={datetime.now(UTC).isoformat()}\nphase={phase}\nnodeid={nodeid}\npid={os.getpid()}\n",
        encoding="utf-8",
    )
    if _worker_fault_log:
        _worker_fault_log.write(
            f"[{datetime.now(UTC).isoformat()}] {phase}: {nodeid} (pid={os.getpid()})\n"
        )
        _worker_fault_log.flush()


# ── Cached device name resolution (avoids re-parsing config per test) ──
_device_display_cache: dict[str, str] = {}


def _resolve_device_display(device_id: str) -> str:
    """Resolve symbolic device_id (e.g. 'device_1') to 'Name · Android ver'.

    Result is cached for the session lifetime so ConfigManager is only
    instantiated once per unique device_id value.
    """
    if device_id in _device_display_cache:
        return _device_display_cache[device_id]
    display = device_id
    try:
        dev = ConfigManager().get(f"devices.android.{device_id}", {})
        if isinstance(dev, dict) and dev.get("name"):
            display = f"{dev['name']} · Android {dev.get('platform_version', '?')}"
    except Exception:
        pass
    _device_display_cache[device_id] = display
    return display


def pytest_addoption(parser):
    """Add custom command line options"""
    group = parser.getgroup("app")

    # Run mode options
    group.addoption(
        "--run-mode", choices=["local", "ci"], default="local", help="Execution mode (local or CI)"
    )

    # Platform options
    group.addoption(
        "--platform",
        choices=["android", "ios", "web"],
        default="android",
        help="Target platform (default: android)",
    )

    # Device options
    group.addoption(
        "--device-provider",
        choices=["local", "emulator", "simulator", "farm"],
        default="local",
        help="Device provider type",
    )

    group.addoption("--device-id", help="Specific device ID to use")

    # Test data options
    group.addoption("--data-csv", help="CSV file with test data")

    # Execution options
    group.addoption("--parallel", type=int, default=1, help="Number of parallel executions")

    group.addoption("--retry", type=int, default=0, help="Number of retries for failed tests")

    # Configuration options
    group.addoption(
        "--test-config",
        default=None,
        help="Test configuration file path (default: discovered, see ConfigManager)",
    )

    group.addoption("--test-env", default=".env", help="Test environment file path")


@pytest.fixture(scope="session")
def config(pytestconfig):
    """Session-scoped configuration fixture"""
    # FIX: Pass pytestconfig.option (where CLI args live) instead of pytestconfig
    return ConfigManager.from_cli_args(pytestconfig.option)


@pytest.fixture(scope="session")
def db_storage(config):
    """Session-scoped database storage fixture"""
    if config.get("postgres.write_results", False):
        storage = PostgresStorage(
            uri=config.get("postgres.uri"), schema=config.get("postgres.schema", "public")
        )
        yield storage
        storage.close()
    else:
        yield None


# Unused fixtures removed - all test data is now in Page Objects or passed directly


# ========================================
# BACKEND API DATA FIXTURES (opt-in)
# ========================================


def _notify_contract_violations(
    violations: list,
    new_fields: list,
    removed_fields: list,
    ci_run: str,
) -> None:
    """Send Slack alert to backend team for contract violations, new/removed fields.

    Uses BACKEND_WEBHOOK_URL (separate from test notification webhook).
    Reuses SlackNotifier.send_simple_message() for consistent error handling.
    Non-fatal: if webhook is missing or send fails, just log and continue.
    """
    webhook = os.environ.get("BACKEND_WEBHOOK_URL", "")
    if not webhook:
        logger.debug("BACKEND_WEBHOOK_URL not set — skipping contract Slack alert")
        return

    try:
        from src.reporting.slack_notifier import SlackNotifier

        sections = []

        # Violations section
        if violations:
            violation_lines = []
            for v in violations:
                endpoint = v.get("endpoint", "?")
                for err in v.get("errors", []):
                    field = err.get("field", "?")
                    msg = err.get("message", "?")
                    # Strip numeric indices — not useful for backend team
                    parts = [p.strip() for p in field.split("→")]
                    clean_field = " → ".join(p for p in parts if not p.isdigit())
                    if not clean_field:
                        clean_field = field
                    violation_lines.append(f"• `{endpoint}` → `{clean_field}`: {msg}")
            sections.append(
                "🚨 *Contract Violations*\n"
                + "\n".join(violation_lines[:20])
                + (
                    f"\n_...and {len(violation_lines) - 20} more_"
                    if len(violation_lines) > 20
                    else ""
                )
            )

        def _fmt_field_lines(entries: list, emoji: str) -> list[str]:
            lines = []
            for entry in entries:
                endpoint = entry.get("endpoint", "?")
                api_path = entry.get("api_path", "")
                fields = entry.get("fields", [])
                path_info = f" (`{api_path}`)" if api_path else ""
                lines.append(
                    f"• `{endpoint}`{path_info}:\n"
                    + "\n".join(f"  {emoji} `{f}`" for f in fields[:10])
                )
            return lines

        if new_fields:
            lines = _fmt_field_lines(new_fields, "➕")
            sections.append("ℹ️ *New Fields Detected (Added by Backend)*\n" + "\n".join(lines[:10]))

        if removed_fields:
            lines = _fmt_field_lines(removed_fields, "➖")
            sections.append("⚠️ *Fields Removed (Deleted by Backend)*\n" + "\n".join(lines[:10]))

        text = (
            f"*API Contract Report* — CI Run `{ci_run}`\n\n"
            + "\n\n".join(sections)
            + "\n\n_Update `src/data/models/api_contracts.py` if changes are intentional._"
        )

        notifier = SlackNotifier(webhook_url=webhook, enabled=True)
        if notifier.send_simple_message(text):
            logger.info("Contract Slack alert sent to backend channel")
        else:
            logger.warning("Contract Slack alert failed to send")
    except Exception as e:
        logger.warning(f"Failed to send contract Slack alert: {e}")


# ── Prefetch backend data in background thread during Appium session creation ──
_prefetch_result: dict = {}
_prefetch_thread = None


def _prefetch_backend_data():
    """Run in background thread to fetch API data while Appium session starts."""
    from src.utils.api_fetcher import fetch_all_endpoints

    try:
        _prefetch_result["data"] = fetch_all_endpoints(session_only=True)
        _prefetch_result["timestamp"] = datetime.now(UTC).isoformat()
        logger.info("⚡ Backend data prefetched in background thread")
    except Exception as e:
        logger.warning(f"⚠️ Background prefetch failed: {e}")
        _prefetch_result["data"] = None


def _start_backend_prefetch():
    """Start background thread if not already running."""
    global _prefetch_thread
    import threading

    if _prefetch_thread is None:
        _prefetch_thread = threading.Thread(target=_prefetch_backend_data, daemon=True)
        _prefetch_thread.start()
        logger.info("⚡ Started backend data prefetch thread")


@pytest.fixture(scope="session")
def backend_data():
    """Fetch current backend data once per test session (~3s).

    Returns dict keyed by endpoint name (e.g. "subscription_plans").
    Falls back to cached JSON if API is unreachable.
    NOT autouse — only injected when a test requests it (or expected_screens).

    Optimization: if _start_backend_prefetch() was called earlier (e.g. during
    Appium session creation), this fixture reuses the prefetched result instead
    of blocking for another ~3s.

    After fetching, checks change_log.json for any contract_violation entries
    logged during this run and surfaces them as clear warnings (or hard failures
    in CI / when STRICT_API_CONTRACTS=true).

    This means if the backend changes planAmount → amount, you see:
        WARNING: [subscription_plans] CONTRACT VIOLATION
          planAmount: Field required
    BEFORE any test assertions run — not a mysterious "₹0/Week" failure.
    """
    from src.utils.api_fetcher import CHANGE_LOG_FILE, _load_json, fetch_all_endpoints

    # Only fetch session_fetch=true endpoints (3 essential config APIs)
    # Tests that need more data fetch it mid-test with real JWT tokens
    session_only = True

    # Reuse prefetched data if background thread completed (or wait briefly)
    global _prefetch_thread
    if _prefetch_thread is not None:
        _prefetch_thread.join(timeout=5)
    if _prefetch_result.get("data"):
        data = _prefetch_result["data"]
        session_start = _prefetch_result.get("timestamp", datetime.now(UTC).isoformat())
        logger.info(f"⚡ Using prefetched backend data: {list(data.keys())}")
    else:
        session_start = datetime.now(UTC).isoformat()
        data = fetch_all_endpoints(session_only=session_only)
        logger.info(f"Backend data fetched for endpoints: {list(data.keys())}")

    # ── Surface contract violations logged during THIS session ────────────────
    # Filter by timestamp >= session_start so we only pick up entries from
    # this pytest run, not stale "local" entries from previous runs.
    # ─────────────────────────────────────────────────────────────────────────
    recent_log = _load_json(CHANGE_LOG_FILE, default=[])
    ci_run = os.environ.get("GITHUB_RUN_NUMBER", "local")

    log_entries = [
        e
        for e in (recent_log if isinstance(recent_log, list) else [])
        if e.get("ci_run") == ci_run and e.get("timestamp", "") >= session_start
    ]
    violations = [e for e in log_entries if e.get("type") == "contract_violation"]
    new_fields = [e for e in log_entries if e.get("type") == "new_fields"]
    removed_fields = [e for e in log_entries if e.get("type") == "removed_fields"]

    # Notify backend team about new fields (informational, not a failure)
    if new_fields:
        logger.info("=" * 70)
        logger.info("ℹ️  NEW BACKEND FIELDS DETECTED")
        for nf in new_fields:
            endpoint = nf.get("endpoint", "?")
            fields = nf.get("fields", [])
            logger.info(f"  [{endpoint}] new fields: {fields}")
        logger.info("=" * 70)

    if removed_fields:
        logger.warning("=" * 70)
        logger.warning("⚠️  BACKEND FIELDS REMOVED")
        for rf in removed_fields:
            endpoint = rf.get("endpoint", "?")
            fields = rf.get("fields", [])
            logger.warning(f"  [{endpoint}] removed fields: {fields}")
        logger.warning("=" * 70)

    # Send Slack for violations, new fields, and removed fields
    if violations or new_fields or removed_fields:
        _notify_contract_violations(violations, new_fields, removed_fields, ci_run)

    if violations:
        logger.warning("=" * 70)
        logger.warning("⚠️  BACKEND API CONTRACT VIOLATIONS DETECTED")
        logger.warning("   Backend changes may cause frontend automation to use wrong values.")
        logger.warning("=" * 70)
        for v in violations:
            endpoint = v.get("endpoint", "?")
            errors = v.get("errors", [])
            logger.warning(f"\n  [{endpoint}] {len(errors)} violation(s):")
            for err in errors:
                logger.warning(f"    field:   {err.get('field', '?')}")
                logger.warning(f"    problem: {err.get('message', '?')}  [{err.get('type', '?')}]")
        logger.warning("=" * 70)
        logger.warning("  Check config/change_log.json for full details.")
        logger.warning(
            "  Update src/data/models/api_contracts.py if this is an intentional change."
        )
        logger.warning("=" * 70)

        # Hard fail in CI or when STRICT_API_CONTRACTS=true
        strict = os.environ.get("STRICT_API_CONTRACTS", "").lower() in ("true", "1", "yes")
        if os.environ.get("CI") or strict:
            violated_endpoints = sorted({v.get("endpoint", "?") for v in violations})
            pytest.fail(
                f"Backend API contract(s) broken: {violated_endpoints}\n"
                f"See WARNING lines above or config/change_log.json for field-level details.\n"
                f"Fix: update src/data/models/api_contracts.py to match new backend shape."
            )

    # Configure tab names from API (all page objects inherit via BasePage)
    from src.pages.base_page import BasePage

    client_config = data.get("client_config", {})
    tab_nav = client_config.get("tabNavigationConfigV2", {})
    tabs = tab_nav.get("tabs", [])
    if tabs:
        BasePage.configure_tab_names(tabs)

    return data


@pytest.fixture(scope="session")
def expected_screens(backend_data):
    """Transform backend data into expected screen text values.

    Returns dict keyed by screen name (e.g. "add_cash", "home", "reports").
    Each value is a dict of expected text for that screen.
    NOT autouse — only injected when a test requests it.
    """
    # Transforms are project specific: they turn YOUR backend payloads into the
    # text a screen should show. Supply a module exposing get_all_expected()
    # and import it here.
    try:
        from src.utils.transforms import get_all_expected
    except ImportError:
        pytest.skip(
            "No transforms module found. Provide src/utils/transforms with "
            "get_all_expected(backend_data) to use the expected_screens fixture."
        )

    screens = get_all_expected(backend_data)
    populated = {k: v for k, v in screens.items() if v}
    logger.info(f"Expected screen values built for: {list(populated.keys())}")
    return screens


@pytest.fixture(scope="session")
def platform(pytestconfig):
    """Platform fixture (session-scoped — platform doesn't change mid-suite)"""
    return pytestconfig.getoption("platform")


@pytest.fixture(scope="session")
def device_id(request, pytestconfig):
    """
    Device ID fixture with parametrization support (session-scoped).

    When parametrized by pytest_generate_tests, uses the parametrized value.
    Otherwise falls back to CLI arg or env var.

    Session-scoped: one device per session (driver reuse across tests).
    """
    # Web platform doesn't use devices
    if pytestconfig.getoption("platform") == "web":
        return None
    import os

    # Check if parametrized (via pytest_generate_tests)
    if hasattr(request, "param"):
        return request.param

    # Fallback to CLI or env var
    cli_device = pytestconfig.getoption("device_id")
    return cli_device if cli_device else os.getenv("DEVICE_ID_PRIMARY")


# ========================================
# APPIUM FIXTURES (Replacement for Maestro)
# ========================================


@pytest.fixture(scope="session", autouse=True)
def appium_server(request):
    """
    Automatically start and stop Appium server for test session.

    Features:
    - Auto-starts Appium if not already running
    - Captures server logs to logs/appium.log
    - Waits for server readiness before tests
    - Auto-stops server after tests complete
    - Graceful handling if server already running
    - pytest-xdist safe: Only controller manages server, workers connect

    Configuration via environment variables:
    - APPIUM_PORT: Server port (default: 4723)
    - APPIUM_HOST: Server host (default: 127.0.0.1)
    - APPIUM_AUTO_START: Enable/disable auto-start (default: true)

    Note: If you prefer manual Appium management, set:
        export APPIUM_AUTO_START=false
    """
    # Skip Appium server for web platform tests (Playwright handles its own browser)
    if request.config.getoption("platform") == "web":
        yield
        return
    import os
    import subprocess
    import time
    from pathlib import Path

    import requests

    # Skip local Appium server management when using cloud provider (LambdaTest)
    # Cloud tests connect directly to the provider's hub URL - no local server needed
    provider = ConfigManager().get("provider.name", "local")
    if provider == "lambdatest":
        logging.info("☁️  Cloud provider (LambdaTest) — skipping local Appium server management")
        yield
        return

    # No Appium binary on PATH. Nothing below can manage a server, and the unit
    # tests never open a session, so yield instead of failing: `pytest tests/unit`
    # has to work on a clean checkout and on CI, neither of which installs Appium.
    import shutil

    if shutil.which("appium") is None:
        logging.info("appium not on PATH — skipping local Appium server management")
        yield
        return

    # FIX: pytest-xdist safety - only controller manages server
    # Workers should just wait for the server to be ready
    if hasattr(request.config, "workerinput"):
        # This is a pytest-xdist worker - don't start server, just wait for it
        appium_port = os.getenv("APPIUM_PORT", "4723")
        appium_host = os.getenv("APPIUM_HOST", "127.0.0.1")
        appium_url = f"http://{appium_host}:{appium_port}/status"

        # Wait for controller to start the server
        max_wait = 60  # Give controller time to start server
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                response = requests.get(appium_url, timeout=2)
                if response.status_code == 200:
                    logging.info(f"✅ [Worker] Appium server ready at {appium_url}")
                    yield
                    return
            except requests.exceptions.RequestException:
                # Polling interval for server readiness check
                # NOTE: This is infrastructure wait, not test wait - acceptable here
                time.sleep(2)  # Recheck every 2 seconds

        raise RuntimeError(f"❌ [Worker] Appium server not available at {appium_url}")

    # Controller process - manage the server
    # Configuration
    auto_start = os.getenv("APPIUM_AUTO_START", "true").lower() == "true"
    appium_port = os.getenv("APPIUM_PORT", "4723")
    appium_host = os.getenv("APPIUM_HOST", "127.0.0.1")
    appium_url = f"http://{appium_host}:{appium_port}/status"

    # Check if auto-start is disabled
    if not auto_start:
        logging.info("⚙️  Appium auto-start disabled (APPIUM_AUTO_START=false)")
        logging.info(f"   Expecting manual Appium server at: {appium_url}")
        yield
        return

    # Function to check if Appium is already running
    def is_appium_running():
        try:
            response = requests.get(appium_url, timeout=2)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False

    # Check if Appium is already running
    if is_appium_running():
        logging.info(f"✅ Appium server already running at {appium_url}")
        # Start backend data prefetch while Appium session is being created
        _start_backend_prefetch()
        yield
        return

    # Start Appium server
    logging.info("🚀 Starting Appium server automatically...")
    logging.info(f"   Port: {appium_port}")
    logging.info(f"   Host: {appium_host}")

    # Create logs directory
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    log_file_path = logs_dir / "appium.log"

    # Start Appium as subprocess
    log_file = open(log_file_path, "w")
    process = None
    try:
        process = subprocess.Popen(
            ["appium", "-p", appium_port, "--log-timestamp"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )

        logging.info(f"   PID: {process.pid}")
        logging.info(f"   Logs: {log_file_path}")

        # Wait for server to be ready
        max_wait = 30  # seconds
        start_time = time.time()
        server_ready = False

        while time.time() - start_time < max_wait:
            if is_appium_running():
                server_ready = True
                elapsed = time.time() - start_time
                logging.info(f"✅ Appium server ready in {elapsed:.1f}s")
                # Start backend data prefetch while Appium session is being created
                _start_backend_prefetch()
                break

            # Polling interval for server startup check
            # NOTE: This is infrastructure wait, not test wait - acceptable here
            time.sleep(1)  # Recheck every 1 second

        if not server_ready:
            process.terminate()
            log_file.close()
            raise RuntimeError(
                f"❌ Appium server failed to start within {max_wait}s. "
                f"Check logs at: {log_file_path}"
            )

        # Server is ready, run tests
        yield

    finally:
        # Cleanup: Stop Appium server
        logging.info("🛑 Stopping Appium server...")
        try:
            if process is not None:
                process.terminate()
                process.wait(timeout=10)
                logging.info("✅ Appium server stopped gracefully")
        except subprocess.TimeoutExpired:
            logging.warning("⚠️  Appium server didn't stop gracefully, killing...")
            if process is not None:
                process.kill()
                process.wait()
        except Exception as e:
            logging.warning(f"⚠️  Error stopping Appium: {e}")
        finally:
            log_file.close()


@pytest.fixture(scope="session", autouse=True)
def disable_device_animations(pytestconfig):
    """
    ⚡ LAYER 1: Kill ALL Android system animations via ADB (runs ONCE per session).

    Sets window_animation_scale, transition_animation_scale, animator_duration_scale
    to 0 on the device. Persists until manually restored.

    This eliminates system-level animations (screen transitions, window open/close)
    that cause UiAutomator2 to wait for idle state. App-internal animations (React
    Native JS-side) are handled by Layer 3 (waitForIdleTimeout=0).

    No root required. Works on real devices and emulators.

    Also pre-grants notification permission to avoid permission popup on every test.
    """
    import os
    import subprocess

    platform = pytestconfig.getoption("platform")
    if platform != "android":
        yield
        return

    # Cloud: animations handled by capabilities (disableAnimation=True) — no ADB needed
    provider = ConfigManager().get("provider.name", "local")
    if provider == "lambdatest":
        logging.info("☁️  Cloud provider — animations disabled via capabilities (no ADB needed)")
        yield
        return

    device_id = pytestconfig.getoption("device_id") or os.getenv("DEVICE_ID_PRIMARY")
    adb_target = ["-s", device_id] if device_id else []

    animation_commands = [
        "settings put global window_animation_scale 0",
        "settings put global transition_animation_scale 0",
        "settings put global animator_duration_scale 0",
    ]

    logging.info("⚡ Disabling ALL device animations via ADB...")
    for cmd in animation_commands:
        try:
            subprocess.run(
                ["adb"] + adb_target + ["shell", cmd], capture_output=True, text=True, timeout=5
            )
        except Exception as e:
            logging.warning(f"⚠️  ADB animation command failed: {cmd} → {e}")

    logging.info("✅ Device animations disabled (window + transition + animator = 0)")

    # 🔔 Pre-grant notification permission to avoid popup on every test
    logging.info("🔔 Pre-granting notification permission...")
    try:
        subprocess.run(
            ["adb"]
            + adb_target
            + [
                "shell",
                "pm",
                "grant",
                "com.example.app",
                "android.permission.POST_NOTIFICATIONS",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        logging.info("✅ Notification permission granted")
    except Exception as e:
        logging.warning(f"⚠️  Could not pre-grant notification permission: {e}")

    yield

    # Restore animations after full session (optional — keeps device usable)
    logging.info("🔄 Restoring device animations to default...")
    for cmd in animation_commands:
        try:
            subprocess.run(
                ["adb"] + adb_target + ["shell", cmd.replace(" 0", " 1")],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            pass


@pytest.fixture(scope="session")
def appium_driver(request, platform, device_id):
    """
    Create Appium driver for the entire test session using DriverFactory.

    Session-scoped: ONE Appium session per suite, reused across all tests.
    This eliminates 5-10 min driver init overhead per test on LambdaTest.

    Test isolation is maintained by launch_app_with_clear_data() in each test.
    The ensure_driver_alive fixture (autouse, function-scoped) handles:
    - Session health checks before each test
    - LambdaTest session name updates for dashboard visibility
    - Automatic session re-initialization if driver dies
    """
    # Skip Appium driver for web platform tests
    if platform == "web":
        yield None
        return
    from src.platform.driver_factory import DriverFactory

    # Assign unique system port for parallel execution
    # Each device needs its own UIAutomator2 server port
    device_index = hash(device_id) % 100  # Generate index from device ID
    system_port = 8200 + device_index  # Base port 8200, unique per device

    # Stagger session starts for LOCAL parallel execution only.
    # Cloud (LambdaTest) has isolated Appium servers per device — no stagger needed.
    # The unique systemPort assignment above (line 415) prevents UIAutomator2 port conflicts.
    import time

    from src.data.parsers.config_manager import ConfigManager as _CM

    _provider = _CM().get("provider.name", "local")
    if hasattr(request.config, "workerinput") and _provider != "lambdatest":
        # Local only: stagger to avoid single Appium server overload
        worker_num = int(request.config.workerinput["workerid"].replace("gw", ""))
        stagger_delay = worker_num * 3  # 3 second stagger per worker (local only)
        logging.info(f"⏱️  Worker {worker_num} staggering by {stagger_delay}s (local parallel)")
        time.sleep(stagger_delay)
    elif hasattr(request.config, "workerinput"):
        logging.info("☁️  Cloud provider — skipping stagger (sessions are isolated)")

    # Create Appium client using Factory pattern
    # ⚡ LAYER 2: Pass animation + speed capabilities to driver
    # Combined with AppiumClient's built-in speed boosters:
    #   - ignoreUnimportantViews (30-50% faster finds)
    #   - skipLogcatCapture (CPU savings)
    #   - disableAndroidWatchers (CPU savings)
    #   - suppressKillServer (2-3s/restart)
    #   - elementResponseAttributes (smaller payloads)
    import os

    from src.data.parsers.config_manager import ConfigManager

    # Get provider from config (respects profile setting in config.yaml)
    config_manager = ConfigManager()
    provider = config_manager.get("provider.name", "local")

    # Mixed capability value types (bool/str/None), so keep this explicitly broad.
    extra_caps: dict[str, Any] = {}
    if platform == "android":
        extra_caps = {
            "appium:disableWindowAnimation": True,  # Kill window animations in instrumentation
            "appium:skipServerInstallation": True,
            "appium:skipDeviceInitialization": True,
            # ignoreHiddenApiPolicyError=True: Xiaomi MIUI / Chinese OEMs block
            # `settings delete global hidden_api_policy` (WRITE_SECURE_SETTINGS denied).
            # This tells UiAutomator2 to continue session creation even if that step fails.
            "appium:ignoreHiddenApiPolicyError": True,
        }

    # LambdaTest cloud provider - use device config instead of local device_id
    if provider == "lambdatest":
        # ⚡ Cloud: Override local-safe caps with cloud-optimized values
        # On cloud, device init is handled by LambdaTest — skipping saves 1-2s startup
        extra_caps["appium:skipDeviceInitialization"] = True
        extra_caps["appium:skipServerInstallation"] = True
        from src.data.parsers.config_manager import ConfigManager

        # Load lambdatest profile explicitly
        config_manager = ConfigManager(profile="lambdatest")

        # Get LambdaTest app_id from config
        # After profile merge, lambdatest settings are at top level
        provider_config = config_manager.get("provider", {})
        app_id = provider_config.get("app_id")

        if not app_id:
            raise ValueError(
                "LambdaTest app_id missing in config.yaml!\n"
                "Upload app: python scripts/asset_upload/upload_to_lambdatest.py path/to/app.apk\n"
                "Then update config.yaml lambdatest.provider.app_id"
            )

        # ✅ Map symbolic device_id to actual device config
        # device_id comes from pytest parametrization: "device_1", "device_2", etc.
        devices_config = config_manager.get("devices", {}).get(platform, {})
        device_config = devices_config.get(device_id, {})  # Use parametrized device_id

        if not device_config:
            raise ValueError(
                f"No {platform} device '{device_id}' configured in config.yaml lambdatest.devices.{platform} section\n"
                f"Available devices: {list(devices_config.keys())}"
            )

        # Get build info
        build_number = os.getenv("BUILD_NUMBER", "local")

        # Pre-install payment apps (GPay, Chrome, etc.) on cloud devices.
        # Always install if configured — no marker dependency needed.
        # Marker-based detection was fragile with pytest-xdist (workers may not
        # see all markers in request.session.items during fixture creation).
        # Having GPay/Chrome pre-installed has zero downside for non-payment tests.
        other_apps = None
        raw_apps = config_manager.get("other_apps", [])
        other_apps = [app for app in raw_apps if app and "REPLACE_WITH_" not in app] or None
        if other_apps:
            logging.info(f"📦 Pre-installing {len(other_apps)} payment apps on cloud device")

        # Read LambdaTest capabilities from config (video, visual, console)
        lt_caps_config = config_manager.get("capabilities", {})

        # Pass LambdaTest-specific kwargs
        extra_caps.update(
            {
                "lt_app_id": app_id,
                "lt_build": f"the app Mobile - {build_number}",
                "lt_project": "ExampleApp",
                "device_name": device_config.get("name"),
                "platform_version": device_config.get("platform_version"),
                "lt_other_apps": other_apps,
                "lt_video": lt_caps_config.get("video", True),
                "lt_visual": lt_caps_config.get("visual", False),
                "lt_console": lt_caps_config.get("console", True),
            }
        )

        logging.info(
            f"🌥️ LambdaTest Cloud Device: {device_config.get('name')} (Android {device_config.get('platform_version')})"
        )

        # Get LambdaTest hub URL and credentials
        hub_url_base = config_manager.get("provider.hub_url", "mobile-hub.lambdatest.com/wd/hub")
        lt_username = os.getenv("LT_USERNAME")
        lt_access_key = os.getenv("LT_ACCESS_KEY")

        if not lt_username or not lt_access_key:
            raise ValueError(
                "LambdaTest credentials not found! Set LT_USERNAME and LT_ACCESS_KEY environment variables"
            )

        # Build authenticated hub URL
        hub_url = f"https://{lt_username}:{lt_access_key}@{hub_url_base}"
        logging.info(f"🌐 Connecting to LambdaTest cloud: {hub_url_base}")

        # Don't pass local device_id to cloud - use LambdaTest hub
        client = DriverFactory.create_driver(
            platform=platform,
            device_id=None,  # Cloud doesn't use local device ID
            appium_server_url=hub_url,  # LambdaTest cloud hub URL
            **extra_caps,
        )
    else:
        # Local execution - use device_id
        client = DriverFactory.create_driver(
            platform=platform,
            device_id=device_id,
            systemPort=system_port,  # Unique port for parallel execution
            **extra_caps,  # Layer 2 capabilities
        )

    # Start session. An infra-class failure (LT hub never allocated the device
    # after retries) is not a test/app bug — skip the affected tests so the run
    # doesn't ERROR. Real capability/config errors still propagate as errors.
    from src.platform.appium_client import SessionInfraError

    try:
        driver = client.start_session()
    except SessionInfraError as e:
        pytest.skip(f"INFRA: {e}")

    # ⚡ LAYER 3: Disable UiAutomator2 idle waits (the big one!)
    # UiAutomator waits 10s for accessibility stream idle BEFORE + AFTER every action.
    # React Native apps with always-on animations never go idle → 20s+ per tap.
    # This sets waitForIdleTimeout=0, waitForSelectorTimeout=0 (Android only).
    # Layer 1 (ADB) kills system animations, Layer 2 (cap) kills window animations,
    # but THIS is what fixes app-internal JS animations that never stop.
    from src.pages.base_page import BasePage

    BasePage.optimize_for_react_native(driver)

    # Attach client helpers dynamically to webdriver instance.
    # Use setattr so static type checkers don't flag unknown WebDriver attributes.
    setattr(driver, "_client", client)  # noqa: B010
    setattr(driver, "launch_app_with_clear_data", client.launch_app_with_clear_data)  # noqa: B010
    setattr(driver, "launch_app_without_clear_data", client.launch_app_without_clear_data)  # noqa: B010
    setattr(driver, "launch_with_deeplink", client.launch_with_deeplink)  # noqa: B010
    setattr(driver, "launch_with_campaign", client.launch_with_campaign)  # noqa: B010
    setattr(driver, "navigate_to_screen", client.navigate_to_screen)  # noqa: B010
    setattr(driver, "start_screen_recording", client.start_screen_recording)  # noqa: B010
    setattr(driver, "stop_screen_recording", client.stop_screen_recording)  # noqa: B010

    # Cloud animations disabled via capabilities: disableAnimation=True, disableWindowAnimation=True

    # Heartbeat: pings driver.get_settings() every 60s for cloud sessions to
    # prevent LambdaTest idle-timeout during long Python-side work (between-test
    # API setup, JWT refresh, balance polling, retry sleeps). No-op for local.
    from src.platform.appium_client import keep_session_alive

    heartbeat_interval = int(os.getenv("APPIUM_HEARTBEAT_INTERVAL", "60"))
    enable_heartbeat = provider == "lambdatest"

    try:
        if enable_heartbeat:
            with keep_session_alive(driver, interval=heartbeat_interval):
                yield driver
        else:
            yield driver
    finally:
        # Cleanup - ALWAYS quit session (even on test failure/error)
        try:
            client.quit_session()
        except Exception as e:
            logging.warning(f"⚠️ Error during driver cleanup: {e}")
            # Force kill app as last resort
            try:
                if driver and hasattr(driver, "terminate_app"):
                    driver.terminate_app("com.example.app")
                    logging.info("✓ Force terminated app")
            except Exception:
                pass


@pytest.fixture(autouse=True)
def ensure_driver_alive(request):
    """
    Before each test: verify Appium session is alive, re-init if dead.

    This function-scoped autouse fixture ensures the session-scoped driver
    is still responsive before each test runs. On LambdaTest, it also updates
    the session name so the dashboard shows which test is currently running.

    Session recovery: If the driver session died (e.g., LambdaTest timeout),
    we reinitialize and transplant the new session into the existing driver
    reference so all Page Objects created from appium_driver still work.
    """
    # Skip for web tests — they don't use Appium
    if "appium_driver" not in request.fixturenames:
        yield
        return
    appium_driver = request.getfixturevalue("appium_driver")
    # Skip for web platform tests (no Appium driver)
    if appium_driver is None:
        yield
        return
    from src.pages.base_page import BasePage

    client = appium_driver._client

    if not client.is_session_valid():
        logging.warning("⚠️ Session dead — re-initializing...")
        try:
            new_driver = client.reinitialize_session()
            BasePage.optimize_for_react_native(new_driver)

            # Transplant new session into the existing driver reference.
            # Tests and Page Objects hold a reference to `appium_driver` (the old driver).
            # By swapping session_id + command_executor, the old reference now talks
            # to the new session — no need for a wrapper/proxy pattern.
            appium_driver.session_id = new_driver.session_id
            appium_driver.command_executor = new_driver.command_executor

            # Re-attach client helpers (bound methods reference client.driver internally)
            appium_driver.launch_app_with_clear_data = client.launch_app_with_clear_data
            appium_driver.launch_app_without_clear_data = client.launch_app_without_clear_data

            logging.info("✅ Session re-initialized successfully")
        except Exception as e:
            logging.error(f"❌ Session re-initialization failed: {e}")
            pytest.skip(f"Appium session died and could not recover: {e}")

    # Update LambdaTest session name to current test
    if client._is_cloud_provider():
        test_name = request.node.name
        client.update_session_name(test_name)

    yield


@pytest.fixture(scope="session")
def appium_server_url():
    """Appium server URL (configurable via env var)"""
    import os

    return os.getenv("APPIUM_SERVER_URL", "http://localhost:4723")


def pytest_configure(config):
    """Configure pytest. Markers registered in pyproject.toml.

    NOTE: Allure metadata setup MOVED to pytest_sessionstart — pytest_configure
    runs BEFORE --clean-alluredir wipes the directory, so env/executor/categories
    files written here are deleted before report generation. Empty Environment
    panel in Allure UI is the symptom.
    """
    pass


def pytest_collection_modifyitems(config, items):
    """Modify test collection based on platform and add xdist grouping."""
    platform = config.getoption("--platform")

    skip_android = pytest.mark.skip(reason="Test only for Android")
    skip_ios = pytest.mark.skip(reason="Test only for iOS")

    for item in items:
        # Skip platform-specific tests
        if platform == "ios" and "android" in item.keywords:
            item.add_marker(skip_android)
        elif platform == "android" and "ios" in item.keywords:
            item.add_marker(skip_ios)

        # Pin each test to one xdist worker by device. Composite name keeps
        # class-level @xdist_group("X") on different devices on different
        # workers — without this, both devices collapse onto one worker.
        if hasattr(item, "callspec") and "device_id" in item.callspec.params:
            device = item.callspec.params["device_id"]
            existing_name = None
            for mark in item.iter_markers(name="xdist_group"):
                existing_name = mark.kwargs.get("name") or (mark.args[0] if mark.args else None)
                break
            group_name = f"{existing_name}-{device}" if existing_name else device
            item.add_marker(pytest.mark.xdist_group(name=group_name))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    """
    Hook that runs before each test call - starts screen timing session.

    This automatically initializes the timing tracker for each test,
    so page objects can record screen transition times without manual setup.
    """
    start_timing_session()
    logging.debug(f"📊 Screen timing session started for: {item.name}")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """
    Enhanced hook to automatically capture screenshots and attach to Allure report.

    This hook runs after each test phase (setup, call, teardown) and:
    1. Captures screenshots on test failure
    2. Attaches final screenshot on test completion
    3. Attaches page source XML for debugging
    4. Records test execution metadata
    5. Attaches screen timing report (NEW!)

    No manual screenshot code needed in tests!
    """
    outcome = yield
    report = outcome.get_result()

    if report.when in {"setup", "call", "teardown"}:
        _write_worker_breadcrumb(item.config, f"report:{report.when}:{report.outcome}", item.nodeid)

    if report.when == "call":
        # Attach screen timing report to Allure (before screenshots)
        try:
            end_timing_session()
        except Exception as e:
            logging.debug(f"Could not attach timing report: {e}")
        # Get appium_driver from test fixtures
        if "appium_driver" in item.funcargs:
            driver = item.funcargs["appium_driver"]

            # Always attach final screenshot (success or failure)
            # Compressed to JPEG (quality=60) to keep Allure reports under GitHub Pages 1GB limit
            if hasattr(driver, "_client"):
                try:
                    screenshot_path = driver._client.capture_screenshot(f"{item.name}_final")
                    if screenshot_path and Path(screenshot_path).exists():
                        from io import BytesIO

                        from PIL import Image

                        img = Image.open(screenshot_path)
                        buf = BytesIO()
                        img.convert("RGB").save(buf, format="JPEG", quality=60, optimize=True)
                        screenshot_name = "✅ Test Complete" if report.passed else "❌ Test Failed"
                        allure.attach(
                            buf.getvalue(),
                            name=screenshot_name,
                            attachment_type=allure.attachment_type.JPG,
                        )
                except Exception as e:
                    logging.warning(f"Failed to capture final screenshot: {e}")

            # Detect cloud provider (used for debug attachments + status reporting)
            is_cloud = (
                hasattr(driver, "_client")
                and hasattr(driver._client, "_is_cloud_provider")
                and driver._client._is_cloud_provider()
            )

            # On failure, attach extra debug information
            if report.failed:
                # Skip page_source and logcat on cloud — they hang/timeout on
                # LambdaTest (page_source is 180K+ for React Native, logcat
                # fetches over network). Screenshot above is sufficient for cloud.
                if not is_cloud:
                    try:
                        # Attach page source for debugging (local only)
                        page_source = driver.page_source
                        allure.attach(
                            page_source,
                            name="📄 Page Source (XML)",
                            attachment_type=allure.attachment_type.XML,
                        )
                    except Exception as e:
                        allure.attach(
                            f"Failed to get page source: {e}",
                            name="⚠️ Page Source Error",
                            attachment_type=allure.attachment_type.TEXT,
                        )

                    try:
                        # Attach device logs if available (local only)
                        if hasattr(driver, "get_log"):
                            logs = driver.get_log("logcat")
                            log_text = "\n".join(
                                [f"{log['timestamp']}: {log['message']}" for log in logs[-100:]]
                            )
                            allure.attach(
                                log_text,
                                name="📱 Device Logs (Last 100 entries)",
                                attachment_type=allure.attachment_type.TEXT,
                            )
                    except Exception as e:
                        logging.debug(f"Could not attach device logs: {e}")
                else:
                    # Cloud: dump a FILTERED page source — text + resource-id + content-desc only.
                    # Full XML is 180K+ and can hang the driver. Filter ~5K, safe + debuggable.
                    import re
                    import threading

                    page_source_holder: dict = {}

                    def _fetch():
                        try:
                            page_source_holder["xml"] = driver.page_source
                        except Exception as e:  # noqa: BLE001
                            page_source_holder["err"] = str(e)

                    fetch_thread = threading.Thread(target=_fetch, daemon=True)
                    fetch_thread.start()
                    fetch_thread.join(timeout=15)

                    if "xml" in page_source_holder:
                        xml = page_source_holder["xml"]
                        nodes = re.findall(
                            r'<[^>]*(?:text|resource-id|content-desc|class)="[^"]*"[^>]*>',
                            xml,
                        )
                        attrs = re.findall(
                            r'(text|resource-id|content-desc|class)="([^"]+)"',
                            "\n".join(nodes),
                        )
                        seen: set = set()
                        lines = []
                        for k, v in attrs:
                            if v.strip() and (k, v) not in seen:
                                seen.add((k, v))
                                lines.append(f"{k}={v!r}")
                        filtered = "\n".join(lines[:500])
                        allure.attach(
                            filtered or "(no element attrs extracted)",
                            name="📄 Page Source (filtered, cloud-safe)",
                            attachment_type=allure.attachment_type.TEXT,
                        )
                    elif "err" in page_source_holder:
                        allure.attach(
                            f"page_source error: {page_source_holder['err']}",
                            name="⚠️ Page Source Error",
                            attachment_type=allure.attachment_type.TEXT,
                        )
                    else:
                        allure.attach(
                            "page_source fetch timed out (>15s)",
                            name="⚠️ Page Source Timeout",
                            attachment_type=allure.attachment_type.TEXT,
                        )

                # Attach failure details
                if report.longrepr:
                    allure.attach(
                        str(report.longrepr),
                        name="💥 Failure Traceback",
                        attachment_type=allure.attachment_type.TEXT,
                    )

            # Report test status to LambdaTest dashboard
            if is_cloud:
                try:
                    lt_status = "passed" if report.passed else "failed"
                    driver.execute_script("lambda-status", {"status": lt_status})
                except Exception as e:
                    logging.debug(f"Could not update LambdaTest status: {e}")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """
    Hook to automatically extract and attach test parameters to Allure report.

    This hook analyzes:
    1. Test docstrings for metadata (User, OTP, Platform)
    2. Pytest parametrize marks
    3. Fixture values
    4. Device information

    No manual allure.dynamic.parameter() calls needed!

    A second pytest_runtest_setup was defined later in this file and silently
    replaced this one, so none of the extraction below ran. Its only statement
    was the breadcrumb write, which now happens here.
    """
    _write_worker_breadcrumb(item.config, "setup", item.nodeid)

    # Extract parameters from test docstring
    if item.obj.__doc__:
        doc = item.obj.__doc__

        # Extract User/Phone: 5550000000
        phone_match = re.search(r"(?:User|Phone):\s*(\d{10,})", doc)
        if phone_match:
            allure.dynamic.parameter("📱 Phone Number", phone_match.group(1))

        # Extract the OTP from the message body
        otp_match = re.search(r"OTP:\s*(\d+)", doc)
        if otp_match:
            allure.dynamic.parameter("🔐 OTP", otp_match.group(1))

        # Extract Platform: Android/iOS
        platform_match = re.search(r"Platform:\s*(\w+)", doc, re.IGNORECASE)
        if platform_match:
            allure.dynamic.parameter("📲 Platform", platform_match.group(1))

        # Extract Framework: Appium/Maestro
        framework_match = re.search(r"Framework:\s*([\w\s]+)", doc)
        if framework_match:
            allure.dynamic.parameter("🔧 Framework", framework_match.group(1).strip())

    # Extract parameters from pytest.mark.parametrize
    for marker in item.iter_markers(name="parametrize"):
        param_names = marker.args[0]
        if isinstance(param_names, str):
            param_names = [param_names]

        # Get parameter values from item.callspec
        if hasattr(item, "callspec"):
            for param_name in param_names:
                if param_name in item.callspec.params:
                    value = item.callspec.params[param_name]

                    # Handle dict parameters (user_data, etc.)
                    if isinstance(value, dict):
                        for key, val in value.items():
                            allure.dynamic.parameter(f"📋 {key.title()}", val)
                    else:
                        allure.dynamic.parameter(f"📋 {param_name}", value)

    # Resolve device_id from callspec (set by pytest_generate_tests, not @pytest.mark.parametrize)
    if hasattr(item, "callspec") and "device_id" in item.callspec.params:
        device_val = item.callspec.params["device_id"]
        device_display = _resolve_device_display(device_val)
        allure.dynamic.parameter("device_id", device_display)
        # Group Allure "Suites" view by device so parallel runs split into
        # one section per device instead of interleaving in the same suite.
        allure.dynamic.parent_suite(device_display)

    # Extract device info from fixtures (if appium_driver is available)
    if "appium_driver" in item.funcargs:
        driver = item.funcargs["appium_driver"]
        if driver and hasattr(driver, "capabilities"):
            caps = driver.capabilities

            # Device info
            device_name = caps.get("deviceName", "Unknown")
            os_version = caps.get("platformVersion", "Unknown")
            udid = caps.get("udid", caps.get("deviceUDID", "Unknown"))

            allure.dynamic.parameter("📱 Device", device_name)
            allure.dynamic.parameter("🔢 OS Version", os_version)

            if udid and udid != "Unknown":
                # Show last 8 chars of UDID for identification
                short_udid = f"...{udid[-8:]}" if len(udid) > 8 else udid
                allure.dynamic.parameter("🆔 Device ID", short_udid)

    # Add test markers as labels
    for marker in item.iter_markers():
        if marker.name in ["smoke", "regression", "e2e"]:
            allure.dynamic.tag(marker.name.upper())
        elif marker.name in ["android", "ios"]:
            allure.dynamic.tag(f"Platform: {marker.name.upper()}")


@pytest.fixture(autouse=True)
def reset_test_state():
    """Auto-use fixture to reset state between tests"""
    # Setup
    yield
    # Teardown - any cleanup needed between tests


@pytest.fixture(autouse=True)
def self_heal_reporter():
    """Report self-healing statistics after each test (Allure attachment)."""
    if os.getenv("SELF_HEAL", "").lower() not in ("true", "1", "yes"):
        yield
        return

    from src.utils.self_heal import get_heal_stats, reset_heal_stats

    reset_heal_stats()
    yield
    stats = get_heal_stats()
    if any(v > 0 for v in stats.values()):
        allure.attach(
            json.dumps(stats, indent=2),
            name="Self-Healing Stats",
            attachment_type=allure.attachment_type.JSON,
        )
        heal_logger = logging.getLogger(__name__)
        heal_logger.info(
            f"[HEAL] Test stats: {stats['baseline_heals']} baseline, "
            f"{stats['claude_heals']} Claude, {stats['heal_failures']} failures"
        )


@pytest.fixture(autouse=True)
def _label_oem_family(request):
    """Tag each test with its OEM family — once per test, not per page-object construction."""
    # Skip for unit tests / web tests that don't use Appium
    if "appium_driver" not in request.fixturenames:
        yield
        return
    appium_driver = request.getfixturevalue("appium_driver")
    try:
        import allure as _allure

        family = "unknown_android"
        if appium_driver and hasattr(appium_driver, "capabilities"):
            from scripts.lib.oem_families import resolve_oem_family

            caps = appium_driver.capabilities or {}
            family = resolve_oem_family(
                caps.get("deviceName", ""),
                caps.get("deviceManufacturer", ""),
            )
        _allure.dynamic.label("oem_family", family)
    except Exception:
        pass
    yield


# Parametrization helpers
def load_test_data(file_path):
    """Helper to load test data for parametrization"""
    from src.data.parsers.data_parser import DataParser

    return DataParser.parse_csv_file(file_path)


# ========================================
# PARALLEL DEVICE TESTING FIXTURES
# ========================================


def pytest_generate_tests(metafunc):
    """
    Automatically parametrize tests with device_id based on configuration.

    Configuration behavior:
    - enable_multi_device: false → PRIMARY device only
    - enable_multi_device: true, device_count: 2 → PRIMARY + SECONDARY devices
    - enable_multi_device: true, device_count: 3 → PRIMARY + SECONDARY + device_id_3
    - parallel: false → Devices run one after another (sequential)
    - parallel: true → Parallel execution (requires: pytest -n <count>)

    Cloud execution (LambdaTest):
    - Devices managed by cloud provider
    - Uses device names from config (e.g., "Xiaomi Redmi Note 11", "Oppo A74")
    - Device IDs are symbolic (device_1, device_2) for pytest parametrization

    Usage in test:
        def test_something(self, device_id):
            # This test will run based on config
            pass
    """
    if "device_id" in metafunc.fixturenames:
        # Skip device parametrization for web platform
        if metafunc.config.getoption("platform") == "web":
            return

        # --device-id pins a single device and overrides every profile/config source.
        cli_device = metafunc.config.getoption("device_id")
        if cli_device:
            metafunc.parametrize("device_id", [cli_device], ids=lambda d: d, indirect=True)
            return

        # Get configuration manager (respects RUN_MODE env var)
        config_manager = ConfigManager()

        # Check provider type
        provider = config_manager.get("provider.name", "local")

        # DEBUG: Print what we're detecting
        print("\n🔍 DEBUG pytest_generate_tests:")
        print(f"   Profile: {config_manager.profile}")
        print(f"   Provider: {provider}")
        print(
            f"   Will use: {'CLOUD devices (device_1, device_2)' if provider == 'lambdatest' else 'LOCAL devices (UDIDs)'}\n"
        )

        if provider == "lambdatest":
            # ✅ CLOUD EXECUTION - Use symbolic device identifiers
            # Cloud provider handles actual device allocation
            enable_multi_device = config_manager.get("orchestration.enable_multi_device", False)
            device_count = config_manager.get("orchestration.device_count", 1)

            # Use symbolic device IDs (device_1, device_2, etc.)
            # The appium_driver fixture will map these to actual cloud devices
            devices = ["device_1"]  # At least one device

            if enable_multi_device and device_count > 1:
                for i in range(2, device_count + 1):
                    devices.append(f"device_{i}")

            # Build human-readable IDs from config device names
            devices_config = config_manager.get("devices", {}).get("android", {})
            device_ids = [
                devices_config.get(d, {}).get("name", d).replace(" ", "_") for d in devices
            ]
            metafunc.parametrize("device_id", devices, ids=device_ids, indirect=True)

        else:
            # ✅ LOCAL EXECUTION - Use actual local device UDIDs
            enable_multi_device = config_manager.get("orchestration.enable_multi_device", False)
            device_count = config_manager.get("orchestration.device_count", 1)

            # Always include primary device
            devices = [config_manager.get("devices.android.primary_device_id", "emulator-5554")]

            # Add additional devices if multi-device is enabled
            if enable_multi_device and device_count > 1:
                # Add secondary device
                secondary = config_manager.get("devices.android.secondary_device_id")
                if secondary:
                    devices.append(secondary)

                # Add more devices if count > 2
                if device_count > 2:
                    for i in range(3, device_count + 1):
                        device = config_manager.get(f"devices.android.device_id_{i}")
                        if device:
                            devices.append(device)

            # FIX: Use indirect=True so the device_id fixture receives parametrized values
            # This ensures the fixture's request.param contains the actual device ID
            metafunc.parametrize("device_id", devices, ids=lambda d: f"device_{d}", indirect=True)


# ========================================
# SLACK INTEGRATION
# ========================================

# Global session start time
_session_start_time = None


def pytest_sessionstart(session):
    """Track session start time + write Allure metadata.

    Allure metadata (environment.properties, executor.json, categories.json)
    is written here — AFTER --clean-alluredir has wiped the directory but
    BEFORE tests run. Writing in pytest_configure was being clobbered.
    """
    global _session_start_time
    import time

    _session_start_time = time.time()
    _install_worker_crash_diagnostics(session.config)

    try:
        setup_allure_metadata(
            output_dir="allure-results",
            build_name=f"Test Run {datetime.now().strftime('%Y%m%d-%H%M%S')}",
            additional_env_info={
                "Framework": "the app Mobile Test Automation",
                "Test Type": "Mobile UI Automation",
                "Automation Tool": "Appium + Pytest",
            },
        )
    except Exception as e:
        logging.warning(f"Failed to setup Allure metadata: {e}")


def pytest_sessionfinish(session, exitstatus):
    """
    Send Slack notification after test session completes.
    Only the controller process sends (not xdist workers).
    """
    # ── xdist guard: only the controller sends the notification ──
    if hasattr(session.config, "workerinput"):
        return  # This is a worker process — skip to avoid duplicate notifications

    global _session_start_time
    import os
    import time

    # ── CI guard: workflow has its own Slack step with richer metadata ──
    if os.getenv("GITHUB_ACTIONS"):
        return

    # Calculate duration
    duration = 0
    if _session_start_time:
        duration = time.time() - _session_start_time

    # Get config
    config_manager = ConfigManager()
    slack_config = config_manager.get("reporting.slack", {})

    # Check if Slack notifications enabled
    enabled = os.getenv("SLACK_ENABLED", str(slack_config.get("enabled", "false"))).lower() in (
        "true",
        "1",
        "yes",
    )

    if not enabled:
        logging.info("📢 Slack notifications disabled (set SLACK_ENABLED=true to enable)")
        return

    webhook_url = os.getenv("SLACK_WEBHOOK_URL", slack_config.get("webhook_url", ""))

    if not webhook_url:
        logging.warning("⚠️  SLACK_WEBHOOK_URL not set, skipping notification")
        return

    # Get provider and build info
    run_mode = os.getenv("RUN_MODE", "local")
    provider = "lambdatest" if run_mode == "lambdatest" else "local"
    build_number = os.getenv("BUILD_NUMBER")
    allure_report_url = os.getenv("ALLURE_REPORT_URL")

    # Get device info (all devices, not just device_1)
    device_info = None
    profile_key = "lambdatest" if run_mode == "lambdatest" else "local"

    if provider == "local":
        device_id = config_manager.get("devices.android.primary_device_id")
        if device_id:
            device_info = f"Local Device ({device_id})"
    else:
        # LambdaTest device info — collect ALL devices
        devices_config = config_manager.get(f"profiles.{profile_key}.devices.android", {})
        if not devices_config:
            devices_config = config_manager.get("devices", {}).get("android", {})
        device_list = []
        for key in sorted(devices_config.keys()):
            if key.startswith("device_"):
                dev = devices_config[key]
                if isinstance(dev, dict) and dev.get("name"):
                    device_list.append(
                        f"{dev['name']} · Android {dev.get('platform_version', '?')}"
                    )
        if device_list:
            device_info = device_list if len(device_list) > 1 else device_list[0]

    # Get suite/branch/run_url for richer Slack messages
    suite_name = os.getenv("TEST_SUITE")
    run_url = os.getenv("RUN_URL")
    branch = os.getenv("GIT_BRANCH")
    if not branch:
        try:
            import subprocess

            branch = (
                subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
        except Exception:
            pass

    # Send notification
    try:
        from src.reporting.slack_notifier import send_test_notification

        logging.info("📤 Sending Slack notification...")

        success = send_test_notification(
            webhook_url=webhook_url,
            enabled=enabled,
            results_dir="allure-results",
            allure_report_url=allure_report_url,
            provider=provider,
            device_info=device_info,
            build_number=build_number,
            duration=duration,
            suite_name=suite_name,
            branch=branch,
            run_url=run_url,
        )

        if success:
            logging.info("✅ Slack notification sent successfully!")
        else:
            logging.warning("⚠️  Failed to send Slack notification")

    except Exception as e:
        logging.error(f"❌ Error sending Slack notification: {e}")
        # Don't fail the test run because of notification failure
        pass
