"""
Screen Timing Tracker - Automatic Screen Transition Performance Monitoring.

This module provides decorators that automatically track screen transition times
and report them to Allure, similar to how allure_decorators.py works for steps.

NO CHANGES NEEDED IN TEST CASES - just apply decorators to page objects!

USAGE:
======

1. Define expected times on your page class:

   @screen_timing_class
   class LoginPage(BasePage):
       # Define expected screen load times
       SCREEN_TIMING = {
           "phone_number_screen": {"expected": 8.5, "tolerance": 1.5},
           "otp_screen": {"expected": 0.8, "tolerance": 0.5},
       }

2. The decorator automatically tracks any method containing:
   - "wait_for_" (e.g., wait_for_otp_screen)
   - "is_*_visible" (e.g., is_login_screen_visible)

3. Results appear in Allure report with:
   - ✅ PASS if within tolerance
   - ⚠️ WARNING if slightly over
   - ❌ FAIL if significantly over expected time

EXAMPLE ALLURE OUTPUT:
=====================
📊 Screen Timing Report:
┌─────────────────────────────┬──────────┬──────────┬─────────┐
│ Screen                      │ Expected │ Actual   │ Status  │
├─────────────────────────────┼──────────┼──────────┼─────────┤
│ Phone Number Screen         │ 8.5s     │ 8.2s     │ ✅ PASS │
│ OTP Screen                  │ 0.8s     │ 0.6s     │ ✅ PASS │
│ Full Name Screen            │ 4.0s     │ 5.2s     │ ⚠️ WARN │
│ Chat Screen                 │ 9.0s     │ 12.1s    │ ❌ SLOW │
└─────────────────────────────┴──────────┴──────────┴─────────┘
"""

import functools
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

import allure

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================


@dataclass
class ScreenTimingResult:
    """Result of a single screen transition timing measurement."""

    screen_name: str
    expected_seconds: float
    actual_seconds: float
    tolerance_seconds: float
    timestamp: float = field(default_factory=time.time)

    @property
    def is_within_tolerance(self) -> bool:
        """Check if actual time is within expected + tolerance."""
        return self.actual_seconds <= (self.expected_seconds + self.tolerance_seconds)

    @property
    def is_warning(self) -> bool:
        """Check if actual time is between expected and expected + 2*tolerance."""
        return self.actual_seconds > self.expected_seconds and self.actual_seconds <= (
            self.expected_seconds + 2 * self.tolerance_seconds
        )

    @property
    def is_slow(self) -> bool:
        """Check if actual time is significantly over expected."""
        return self.actual_seconds > (self.expected_seconds + 2 * self.tolerance_seconds)

    @property
    def status_emoji(self) -> str:
        """Get status emoji based on timing result."""
        if self.actual_seconds <= self.expected_seconds:
            return "✅"  # Excellent - at or under expected
        elif self.is_within_tolerance:
            return "✅"  # Pass - within tolerance
        elif self.is_warning:
            return "⚠️"  # Warning - slightly over
        else:
            return "❌"  # Slow - significantly over

    @property
    def status_text(self) -> str:
        """Get status text based on timing result."""
        if self.actual_seconds <= self.expected_seconds:
            return "EXCELLENT"
        elif self.is_within_tolerance:
            return "PASS"
        elif self.is_warning:
            return "WARNING"
        else:
            return "SLOW"

    @property
    def diff_seconds(self) -> float:
        """Difference from expected time (positive = slower)."""
        return self.actual_seconds - self.expected_seconds

    def __str__(self) -> str:
        diff_sign = "+" if self.diff_seconds > 0 else ""
        return (
            f"{self.status_emoji} {self.screen_name}: "
            f"{self.actual_seconds:.1f}s (expected: {self.expected_seconds:.1f}s, "
            f"{diff_sign}{self.diff_seconds:.1f}s)"
        )


# ============================================================================
# GLOBAL TIMING TRACKER (Thread-Safe)
# ============================================================================


class ScreenTimingTracker:
    """
    Global tracker for screen transition timings.

    Thread-safe singleton that collects timing data across all page objects
    during a test session.
    """

    _instance: ClassVar["ScreenTimingTracker | None"] = None
    _instance_lock: ClassVar[threading.Lock] = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> "ScreenTimingTracker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self._results: list[ScreenTimingResult] = []
        self._start_times: dict[str, float] = {}
        self._lock: threading.Lock = threading.Lock()
        self._test_start_time: float | None = None
        self._initialized = True

        logger.debug("ScreenTimingTracker initialized")

    def start_test(self) -> None:
        """Call at the start of a test to reset timings."""
        with self._lock:
            self._results = []
            self._start_times = {}
            self._test_start_time = time.time()
            logger.info("📊 Screen timing tracker started for new test")

    def start_timer(self, screen_name: str) -> None:
        """Start timing for a screen transition."""
        with self._lock:
            self._start_times[screen_name] = time.time()
            logger.debug(f"⏱️ Timer started for: {screen_name}")

    def stop_timer(
        self,
        screen_name: str,
        expected_seconds: float,
        tolerance_seconds: float = 1.0,
    ) -> ScreenTimingResult | None:
        """
        Stop timing and record result.

        Args:
            screen_name: Human-readable screen name
            expected_seconds: Expected time for this transition
            tolerance_seconds: Acceptable variance from expected

        Returns:
            ScreenTimingResult or None if timer wasn't started
        """
        with self._lock:
            if screen_name not in self._start_times:
                logger.warning(f"Timer not started for: {screen_name}")
                return None

            start_time = self._start_times.pop(screen_name)
            actual_seconds = time.time() - start_time

            result = ScreenTimingResult(
                screen_name=screen_name,
                expected_seconds=expected_seconds,
                actual_seconds=actual_seconds,
                tolerance_seconds=tolerance_seconds,
            )

            self._results.append(result)

            # Log immediately
            logger.info(str(result))

            # Attach to current Allure step
            self._attach_timing_to_allure(result)

            return result

    def record_timing(
        self,
        screen_name: str,
        actual_seconds: float,
        expected_seconds: float,
        tolerance_seconds: float = 1.0,
    ) -> ScreenTimingResult:
        """
        Record a timing directly (when you have the actual time already).

        Args:
            screen_name: Human-readable screen name
            actual_seconds: Measured time
            expected_seconds: Expected time for this transition
            tolerance_seconds: Acceptable variance from expected

        Returns:
            ScreenTimingResult
        """
        with self._lock:
            result = ScreenTimingResult(
                screen_name=screen_name,
                expected_seconds=expected_seconds,
                actual_seconds=actual_seconds,
                tolerance_seconds=tolerance_seconds,
            )

            self._results.append(result)

            # Log immediately
            logger.info(str(result))

            # Attach to current Allure step
            self._attach_timing_to_allure(result)

            return result

    def _attach_timing_to_allure(self, result: ScreenTimingResult) -> None:
        """Attach timing result to Allure as a parameter."""
        try:
            # Add as dynamic parameter for the test
            allure.dynamic.parameter(
                f"⏱️ {result.screen_name}", f"{result.actual_seconds:.1f}s ({result.status_text})"
            )
        except Exception as e:
            logger.debug(f"Could not attach timing to Allure: {e}")

    def get_results(self) -> list[ScreenTimingResult]:
        """Get all timing results for current test."""
        with self._lock:
            return list(self._results)

    def generate_report(self) -> str:
        """Generate a formatted timing report."""
        with self._lock:
            if not self._results:
                return "No screen timing data collected."

            # Calculate column widths
            max_name_len = max(len(r.screen_name) for r in self._results)

            # Build report
            lines = [
                "📊 Screen Timing Report",
                "=" * 60,
                f"{'Screen':<{max_name_len}} │ Expected │ Actual  │ Status",
                "─" * max_name_len + "─┼──────────┼─────────┼─────────",
            ]

            for result in self._results:
                lines.append(
                    f"{result.screen_name:<{max_name_len}} │ "
                    f"{result.expected_seconds:>6.1f}s  │ "
                    f"{result.actual_seconds:>5.1f}s  │ "
                    f"{result.status_emoji} {result.status_text}"
                )

            lines.append("=" * 60)

            # Summary
            total_expected = sum(r.expected_seconds for r in self._results)
            total_actual = sum(r.actual_seconds for r in self._results)
            passed = sum(1 for r in self._results if r.is_within_tolerance)

            lines.append(f"Total: {total_actual:.1f}s (expected: {total_expected:.1f}s)")
            lines.append(f"Screens: {passed}/{len(self._results)} within tolerance")

            return "\n".join(lines)

    def attach_report_to_allure(self) -> None:
        """Attach the full timing report to Allure."""
        report = self.generate_report()

        try:
            allure.attach(
                report,
                name="📊 Screen Timing Report",
                attachment_type=allure.attachment_type.TEXT,
            )
            logger.info("Screen timing report attached to Allure")
        except Exception as e:
            logger.warning(f"Could not attach timing report to Allure: {e}")

    def attach_slt_behavior_to_allure(self) -> None:
        """
        Add Screen Load Times (SLT) to Allure's Behaviors tab.

        This creates a feature/story structure that appears in Behaviors:
        - Feature: Screen Load Times (SLT)
          - Story: OTP Screen (0.8s ✅)
          - Story: Full Name Screen (4.7s ✅)
          - etc.
        """
        try:
            # Add Feature for Screen Load Times
            allure.dynamic.feature("📊 Screen Load Times (SLT)")

            # Create summary story
            results = self.get_results()
            if not results:
                return

            # Calculate summary
            passed = sum(1 for r in results if r.is_within_tolerance)
            total = len(results)
            total_time = sum(r.actual_seconds for r in results)

            # Add story with summary
            summary = f"SLT Summary: {passed}/{total} passed, Total: {total_time:.1f}s"
            allure.dynamic.story(summary)

            # Add each screen as a label
            for result in results:
                label = f"{result.screen_name}: {result.actual_seconds:.1f}s {result.status_emoji}"
                allure.dynamic.tag(label)

            # Attach detailed SLT report as HTML for better visibility
            html_report = self._generate_slt_html_report()
            allure.attach(
                html_report,
                name="📊 Screen Load Times (SLT) - Detailed Report",
                attachment_type=allure.attachment_type.HTML,
            )

        except Exception as e:
            logger.debug(f"Could not attach SLT behavior to Allure: {e}")

    def _generate_slt_html_report(self) -> str:
        """Generate an HTML report for better Allure visibility."""
        results = self.get_results()
        if not results:
            return "<p>No screen timing data collected.</p>"

        # Calculate totals
        total_expected = sum(r.expected_seconds for r in results)
        total_actual = sum(r.actual_seconds for r in results)
        passed = sum(1 for r in results if r.is_within_tolerance)

        html = """
        <style>
            .slt-table {{ border-collapse: collapse; width: 100%; font-family: Arial, sans-serif; }}
            .slt-table th, .slt-table td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
            .slt-table th {{ background-color: #4CAF50; color: white; }}
            .slt-table tr:nth-child(even) {{ background-color: #f2f2f2; }}
            .slt-table tr:hover {{ background-color: #ddd; }}
            .pass {{ color: #4CAF50; font-weight: bold; }}
            .warn {{ color: #FF9800; font-weight: bold; }}
            .fail {{ color: #f44336; font-weight: bold; }}
            .summary {{ background-color: #333; color: white; padding: 15px; margin-bottom: 20px; border-radius: 5px; }}
            .summary h2 {{ margin: 0 0 10px 0; }}
            .summary-stats {{ display: flex; gap: 30px; }}
            .stat {{ text-align: center; }}
            .stat-value {{ font-size: 24px; font-weight: bold; }}
            .stat-label {{ font-size: 12px; color: #aaa; }}
        </style>

        <div class="summary">
            <h2>📊 Screen Load Times (SLT) Report</h2>
            <div class="summary-stats">
                <div class="stat">
                    <div class="stat-value">{passed}/{total}</div>
                    <div class="stat-label">Screens Passed</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{total_actual:.1f}s</div>
                    <div class="stat-label">Total Time</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{total_expected:.1f}s</div>
                    <div class="stat-label">Expected Time</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{diff:+.1f}s</div>
                    <div class="stat-label">Difference</div>
                </div>
            </div>
        </div>

        <table class="slt-table">
            <tr>
                <th>Screen</th>
                <th>Expected</th>
                <th>Actual</th>
                <th>Diff</th>
                <th>Status</th>
            </tr>
        """.format(  # noqa: UP032
            passed=passed,
            total=len(results),
            total_actual=total_actual,
            total_expected=total_expected,
            diff=total_actual - total_expected,
        )

        for result in results:
            diff = result.actual_seconds - result.expected_seconds
            diff_str = f"+{diff:.1f}s" if diff > 0 else f"{diff:.1f}s"

            if result.actual_seconds <= result.expected_seconds:
                status_class = "pass"
                status_text = "✅ EXCELLENT"
            elif result.is_within_tolerance:
                status_class = "pass"
                status_text = "✅ PASS"
            elif result.is_warning:
                status_class = "warn"
                status_text = "⚠️ WARNING"
            else:
                status_class = "fail"
                status_text = "❌ SLOW"

            html += f"""
            <tr>
                <td><strong>{result.screen_name}</strong></td>
                <td>{result.expected_seconds:.1f}s</td>
                <td>{result.actual_seconds:.1f}s</td>
                <td>{diff_str}</td>
                <td class="{status_class}">{status_text}</td>
            </tr>
            """

        html += """
        </table>

        <p style="margin-top: 20px; color: #666;">
            <strong>Legend:</strong><br>
            ✅ EXCELLENT = At or under expected time<br>
            ✅ PASS = Within tolerance<br>
            ⚠️ WARNING = Slightly over (up to 2x tolerance)<br>
            ❌ SLOW = Significantly over expected time
        </p>
        """

        return html


# Global tracker instance
_tracker = ScreenTimingTracker()


def get_timing_tracker() -> ScreenTimingTracker:
    """Get the global timing tracker instance."""
    return _tracker


# ============================================================================
# DEFAULT TIMING BASELINES
# ============================================================================

# These are the expected times for each screen transition.
# Override in your page class using SCREEN_TIMING attribute.

DEFAULT_SCREEN_TIMINGS = {
    # Login Flow
    "app_launch_to_phone_screen": {"expected": 8.5, "tolerance": 1.5},
    "phone_number_screen": {"expected": 8.5, "tolerance": 1.5},
    "otp_screen": {"expected": 0.8, "tolerance": 0.5},
    # Onboarding Flow
    "full_name_screen": {"expected": 4.0, "tolerance": 1.0},
    "onboarding_screen": {"expected": 4.0, "tolerance": 1.0},
    # Onboarding screens: ~2s expected (includes screen transition + all elements)
    "gender_screen": {"expected": 2.5, "tolerance": 1.0},
    "dob_screen": {"expected": 2.0, "tolerance": 1.0},
    "date_of_birth_screen": {"expected": 2.0, "tolerance": 1.0},
    "tob_screen": {"expected": 2.0, "tolerance": 1.0},
    "time_of_birth_screen": {"expected": 2.0, "tolerance": 1.0},
    "pob_screen": {"expected": 2.5, "tolerance": 1.0},
    "place_of_birth_screen": {"expected": 2.5, "tolerance": 1.0},
    "city_screen": {"expected": 0.8, "tolerance": 0.5},
    # Chat/Home
    "chat_screen": {"expected": 9.0, "tolerance": 2.0},
    "home_screen": {"expected": 3.0, "tolerance": 1.0},
}


# ============================================================================
# CLASS DECORATOR
# ============================================================================


def screen_timing_class(cls: type) -> type:
    """
    Class decorator that automatically tracks screen transition times.

    This decorator wraps methods that wait for screens (wait_for_*, is_*_visible)
    and automatically measures how long the transition takes.

    Usage:
        @screen_timing_class
        class LoginPage(BasePage):
            # Optional: Define custom timing expectations
            SCREEN_TIMING = {
                "phone_number_screen": {"expected": 8.5, "tolerance": 1.5},
            }

            def wait_for_otp_screen(self, timeout: int = 30):
                # This method will automatically track timing!
                self.wait_for_element(self.OTP_SENT_MESSAGE, ...)

    Args:
        cls: The page class to decorate

    Returns:
        The decorated class with timing tracking
    """
    # Get class-specific timing config or use defaults
    class_timings = getattr(cls, "SCREEN_TIMING", {})

    # Methods that indicate screen transitions
    TRANSITION_PATTERNS = [
        "wait_for_",  # wait_for_otp_screen, wait_for_chat_screen
        "is_",  # is_chat_screen_visible (when checking screens)
    ]

    # Methods to exclude from timing (helper/low-level methods)
    EXCLUDED_PATTERNS = [
        "wait_for_element",  # Low-level element wait
        "wait_for_text",  # Low-level text wait
        "wait_for_clickable",  # Low-level clickable wait
        "wait_for_screen_ready",  # Helper method called BY other wait_for_* methods
        "is_displayed",  # Low-level check
        "is_enabled",  # Low-level check
        "is_text_visible",  # Low-level text check (not a screen transition)
    ]

    # Custom screen name mapping (method_name -> display_name)
    SCREEN_NAME_MAP = {
        "wait_for_onboarding_screen": "Full Name Screen",
        "wait_for_login_screen": "Login Screen",
        "wait_for_otp_screen": "OTP Screen",
        "wait_for_gender_screen": "Gender Screen",
        "wait_for_dob_screen": "Date of Birth Screen",
        "wait_for_tob_screen": "Time of Birth Screen",
        "wait_for_pob_screen": "Place of Birth Screen",
        "wait_for_chat_screen": "Chat Screen",
        "wait_for_home_load": "Home Screen",
    }

    def _extract_screen_name(method_name: str) -> str:
        """Extract human-readable screen name from method name."""
        # Check custom mapping first
        if method_name in SCREEN_NAME_MAP:
            return SCREEN_NAME_MAP[method_name]

        # Remove common prefixes
        name = method_name
        for prefix in ["wait_for_", "is_"]:
            if name.startswith(prefix):
                name = name[len(prefix) :]
                break

        # Remove common suffixes
        for suffix in ["_visible", "_screen", "_loaded"]:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break

        # Convert to title case with spaces
        return name.replace("_", " ").title() + " Screen"

    def _get_timing_config(screen_name: str) -> dict[str, float]:
        """Get timing configuration for a screen."""
        # Normalize screen name for lookup
        lookup_key = screen_name.lower().replace(" ", "_")

        # Check class-specific config first
        if lookup_key in class_timings:
            return class_timings[lookup_key]

        # Fall back to defaults
        if lookup_key in DEFAULT_SCREEN_TIMINGS:
            return DEFAULT_SCREEN_TIMINGS[lookup_key]

        # Default fallback
        return {"expected": 2.0, "tolerance": 1.0}

    def _should_track(method_name: str) -> bool:
        """Check if method should be tracked for timing."""
        # Skip excluded patterns
        if any(method_name.startswith(ex) for ex in EXCLUDED_PATTERNS):
            return False

        # Check for transition patterns
        return any(method_name.startswith(pat) for pat in TRANSITION_PATTERNS)

    def _wrap_with_timing(method: Callable[..., Any], method_name: str) -> Callable[..., Any]:
        """Wrap a method with timing tracking."""

        @functools.wraps(method)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            screen_name = _extract_screen_name(method_name)
            config = _get_timing_config(screen_name)

            tracker = get_timing_tracker()

            # Start timer
            start_time = time.time()

            try:
                # Execute original method
                result = method(self, *args, **kwargs)

                # Record timing
                actual_time = time.time() - start_time
                tracker.record_timing(
                    screen_name=screen_name,
                    actual_seconds=actual_time,
                    expected_seconds=config["expected"],
                    tolerance_seconds=config["tolerance"],
                )

                return result

            except Exception as e:
                # Still record timing even if method fails
                actual_time = time.time() - start_time
                logger.warning(f"⏱️ {screen_name}: {actual_time:.1f}s (FAILED: {type(e).__name__})")
                raise

        return wrapper

    # Wrap relevant methods
    for attr_name in dir(cls):
        # Skip private/magic methods
        if attr_name.startswith("_"):
            continue

        # Check if method should be tracked
        if not _should_track(attr_name):
            continue

        attr = getattr(cls, attr_name)

        # Only wrap callable methods
        if callable(attr) and not isinstance(attr, (type, property, staticmethod, classmethod)):
            wrapped = _wrap_with_timing(attr, attr_name)
            setattr(cls, attr_name, wrapped)
            logger.debug(f"📊 Timing enabled for: {cls.__name__}.{attr_name}")

    return cls


# ============================================================================
# METHOD DECORATOR (for manual use)
# ============================================================================


def track_screen_timing(
    screen_name: str | None = None,
    expected_seconds: float | None = None,
    tolerance_seconds: float = 1.0,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Method decorator to manually track a specific screen transition.

    Use this when you want explicit control over timing tracking.

    Usage:
        @track_screen_timing("Chat Screen", expected_seconds=9.0)
        def wait_for_chat_screen(self, timeout: int = 30):
            self.wait_for_element(self.CHAT_INPUT, ...)

    Args:
        screen_name: Human-readable screen name (auto-generated if None)
        expected_seconds: Expected transition time (uses default if None)
        tolerance_seconds: Acceptable variance from expected

    Returns:
        Decorator function
    """

    def decorator(method: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(method)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Determine screen name
            name = screen_name or method.__name__.replace("_", " ").title()

            # Determine expected time
            if expected_seconds is not None:
                expected = expected_seconds
            else:
                lookup_key = name.lower().replace(" ", "_")
                config = DEFAULT_SCREEN_TIMINGS.get(lookup_key, {"expected": 2.0, "tolerance": 1.0})
                expected = config["expected"]

            tracker = get_timing_tracker()

            # Start timer
            start_time = time.time()

            try:
                # Execute original method
                result = method(*args, **kwargs)

                # Record timing
                actual_time = time.time() - start_time
                tracker.record_timing(
                    screen_name=name,
                    actual_seconds=actual_time,
                    expected_seconds=expected,
                    tolerance_seconds=tolerance_seconds,
                )

                return result

            except Exception:
                actual_time = time.time() - start_time
                logger.warning(f"⏱️ {name}: {actual_time:.1f}s (FAILED)")
                raise

        return wrapper

    return decorator


# ============================================================================
# PYTEST FIXTURES & HOOKS HELPERS
# ============================================================================


def start_timing_session() -> None:
    """Call at the start of a test to reset the tracker."""
    get_timing_tracker().start_test()


def end_timing_session() -> None:
    """Call at the end of a test to finalize and attach report."""
    tracker = get_timing_tracker()
    tracker.attach_report_to_allure()
    tracker.attach_slt_behavior_to_allure()  # Add to Behaviors tab

    # Log summary
    report = tracker.generate_report()
    logger.info("\n" + report)


def get_timing_summary() -> dict[str, Any]:
    """Get a summary of all timing results for the current test."""
    tracker = get_timing_tracker()
    results = tracker.get_results()

    if not results:
        return {"screens": [], "total_actual": 0, "total_expected": 0}

    return {
        "screens": [
            {
                "name": r.screen_name,
                "expected": r.expected_seconds,
                "actual": r.actual_seconds,
                "status": r.status_text,
            }
            for r in results
        ],
        "total_actual": sum(r.actual_seconds for r in results),
        "total_expected": sum(r.expected_seconds for r in results),
        "passed": sum(1 for r in results if r.is_within_tolerance),
        "total": len(results),
    }
