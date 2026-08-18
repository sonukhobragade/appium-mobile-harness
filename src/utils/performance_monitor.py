"""
Performance Monitoring Utility for Mobile Test Automation.

Tracks key performance metrics:
- TTID (Time To Initial Display): When first frame is drawn (Android system metric)
- TTFD (Time To Full Display): When screen is fully loaded and interactive
- Screen-wise performance breakdowns
- Compares Android system metrics vs Appium-perceived metrics
"""

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field

import allure

logger = logging.getLogger(__name__)


@dataclass
class ScreenMetrics:
    """Performance metrics for a single screen

    Tracks both Android system-reported metrics and Appium-perceived metrics:
    - system_ttid: Android's "Displayed" timestamp (ground truth)
    - system_ttfd: Android's "Fully drawn" timestamp (if app calls reportFullyDrawn())
    - appium_ttid: When Appium detects first element (includes overhead)
    - appium_ttfd: When Appium verifies screen is interactive (includes overhead)
    """

    screen_name: str
    # Android system metrics (from logcat)
    system_ttid: float = 0.0  # Time To Initial Display (ActivityManager: Displayed)
    system_ttfd: float = 0.0  # Time To Full Display (ActivityManager: Fully drawn)

    # Appium-perceived metrics (includes instrumentation overhead)
    appium_ttid: float = 0.0  # When Appium detects first element
    appium_ttfd: float = 0.0  # When Appium verifies interactive state

    # Legacy names (for backward compatibility, map to appium metrics)
    ttfd: float = 0.0  # Deprecated: use appium_ttfd
    ttid: float = 0.0  # Deprecated: use appium_ttid

    total_load_time: float = 0.0  # Total time from navigation to interactive
    element_verification_time: float = 0.0  # Time to verify key elements
    overhead_ttid: float = 0.0  # Difference between appium and system TTID
    overhead_ttfd: float = 0.0  # Difference between appium and system TTFD
    notes: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for reporting"""
        return {
            "screen_name": self.screen_name,
            "system_ttid_seconds": round(self.system_ttid, 3),
            "system_ttfd_seconds": round(self.system_ttfd, 3),
            "appium_ttid_seconds": round(self.appium_ttid, 3),
            "appium_ttfd_seconds": round(self.appium_ttfd, 3),
            "overhead_ttid_seconds": round(self.overhead_ttid, 3),
            "overhead_ttfd_seconds": round(self.overhead_ttfd, 3),
            "total_load_time_seconds": round(self.total_load_time, 3),
            "element_verification_time_seconds": round(self.element_verification_time, 3),
            "notes": self.notes,
        }

    def __str__(self) -> str:
        result = f"Screen: {self.screen_name}\n"
        if self.system_ttid > 0:
            result += f"  System TTID: {self.system_ttid:.3f}s (Android ground truth)\n"
        if self.appium_ttid > 0:
            result += f"  Appium TTID: {self.appium_ttid:.3f}s"
            if self.overhead_ttid > 0:
                result += f" (overhead: +{self.overhead_ttid:.3f}s)\n"
            else:
                result += "\n"
        if self.system_ttfd > 0:
            result += f"  System TTFD: {self.system_ttfd:.3f}s (Android ground truth)\n"
        if self.appium_ttfd > 0:
            result += f"  Appium TTFD: {self.appium_ttfd:.3f}s"
            if self.overhead_ttfd > 0:
                result += f" (overhead: +{self.overhead_ttfd:.3f}s)\n"
            else:
                result += "\n"
        result += f"  Total Load: {self.total_load_time:.3f}s\n"
        result += f"  Element Verification: {self.element_verification_time:.3f}s"
        return result


@dataclass
class PerformanceMetrics:
    """Complete performance metrics for a test flow"""

    test_name: str
    app_launch_time: float = 0.0
    session_start_time: float = 0.0
    screens: dict[str, ScreenMetrics] = field(default_factory=dict)
    total_test_duration: float = 0.0
    device_id: str | None = None
    platform: str | None = None

    def add_screen_metrics(self, screen_name: str, metrics: ScreenMetrics):
        """Add metrics for a screen"""
        self.screens[screen_name] = metrics

    def get_screen_metrics(self, screen_name: str) -> ScreenMetrics | None:
        """Get metrics for a specific screen"""
        return self.screens.get(screen_name)

    def to_dict(self) -> dict:
        """Convert to dictionary for reporting"""
        return {
            "test_name": self.test_name,
            "device_id": self.device_id,
            "platform": self.platform,
            "app_launch_time_seconds": round(self.app_launch_time, 3),
            "session_start_time_seconds": round(self.session_start_time, 3),
            "total_test_duration_seconds": round(self.total_test_duration, 3),
            "screens": {name: metrics.to_dict() for name, metrics in self.screens.items()},
        }

    def attach_to_allure(self):
        """Attach performance metrics to Allure report"""
        metrics_dict = self.to_dict()

        # Attach as JSON
        import json

        allure.attach(
            json.dumps(metrics_dict, indent=2),
            name="📊 Performance Metrics",
            attachment_type=allure.attachment_type.JSON,
        )

        # Attach summary as text
        summary = self._generate_summary()
        allure.attach(
            summary,
            name="⏱️ Performance Summary",
            attachment_type=allure.attachment_type.TEXT,
        )

    def _generate_summary(self) -> str:
        """Generate human-readable summary"""
        lines = [
            f"Performance Metrics: {self.test_name}",
            "=" * 60,
            f"Device: {self.device_id or 'N/A'}",
            f"Platform: {self.platform or 'N/A'}",
            "",
            "Timing Overview:",
            f"  Session Start: {self.session_start_time:.3f}s",
            f"  App Launch: {self.app_launch_time:.3f}s",
            f"  Total Duration: {self.total_test_duration:.3f}s",
            "",
            "Screen Performance:",
        ]

        for _screen_name, metrics in self.screens.items():
            lines.append(f"\n{metrics}")

        return "\n".join(lines)


class PerformanceMonitor:
    """
    Performance monitoring utility for tracking screen load times.

    Tracks:
    - TTFD (Time To First Draw): When screen first renders
    - TTID (Time To Interactive Draw): When screen becomes interactive
    - Screen-wise breakdowns
    """

    def __init__(self, test_name: str, device_id: str | None = None, platform: str | None = None):
        """
        Initialize performance monitor

        Args:
            test_name: Name of the test being monitored
            device_id: Device ID (optional)
            platform: Platform name (optional)
        """
        self.test_name = test_name
        self.device_id = device_id
        self.platform = platform
        self.metrics = PerformanceMetrics(test_name, device_id=device_id, platform=platform)
        self.start_time = time.time()
        self.screen_start_times: dict[str, float] = {}

        logger.info(f"📊 Performance Monitor initialized for: {test_name}")

    def mark_session_start(self):
        """Mark when Appium session starts"""
        elapsed = time.time() - self.start_time
        self.metrics.session_start_time = elapsed
        logger.info(f"⏱️ Session start time: {elapsed:.3f}s")

    def mark_app_launch(self):
        """Mark when app launch completes"""
        elapsed = time.time() - self.start_time
        self.metrics.app_launch_time = elapsed
        logger.info(f"⏱️ App launch time: {elapsed:.3f}s")

    def start_screen_timing(self, screen_name: str):
        """
        Start timing a screen transition

        Args:
            screen_name: Name of the screen being loaded
        """
        self.screen_start_times[screen_name] = time.time()
        logger.info(f"📱 Starting timing for screen: {screen_name}")

    def mark_ttfd(self, screen_name: str, element_locator: str | None = None):
        """
        Mark Appium-perceived Time To First Draw (when Appium detects first element).

        Note: This measures when Appium sees the element, NOT when Android drew it.
        For Android ground truth, use capture_logcat_ttid() instead.

        Args:
            screen_name: Name of the screen
            element_locator: Optional locator of first visible element
        """
        if screen_name not in self.screen_start_times:
            self.start_screen_timing(screen_name)

        screen_start = self.screen_start_times[screen_name]
        appium_time = time.time() - screen_start

        if screen_name not in self.metrics.screens:
            self.metrics.screens[screen_name] = ScreenMetrics(screen_name)

        # Set both appium metric and legacy field
        self.metrics.screens[screen_name].appium_ttfd = appium_time
        self.metrics.screens[screen_name].ttfd = appium_time  # Backward compatibility

        logger.info(
            f"⏱️ Appium TTFD for {screen_name}: {appium_time:.3f}s "
            f"(element: {element_locator or 'N/A'})"
        )

        # Calculate overhead if system TTID available
        if self.metrics.screens[screen_name].system_ttid > 0:
            overhead = appium_time - self.metrics.screens[screen_name].system_ttid
            self.metrics.screens[screen_name].overhead_ttid = overhead
            logger.info(f"📊 Appium overhead: +{overhead:.3f}s vs system TTID")

    def mark_ttid(self, screen_name: str, interactive_element: str | None = None):
        """
        Mark Appium-perceived Time To Interactive Draw (when Appium verifies interactivity).

        Note: This measures when Appium confirms interactivity, NOT Android's TTID.
        For Android ground truth, use capture_logcat_ttid() instead.

        Args:
            screen_name: Name of the screen
            interactive_element: Optional locator of interactive element
        """
        if screen_name not in self.screen_start_times:
            self.start_screen_timing(screen_name)

        screen_start = self.screen_start_times[screen_name]
        appium_time = time.time() - screen_start

        if screen_name not in self.metrics.screens:
            self.metrics.screens[screen_name] = ScreenMetrics(screen_name)

        # Set both appium metric and legacy field
        self.metrics.screens[screen_name].appium_ttid = appium_time
        self.metrics.screens[screen_name].ttid = appium_time  # Backward compatibility
        self.metrics.screens[screen_name].total_load_time = appium_time

        logger.info(
            f"⏱️ Appium TTID for {screen_name}: {appium_time:.3f}s "
            f"(element: {interactive_element or 'N/A'})"
        )

        # Calculate overhead if system TTID available
        if self.metrics.screens[screen_name].system_ttid > 0:
            overhead = appium_time - self.metrics.screens[screen_name].system_ttid
            self.metrics.screens[screen_name].overhead_ttid = overhead
            logger.info(f"📊 Appium overhead: +{overhead:.3f}s vs system TTID")

    def mark_element_verification(self, screen_name: str, verification_time: float):
        """
        Mark time taken for element verification

        Args:
            screen_name: Name of the screen
            verification_time: Time taken to verify elements (seconds)
        """
        if screen_name not in self.metrics.screens:
            self.metrics.screens[screen_name] = ScreenMetrics(screen_name)

        self.metrics.screens[screen_name].element_verification_time = verification_time
        logger.info(f"⏱️ Element verification for {screen_name}: {verification_time:.3f}s")

    def add_screen_note(self, screen_name: str, note: str):
        """Add a note to screen metrics"""
        if screen_name not in self.metrics.screens:
            self.metrics.screens[screen_name] = ScreenMetrics(screen_name)

        self.metrics.screens[screen_name].notes = note

    def complete_screen_timing(self, screen_name: str):
        """Complete timing for a screen"""
        if screen_name in self.screen_start_times:
            screen_start = self.screen_start_times[screen_name]
            total_time = time.time() - screen_start

            if screen_name not in self.metrics.screens:
                self.metrics.screens[screen_name] = ScreenMetrics(screen_name)

            if self.metrics.screens[screen_name].total_load_time == 0:
                self.metrics.screens[screen_name].total_load_time = total_time

            logger.info(f"✅ Completed timing for {screen_name}: {total_time:.3f}s")

    def finalize(self):
        """Finalize metrics and attach to Allure"""
        self.metrics.total_test_duration = time.time() - self.start_time
        logger.info(f"📊 Test duration: {self.metrics.total_test_duration:.3f}s")

        # Attach to Allure
        self.metrics.attach_to_allure()

        # Log summary
        logger.info("=" * 80)
        logger.info("📊 PERFORMANCE METRICS SUMMARY")
        logger.info("=" * 80)
        logger.info(self.metrics._generate_summary())
        logger.info("=" * 80)

        return self.metrics

    def capture_logcat_ttid(
        self, screen_name: str, activity_name: str, since_timestamp: float | None = None
    ) -> float:
        """
        Capture Android system TTID from logcat.

        Parses logcat for "ActivityManager: Displayed" line which shows when
        Android system drew the first frame (ground truth).

        Args:
            screen_name: Name of screen for metrics
            activity_name: Activity name to search for (e.g., ".MainActivity")
            since_timestamp: Optional start time for filtering logs

        Returns:
            TTID in seconds (0.0 if not found)

        Example logcat line:
            ActivityManager: Displayed com.example/.MainActivity: +2s534ms
        """
        if not self.device_id or self.platform != "android":
            logger.warning("⚠️ System TTID capture only supported on Android")
            return 0.0

        try:
            # Get recent logcat (last 1000 lines to find the Displayed message)
            # Do NOT clear logcat here - the activity was already displayed!
            logcat_cmd = f"adb -s {self.device_id} logcat -d -t 1000 ActivityManager:I *:S"
            result = subprocess.run(
                logcat_cmd, shell=True, capture_output=True, text=True, timeout=5
            )

            if result.returncode != 0:
                logger.warning(f"⚠️ Failed to capture logcat: {result.stderr}")
                return 0.0

            # Parse for "Displayed" line
            # Pattern: Displayed com.example.app/.MainActivity: +2s534ms
            pattern = rf"Displayed.*{re.escape(activity_name)}.*\+(\d+)s(\d+)ms"
            match = re.search(pattern, result.stdout)

            if match:
                seconds = int(match.group(1))
                millis = int(match.group(2))
                ttid = seconds + (millis / 1000.0)

                logger.info(f"✅ System TTID for {screen_name}: {ttid:.3f}s (from Android)")

                # Store in metrics
                if screen_name not in self.metrics.screens:
                    self.metrics.screens[screen_name] = ScreenMetrics(screen_name)

                self.metrics.screens[screen_name].system_ttid = ttid

                # Calculate overhead if Appium TTID available
                if self.metrics.screens[screen_name].appium_ttid > 0:
                    overhead = self.metrics.screens[screen_name].appium_ttid - ttid
                    self.metrics.screens[screen_name].overhead_ttid = overhead
                    logger.info(f"📊 Appium overhead for {screen_name} TTID: +{overhead:.3f}s")

                return ttid
            else:
                logger.warning(f"⚠️ No 'Displayed' line found for {activity_name} in logcat")
                return 0.0

        except subprocess.TimeoutExpired:
            logger.error("❌ Logcat capture timed out")
            return 0.0
        except Exception as e:
            logger.error(f"❌ Error capturing system TTID: {e}")
            return 0.0

    def capture_logcat_ttfd(
        self, screen_name: str, activity_name: str, since_timestamp: float | None = None
    ) -> float:
        """
        Capture Android system TTFD from logcat.

        Parses logcat for "ActivityManager: Fully drawn" line which shows when
        the app called reportFullyDrawn() (requires app instrumentation).

        Args:
            screen_name: Name of screen for metrics
            activity_name: Activity name to search for
            since_timestamp: Optional start time for filtering logs

        Returns:
            TTFD in seconds (0.0 if not found)

        Example logcat line:
            ActivityManager: Fully drawn {package}/.MainActivity: +3s100ms

        Note: Requires app to call Activity.reportFullyDrawn()
        """
        if not self.device_id or self.platform != "android":
            logger.warning("⚠️ System TTFD capture only supported on Android")
            return 0.0

        try:
            # Get recent logcat (assume already cleared by TTID capture)
            logcat_cmd = f"adb -s {self.device_id} logcat -d ActivityManager:I *:S"
            result = subprocess.run(
                logcat_cmd, shell=True, capture_output=True, text=True, timeout=5
            )

            if result.returncode != 0:
                logger.warning(f"⚠️ Failed to capture logcat: {result.stderr}")
                return 0.0

            # Parse for "Fully drawn" line
            # Pattern: Fully drawn {com.example/.MainActivity}: +3s100ms
            pattern = rf"Fully drawn.*{re.escape(activity_name)}.*\+(\d+)s(\d+)ms"
            match = re.search(pattern, result.stdout)

            if match:
                seconds = int(match.group(1))
                millis = int(match.group(2))
                ttfd = seconds + (millis / 1000.0)

                logger.info(f"✅ System TTFD for {screen_name}: {ttfd:.3f}s (from Android)")

                # Store in metrics
                if screen_name not in self.metrics.screens:
                    self.metrics.screens[screen_name] = ScreenMetrics(screen_name)

                self.metrics.screens[screen_name].system_ttfd = ttfd

                # Calculate overhead if Appium TTFD available
                if self.metrics.screens[screen_name].appium_ttfd > 0:
                    overhead = self.metrics.screens[screen_name].appium_ttfd - ttfd
                    self.metrics.screens[screen_name].overhead_ttfd = overhead
                    logger.info(f"📊 Appium overhead for {screen_name} TTFD: +{overhead:.3f}s")

                return ttfd
            else:
                logger.info(
                    f"ℹ️  No 'Fully drawn' line found for {activity_name} "
                    "(app may not call reportFullyDrawn())"
                )
                return 0.0

        except subprocess.TimeoutExpired:
            logger.error("❌ Logcat capture timed out")
            return 0.0
        except Exception as e:
            logger.error(f"❌ Error capturing system TTFD: {e}")
            return 0.0

    def capture_react_native_startup(self, screen_name: str, app_name: str) -> dict[str, float]:
        """
        Capture React Native app startup time from logcat.

        Parses React Native specific logs to get JS bundle load time
        and app initialization time.

        Args:
            screen_name: Name of screen for metrics
            app_name: React Native app name (e.g., "ExampleApp")

        Returns:
            Dict with bundle_load_time, app_start_time in seconds

        Example logcat lines:
            ReactNativeJS: Running application "ExampleApp"...
            ReactNative: Loaded bundle in 1234ms
        """
        if not self.device_id or self.platform != "android":
            logger.warning("⚠️ React Native capture only supported on Android")
            return {"bundle_load_time": 0.0, "app_start_time": 0.0}

        try:
            # Get React Native logs
            logcat_cmd = (
                f"adb -s {self.device_id} logcat -d -t 1000 ReactNativeJS:I ReactNative:I *:S"
            )
            result = subprocess.run(
                logcat_cmd, shell=True, capture_output=True, text=True, timeout=5
            )

            if result.returncode != 0:
                logger.warning(f"⚠️ Failed to capture logcat: {result.stderr}")
                return {"bundle_load_time": 0.0, "app_start_time": 0.0}

            metrics = {"bundle_load_time": 0.0, "app_start_time": 0.0}

            # Parse for "Loaded bundle in XXXXms"
            bundle_pattern = r"Loaded bundle in (\d+)ms"
            bundle_match = re.search(bundle_pattern, result.stdout)

            if bundle_match:
                bundle_ms = int(bundle_match.group(1))
                bundle_time = bundle_ms / 1000.0
                metrics["bundle_load_time"] = bundle_time
                logger.info(f"✅ React Native bundle loaded in {bundle_time:.3f}s (from logcat)")

                # Store in metrics
                if screen_name not in self.metrics.screens:
                    self.metrics.screens[screen_name] = ScreenMetrics(screen_name)

                # Use bundle load time as a baseline for system TTID
                self.metrics.screens[screen_name].system_ttid = bundle_time
                self.metrics.screens[screen_name].notes += f" RN bundle: {bundle_time:.3f}s;"
            else:
                logger.info("ℹ️  No 'Loaded bundle' line found in React Native logs")

            # Parse for "Running application"
            app_pattern = rf"Running application.*{re.escape(app_name)}"
            app_match = re.search(app_pattern, result.stdout)

            if app_match:
                logger.info(f"✅ React Native app '{app_name}' started")
                # Note: This log doesn't have timing, but confirms app started
                # The actual TTID should be measured when first UI element appears

            return metrics

        except subprocess.TimeoutExpired:
            logger.error("❌ Logcat capture timed out")
            return {"bundle_load_time": 0.0, "app_start_time": 0.0}
        except Exception as e:
            logger.error(f"❌ Error capturing React Native startup: {e}")
            return {"bundle_load_time": 0.0, "app_start_time": 0.0}

    def get_metrics(self) -> PerformanceMetrics:
        """Get current metrics"""
        return self.metrics
