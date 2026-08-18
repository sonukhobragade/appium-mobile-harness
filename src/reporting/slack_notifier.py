"""
Slack notification system for test execution reports.

Sends rich formatted messages to Slack with:
- Test execution summary (passed/failed/skipped)
- Allure report links
- Failure details with error messages
- Execution environment (local/LambdaTest)
- Duration and device information
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


def _extract_parametrize_variant(parameters: list[dict]) -> str | None:
    """Extract a short variant name from parametrized test parameters.

    Looks for 📋-prefixed parameters (added by conftest for @pytest.mark.parametrize)
    and extracts a human-readable short name for Slack display.

    Returns e.g. "GPay UPI", "paytm", or None if not parametrized.
    """
    for p in parameters:
        name = p.get("name", "")
        if not name.startswith("📋"):
            continue
        value = str(p.get("value", ""))
        # Try display_name from dataclass repr: display_name='GPay UPI'
        match = re.search(r"display_name='([^']+)'", value)
        if match:
            return match.group(1)
        # Try id/method_id: method_id='gpay'
        match = re.search(r"(?:method_)?id='([^']+)'", value)
        if match:
            return match.group(1)
        # Simple value (not a dataclass repr) — use directly if short
        if len(value) <= 40 and "(" not in value:
            return value.strip("'\"")
    return None


# Patterns that indicate infrastructure/environment failures (not test bugs)
INFRA_FAILURE_PATTERNS = [
    "timed out from lambda queue",
    "timed out from queue",
    "device not available",
    "session not created",
    "could not start a new session",
    "unreachable browser",
    "connection refused",
    "appium server is not running",
    "device is busy",
    "no such device",
]


def _is_infra_failure(error_message: str) -> bool:
    """Check if an error message indicates an infrastructure failure."""
    error_lower = error_message.lower()
    return any(pattern in error_lower for pattern in INFRA_FAILURE_PATTERNS)


class SlackNotifier:
    """Send test execution notifications to Slack.

    Supports two delivery modes:
      1. Webhook  — pass `webhook_url=...`                          (posts to webhook's configured channel)
      2. Bot API  — pass `bot_token=..., channel=...`              (uses chat.postMessage, any channel the bot is in)

    When `bot_token` is set, it takes precedence over `webhook_url`.
    """

    SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"

    def __init__(
        self,
        webhook_url: str = "",
        enabled: bool = True,
        bot_token: str = "",
        channel: str = "",
    ):
        """
        Initialize Slack notifier.

        Args:
            webhook_url: Slack incoming webhook URL (legacy path)
            enabled: Whether notifications are enabled
            bot_token: Slack bot user OAuth token (xoxb-...) — preferred over webhook
            channel: Target channel ID or name (e.g. "#qa-campaign-tests") — required when bot_token is set
        """
        self.webhook_url = webhook_url
        self.enabled = enabled
        self.bot_token = bot_token
        self.channel = channel
        self.session = requests.Session()

    def _post(self, payload: dict) -> bool:
        """Send a Slack Block Kit payload using whichever mode is configured."""
        if not self.enabled:
            return False
        try:
            if self.bot_token and self.channel:
                body = {"channel": self.channel, **payload}
                response = self.session.post(
                    self.SLACK_POST_MESSAGE_URL,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self.bot_token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
                if not data.get("ok"):
                    logger.error("Slack API error: %s", data.get("error"))
                    return False
                return True
            if self.webhook_url:
                response = self.session.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                )
                response.raise_for_status()
                return True
            logger.error("SlackNotifier has neither bot_token+channel nor webhook_url")
            return False
        except requests.exceptions.RequestException as e:
            logger.error("Failed to send Slack message: %s", e)
            return False

    def send_test_report(
        self,
        total: int,
        passed: int,
        failed: int,
        skipped: int,
        duration: float,
        failures: list[dict[str, str]] | None = None,
        passed_tests: list[str] | None = None,
        allure_report_url: str | None = None,
        provider: str = "local",
        device_info: str | list[str] | None = None,
        build_number: str | None = None,
        suite_name: str | None = None,
        branch: str | None = None,
        run_url: str | None = None,
        rerun: int = 0,
        infra_failures: list[dict[str, str]] | None = None,
        retried_tests: list[dict[str, str]] | None = None,
    ) -> bool:
        """
        Send test execution report to Slack.

        Args:
            total: Total test count
            passed: Passed test count
            failed: Failed test count
            skipped: Skipped test count
            duration: Execution duration in seconds
            failures: List of failure details (test name, error)
            allure_report_url: URL to Allure report
            provider: Execution provider (local/lambdatest)
            device_info: Device information string or list of device strings
            build_number: Build/CI number
            suite_name: Test suite name (e.g. "sanity_smoke")
            branch: Git branch name
            run_url: CI run URL (e.g. GitHub Actions run link)
            retried_tests: List of tests that were retried with attempt counts

        Returns:
            True if notification sent successfully
        """
        if not self.enabled:
            logger.info("Slack notifications disabled")
            return False

        if not self.webhook_url:
            logger.warning("Slack webhook URL not configured")
            return False

        # Build message
        message = self._build_message(
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration=duration,
            failures=failures or [],
            passed_tests=passed_tests or [],
            allure_report_url=allure_report_url,
            provider=provider,
            device_info=device_info,
            build_number=build_number,
            suite_name=suite_name,
            branch=branch,
            run_url=run_url,
            rerun=rerun,
            infra_failures=infra_failures,
            retried_tests=retried_tests,
        )

        ok = self._post(message)
        if ok:
            logger.info("✅ Slack notification sent successfully")
        else:
            logger.error("❌ Failed to send Slack notification")
        return ok

    def _build_message(
        self,
        total: int,
        passed: int,
        failed: int,
        skipped: int,
        duration: float,
        failures: list[dict[str, str]],
        passed_tests: list[str],
        allure_report_url: str | None,
        provider: str,
        device_info: str | list[str] | None,
        build_number: str | None,
        suite_name: str | None = None,
        branch: str | None = None,
        run_url: str | None = None,
        rerun: int = 0,
        infra_failures: list[dict[str, str]] | None = None,
        retried_tests: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Build Slack message with rich formatting using mrkdwn text."""
        infra_failures = infra_failures or []
        retried_tests = retried_tests or []

        # Determine status and color
        if failed == 0 and passed > 0:
            if rerun > 0:
                status_text = "PASSED (WITH RETRIES)"
                color = "#36a64f"
            elif infra_failures:
                status_text = "PASSED (INFRA ISSUES)"
                color = "#ff9800"
            else:
                status_text = "PASSED"
                color = "#36a64f"
        elif failed > 0:
            if rerun > 0:
                status_text = "FAILED (WITH RETRIES)"
                color = "#d00000"
            else:
                status_text = "FAILED"
                color = "#d00000"
        else:
            status_text = "NO TESTS RUN"
            color = "#ff9800"

        # Pass rate based on tests that actually RAN (exclude skipped + infra)
        ran = total - skipped - len(infra_failures)
        pass_rate = (passed / ran * 100) if ran > 0 else 0

        # Execution time
        duration_str = f"{duration:.1f}s" if duration < 60 else f"{duration / 60:.1f}m"

        # Build header
        header = f"the app Mobile Tests — {status_text}"
        if build_number:
            header += f" (#{build_number})"

        # Build blocks
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header, "emoji": True},
            },
            {"type": "divider"},
        ]

        # Summary line: suite | branch | provider
        summary_parts = []
        if suite_name:
            summary_parts.append(f"*Suite:* `{suite_name}`")
        if branch:
            summary_parts.append(f"*Branch:* `{branch}`")
        summary_parts.append(f"*Provider:* `{provider.upper()}`")
        if duration > 0:
            summary_parts.append(f"*Duration:* `{duration_str}`")
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "  |  ".join(summary_parts)}],
            }
        )

        # Device info
        if device_info:
            dev_list = device_info if isinstance(device_info, list) else [device_info]
            dev_parts = [f"*Device {i + 1}:* {dev}" for i, dev in enumerate(dev_list)]
            blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "  |  ".join(dev_parts)}],
                }
            )

        blocks.append({"type": "divider"})

        # Test counts
        counts_line = f"*Pass Rate:* `{pass_rate:.0f}%`  |  *Passed:* `{passed}/{ran}`"
        if failed > 0:
            counts_line += f"  |  *Failed:* `{failed}`"
        if skipped > 0:
            counts_line += f"  |  *Skipped:* `{skipped}`"
        if rerun > 0:
            counts_line += f"  |  *Retried:* `{rerun}`"
        if infra_failures:
            counts_line += f"  |  *Infra:* `{len(infra_failures)}`"
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": counts_line},
            }
        )
        blocks.append({"type": "divider"})

        # Build device_id → device name map
        device_name_map = {}
        if isinstance(device_info, list):
            for idx, dev in enumerate(device_info):
                device_name_map[f"device_{idx + 1}"] = dev
        elif isinstance(device_info, str) and device_info:
            device_name_map["_single"] = device_info

        # ── Helper: resolve device key to display name ──
        def _resolve_device_label(device_key: str) -> str:
            if device_key in device_name_map:
                return device_name_map[device_key]
            if " " in device_key:
                return device_key
            if isinstance(device_info, str) and device_info:
                return device_info
            if device_key:
                return device_key.replace("_", " ").title()
            return "Unknown Device"

        # ── Collect per-device stats ──
        device_passed: dict[str, list[str]] = {}
        device_failed: dict[str, list[dict]] = {}

        for entry in passed_tests or []:
            device = entry.get("device", "") if isinstance(entry, dict) else ""
            name = entry.get("name", entry) if isinstance(entry, dict) else entry
            device_passed.setdefault(device, []).append(name)

        for failure in failures:
            device_key = ""
            params = failure.get("parameters", "")
            if params:
                for param_part in params.split(","):
                    param_part = param_part.strip()
                    if param_part.startswith("device_id="):
                        device_key = param_part.split("=", 1)[1].strip()
                        break
            device_failed.setdefault(device_key, []).append(failure)

        all_device_keys = sorted(set(list(device_passed.keys()) + list(device_failed.keys())))
        multi_device = len(all_device_keys) > 1
        # Pre-compute labels to avoid repeated resolution
        device_labels = {dk: _resolve_device_label(dk) for dk in all_device_keys}

        # ── Per-device summary (only when multiple devices) ──
        if multi_device:
            device_summary_lines = []
            for dk in all_device_keys:
                label = device_labels[dk]
                dp = len(device_passed.get(dk, []))
                df = len(device_failed.get(dk, []))
                dt = dp + df
                rate = (dp / dt * 100) if dt > 0 else 0
                if df == 0:
                    device_summary_lines.append(
                        f":large_green_circle:  *{label}*  —  `{dp}/{dt} passed` (`{rate:.0f}%`)"
                    )
                else:
                    device_summary_lines.append(
                        f":red_circle:  *{label}*  —  `{dp}/{dt} passed, {df} failed` (`{rate:.0f}%`)"
                    )
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "\n".join(device_summary_lines),
                    },
                }
            )
            blocks.append({"type": "divider"})

        # ── Passed tests ──
        if passed_tests and len(passed_tests) <= 20:
            if multi_device:
                for dk in all_device_keys:
                    tests = device_passed.get(dk, [])
                    if not tests:
                        continue
                    label = device_labels[dk]
                    test_lines = [f"`PASS` {t}" for t in tests]
                    blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*{label}*\n" + "\n".join(test_lines),
                            },
                        }
                    )
            else:
                all_names = []
                for entry in passed_tests:
                    name = entry.get("name", entry) if isinstance(entry, dict) else entry
                    all_names.append(f"`PASS` {name}")
                blocks.append(
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "\n".join(all_names)},
                    }
                )

        elif passed_tests and len(passed_tests) > 20:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Passed:* {len(passed_tests)} tests — see Allure report for details.",
                        }
                    ],
                }
            )

        # ── Failed tests (grouped by device when multi-device) ──
        if failures:
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Failed Tests ({len(failures)})*"},
                }
            )

            shown = 0
            for dk in all_device_keys if multi_device else [""]:
                dk_failures = device_failed.get(dk, failures if not multi_device else [])
                if not dk_failures:
                    continue

                if multi_device:
                    label = device_labels[dk]
                    blocks.append(
                        {
                            "type": "context",
                            "elements": [{"type": "mrkdwn", "text": f"*{label}*"}],
                        }
                    )

                for failure in dk_failures:
                    if shown >= 5:
                        break
                    test_name = failure.get("test", "Unknown")
                    error_msg = failure.get("error", "No error message")
                    file_loc = failure.get("file", "")
                    line_no = failure.get("line", "")

                    fail_text = f"`FAIL` *{test_name}*"
                    if not multi_device and dk:
                        fail_text += f"  ({device_labels.get(dk, dk)})"

                    clean_error = error_msg.split("\n")[0][:200]
                    fail_text += f"\n`{clean_error}`"
                    if file_loc and line_no:
                        fail_text += f"  `{file_loc}:{line_no}`"

                    blocks.append(
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": fail_text},
                        }
                    )
                    shown += 1

                if shown >= 5:
                    break

            remaining = len(failures) - shown
            if remaining > 0:
                blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"_+{remaining} more — see Allure report_",
                            }
                        ],
                    }
                )

        # Retried tests
        if retried_tests:
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Retried Tests ({len(retried_tests)})*"},
                }
            )

            for rt in retried_tests[:5]:
                name = rt.get("name", "Unknown")
                device_key = rt.get("device", "")
                device_display = device_name_map.get(device_key, device_key) if device_key else ""
                attempts = rt.get("attempts", 2)
                final = rt.get("final_status", "unknown")
                tag = "PASS" if final == "passed" else "FAIL"

                rt_text = f"`{tag}` *{name}*  (attempts: {attempts})"
                if device_display:
                    rt_text += f"  ({device_display})"
                blocks.append(
                    {
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": rt_text}],
                    }
                )

        # Infrastructure failures
        if infra_failures:
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Infra Issues ({len(infra_failures)})*"},
                }
            )
            for failure in infra_failures[:3]:
                test_name = failure.get("test", "Unknown")
                short_error = failure.get("error", "").split("\n")[0][:150]
                blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": f"`INFRA` *{test_name}*\n`{short_error}`"}
                        ],
                    }
                )

        # Links
        link_parts = []
        if run_url:
            link_parts.append(f"<{run_url}|View Run>")
        if allure_report_url:
            link_parts.append(f"<{allure_report_url}|Allure Report>")
        if link_parts:
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "  |  ".join(link_parts)}],
                }
            )

        # Timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"`{timestamp}`"}],
            }
        )

        return {
            "blocks": blocks,
            "attachments": [
                {
                    "color": color,
                    "fallback": f"Test Execution {status_text}: {passed}/{ran} passed",
                }
            ],
        }

    def send_simple_message(self, text: str) -> bool:
        """
        Send simple text message to Slack.

        Args:
            text: Message text

        Returns:
            True if sent successfully
        """
        return self._post({"text": text})


def parse_allure_results(results_dir: str = "allure-results") -> dict[str, Any]:
    """
    Parse Allure results to extract test counts and failures.

    Args:
        results_dir: Path to allure-results directory

    Returns:
        Dict with test statistics and failure details
    """
    results_path = Path(results_dir)

    if not results_path.exists():
        logger.warning(f"Allure results directory not found: {results_dir}")
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "failures": [],
        }

    # Parse result JSON files
    # Deduplicate reruns: group by test fullName + parameters, keep only the FINAL attempt
    # IMPORTANT: Include parameters (e.g., device_1/device_2) in the key so parallel
    # device runs are NOT mistakenly counted as retries of the same test.
    all_results = {}  # unique_key -> list of result_dicts

    for result_file in results_path.glob("*-result.json"):
        try:
            with open(result_file, encoding="utf-8") as f:
                result = json.load(f)

            full_name = result.get("fullName", result.get("name", str(result_file)))
            # Include parameters in key to distinguish device_1 vs device_2 runs
            # EXCLUDE screen timing parameters (⏱️) — they differ between retries
            # and break deduplication of rerun attempts
            params = result.get("parameters", [])
            stable_params = [p for p in params if not p.get("name", "").startswith("⏱️")]
            param_suffix = (
                "|".join(f"{p.get('name', '')}={p.get('value', '')}" for p in stable_params)
                if stable_params
                else ""
            )
            unique_key = f"{full_name}[{param_suffix}]" if param_suffix else full_name
            if unique_key not in all_results:
                all_results[unique_key] = []
            all_results[unique_key].append(result)
        except Exception as e:
            logger.error(f"Failed to parse result file {result_file}: {e}")
            continue

    total = 0
    passed = 0
    failed = 0
    skipped = 0
    rerun = 0
    failures = []
    passed_tests = []
    retried_tests = []  # Track which tests were retried

    for full_name, attempts in all_results.items():
        # Sort by stop time (latest attempt last) to get final result
        attempts.sort(key=lambda r: r.get("stop", 0))
        final = attempts[-1]
        extra_attempts = len(attempts) - 1
        if extra_attempts > 0:
            rerun += extra_attempts
            # Track retried test with device info and final outcome
            parameters = final.get("parameters", [])
            device_param = ""
            for p in parameters:
                if p.get("name") == "device_id":
                    device_param = p.get("value", "").strip("'\"")
                    break
            # Enrich retried test name with parametrize variant
            retry_variant = _extract_parametrize_variant(parameters)
            retry_name = final.get("name", "Unknown Test")
            if retry_variant:
                retry_name = f"{retry_name} [{retry_variant}]"
            retried_tests.append(
                {
                    "name": retry_name,
                    "device": device_param,
                    "attempts": extra_attempts + 1,
                    "final_status": final.get("status", "unknown"),
                }
            )

        total += 1
        status = final.get("status", "unknown")
        test_name = final.get("name", "Unknown Test")

        # Append parametrize variant to differentiate identical test names
        all_params = final.get("parameters", [])
        variant = _extract_parametrize_variant(all_params)
        if variant:
            test_name = f"{test_name} [{variant}]"

        if status == "passed":
            passed += 1
            # Extract device parameter for grouping
            parameters = final.get("parameters", [])
            device_param = ""
            for p in parameters:
                if p.get("name") == "device_id":
                    device_param = p.get("value", "").strip("'\"")
                    break
            entry = test_name
            if extra_attempts > 0:
                entry = f"{test_name} (passed on retry #{extra_attempts + 1})"
            passed_tests.append({"name": entry, "device": device_param})
        elif status in ("failed", "broken"):
            failed += 1

            # Extract detailed failure information (test_name already enriched above)
            full_name = final.get("fullName", "")

            # Extract test parameters (device, data, etc.)
            parameters = final.get("parameters", [])
            param_str = (
                ", ".join(
                    [
                        f"{p.get('name')}={p.get('value', '').strip(chr(39) + chr(34))}"
                        for p in parameters
                    ]
                )
                if parameters
                else ""
            )

            # Get labels (markers, tags)
            labels = final.get("labels", [])
            tags = [label.get("value") for label in labels if label.get("name") == "tag"]

            # Get status details
            status_details = final.get("statusDetails", {})
            error_message = status_details.get("message", "Unknown error")
            trace = status_details.get("trace", "")

            # Extract file location and line number from trace
            file_location = None
            line_number = None
            failed_element = None

            if trace:
                # Parse traceback to find actual failure location
                trace_lines = trace.split("\n")

                # Look for assertion error or element failure
                for _i, line in enumerate(trace_lines):
                    # Find file:line references (e.g., "tests/smoke/test_login.py:45")
                    if "tests/" in line and ".py:" in line:
                        parts = line.strip().split()
                        for part in parts:
                            if ".py:" in part:
                                try:
                                    file_path, line_no = part.rsplit(":", 1)
                                    if file_path.endswith(".py"):
                                        file_location = file_path.split("/")[-1]  # Just filename
                                        line_number = line_no
                                except Exception:
                                    pass

                    # Extract failed element/locator info
                    if "Element" in line or "Locator" in line or "locator" in line:
                        failed_element = line.strip()
                    elif "assert" in line.lower() or "expected" in line.lower():
                        failed_element = line.strip()

            # Build detailed error message
            error_parts = []

            # Add assertion/error message
            if error_message:
                # Clean up error message (first 200 chars)
                clean_error = error_message.split("\n")[0][:200]
                error_parts.append(clean_error)

            # Add failed element if found
            if failed_element and failed_element not in error_message:
                error_parts.append(f"Element: {failed_element[:150]}")

            # Add location if found
            if file_location and line_number:
                error_parts.append(f"Location: {file_location}:{line_number}")

            # Add retry info
            if extra_attempts > 0:
                error_parts.append(f"Retried: {extra_attempts} time(s), still failed")

            detailed_error = "\n".join(error_parts) if error_parts else error_message

            is_infra = _is_infra_failure(error_message) or _is_infra_failure(trace)
            failures.append(
                {
                    "test": test_name,
                    "error": detailed_error,
                    "full_name": full_name,
                    "parameters": param_str,
                    "tags": tags,
                    "file": file_location,
                    "line": line_number,
                    "infra": is_infra,
                }
            )
        elif status == "skipped":
            skipped += 1

    infra_failures = [f for f in failures if f.get("infra")]
    test_failures = [f for f in failures if not f.get("infra")]

    # Calculate duration from allure timestamps (earliest start → latest stop)
    duration = 0
    earliest_start = None
    latest_stop = None
    for attempts in all_results.values():
        for r in attempts:
            start = r.get("start", 0)
            stop = r.get("stop", 0)
            if start and (earliest_start is None or start < earliest_start):
                earliest_start = start
            if stop and (latest_stop is None or stop > latest_stop):
                latest_stop = stop
    if earliest_start and latest_stop:
        duration = (latest_stop - earliest_start) / 1000  # ms → seconds

    return {
        "total": total,
        "passed": passed,
        "failed": len(test_failures),
        "skipped": skipped,
        "rerun": rerun,
        "duration": duration,
        "failures": test_failures,
        "infra_failures": infra_failures,
        "passed_tests": passed_tests,
        "retried_tests": retried_tests,
    }


def send_test_notification(
    webhook_url: str | None = None,
    enabled: bool | None = None,
    results_dir: str = "allure-results",
    allure_report_url: str | None = None,
    provider: str | None = None,
    device_info: str | list[str] | None = None,
    build_number: str | None = None,
    duration: float | None = None,
    suite_name: str | None = None,
    branch: str | None = None,
    run_url: str | None = None,
) -> bool:
    """
    Convenience function to send test notification.

    Reads webhook URL and enabled flag from environment if not provided.

    Args:
        webhook_url: Slack webhook URL (reads from SLACK_WEBHOOK_URL env var if None)
        enabled: Whether notifications enabled (reads from SLACK_ENABLED env var if None)
        results_dir: Path to allure-results directory
        allure_report_url: URL to Allure report
        provider: Execution provider (reads from PROVIDER env var if None)
        device_info: Device information string or list of device strings
        build_number: Build number (reads from BUILD_NUMBER env var if None)
        duration: Test execution duration in seconds
        suite_name: Test suite name (e.g. "sanity_smoke")
        branch: Git branch name
        run_url: CI run URL (e.g. GitHub Actions run link)

    Returns:
        True if notification sent successfully
    """
    # Read from environment if not provided
    if webhook_url is None:
        webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")

    if enabled is None:
        enabled = os.getenv("SLACK_ENABLED", "false").lower() in ("true", "1", "yes")

    if provider is None:
        provider = os.getenv("PROVIDER", "local")

    if build_number is None:
        build_number = os.getenv("BUILD_NUMBER")

    if not webhook_url:
        logger.info("SLACK_WEBHOOK_URL not set, skipping notification")
        return False

    # Parse Allure results
    stats = parse_allure_results(results_dir)

    # Calculate duration if not provided
    if duration is None:
        # Try to get from pytest duration (set by conftest)
        duration = float(os.getenv("TEST_DURATION", "0"))

    # Create notifier and send
    notifier = SlackNotifier(webhook_url=webhook_url, enabled=enabled)

    return notifier.send_test_report(
        total=stats["total"],
        passed=stats["passed"],
        failed=stats["failed"],
        skipped=stats["skipped"],
        duration=duration,
        failures=stats["failures"],
        passed_tests=stats.get("passed_tests", []),
        allure_report_url=allure_report_url,
        provider=provider,
        device_info=device_info,
        build_number=build_number,
        suite_name=suite_name,
        branch=branch,
        run_url=run_url,
        rerun=stats.get("rerun", 0),
        infra_failures=stats.get("infra_failures", []),
        retried_tests=stats.get("retried_tests", []),
    )


if __name__ == "__main__":
    # Test notification
    import sys

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("❌ SLACK_WEBHOOK_URL not set")
        sys.exit(1)

    print("📤 Sending test notification to Slack...")
    success = send_test_notification(
        webhook_url=webhook_url,
        enabled=True,
        results_dir="allure-results",
    )

    if success:
        print("✅ Notification sent successfully!")
    else:
        print("❌ Failed to send notification")
        sys.exit(1)
