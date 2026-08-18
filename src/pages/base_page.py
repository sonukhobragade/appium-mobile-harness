"""
Base Page class with optimized Appium methods for fast element discovery.

PERFORMANCE OPTIMIZATIONS (vs original):
1. tap() — NO post-tap sleep (was 1.5s per tap = 15s for 10 taps)
2. find_element() — uses visibility_of_element (not presence) by default
3. is_displayed() — single Appium call (was 2: find + is_displayed)
4. tap() — visible = tap immediately, no clickable check needed
5. safe_tap() — removed redundant displayed + enabled checks
6. Platform-aware locator helpers (_by_id, _by_desc, etc.)

WAIT STRATEGY:
1. Implicit Wait (10s): Set globally in driver, applies to ALL findElement calls
2. Explicit Wait (10s): Used in this class for specific conditions (visibility, clickability)
3. Fluent Wait: Custom polling for unpredictable elements (OTP, permissions)

LOCATOR SPEED — PLATFORM COMPARISON:
┌──────────────────────────────┬────────────────────┬──────────────────────┐
│ Strategy                     │ Android (UiAuto2)  │ iOS (XCUITest)       │
├──────────────────────────────┼────────────────────┼──────────────────────┤
│ resource-id / testID         │ ⚡ 0.3s FASTEST    │ N/A (uses acc. ID)   │
│ Accessibility ID             │ ⛔ 30s+ SLOW in RN │ ⚡ FAST (native)     │
│ UiAutomator description()    │ ⚡ 0.5s FAST       │ N/A (Android only)   │
│ iOS Predicate String         │ N/A (iOS only)     │ ⚡ FAST (native)     │
│ iOS Class Chain              │ N/A (iOS only)     │ ⚡ FAST (native)     │
│ XPath                        │ ❌ 2-5s SLOW       │ ❌ up to 10x slower  │
└──────────────────────────────┴────────────────────┴──────────────────────┘

REACT NATIVE PROP MAPPING:
  testID            → Android: resource-id (0.3s)  | iOS: accessibilityIdentifier = accessibility id (FAST)
  accessibilityLabel→ Android: content-desc (0.5s via UiAutomator) | iOS: label/name via acc. id (FAST)

⚠️ CRITICAL DIFFERENCE:
  Android: AppiumBy.ACCESSIBILITY_ID waits for RN accessibility loading = 30s+ → USE UiAutomator instead
  iOS:     AppiumBy.ACCESSIBILITY_ID is the NATIVE fast lookup — perfectly fine to use

CROSS-PLATFORM HELPERS (use these, not raw AppiumBy):
  _by_id(rid)          → testID lookup: Android=resource-id | iOS=accessibility_id
  _by_desc(desc)       → content-desc: Android=UiAutomator description() | iOS=accessibility_id
  _by_text(text)       → exact text:   Android=UiAutomator text() | iOS=predicate label==
  _by_text_contains(t) → partial text: Android=UiAutomator textContains() | iOS=predicate CONTAINS
  _by_predicate(pred)  → iOS native predicate (Android falls back to XPath)
  _by_class_chain(ch)  → iOS class chain (Android falls back to XPath)
"""

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, overload
from urllib.parse import urlparse

from appium import webdriver
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.wait import WebDriverWait as FluentWait
from urllib3.exceptions import ReadTimeoutError

# Import Allure auto-step decorator
from src.reporting.allure_decorators import allure_step_class

logger = logging.getLogger(__name__)


def retry_on_stale(max_attempts: int = 3, backoff: float = 0.2):
    """Retry an action when the underlying element handle goes stale.

    React Native + Fabric re-renders views on every state change → cached element
    references invalidate mid-action. Wrapped methods must accept (locator, by, ...)
    so the locator can be re-resolved on retry — never wrap methods that take a
    pre-resolved WebElement.
    """
    stale_markers = ("stale", "no longer in the layout", "no longer attached")

    def deco(fn):
        def wrapper(self, *args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return fn(self, *args, **kwargs)
                except StaleElementReferenceException as e:
                    last_exc = e
                except WebDriverException as e:
                    msg = str(e).lower()
                    if not any(m in msg for m in stale_markers):
                        raise
                    last_exc = e
                if attempt < max_attempts - 1:
                    time.sleep(backoff * (attempt + 1))
                    logger.debug(
                        f"retry_on_stale: {fn.__name__} attempt {attempt + 1}/{max_attempts} "
                        f"after stale ref — re-resolving locator"
                    )
            raise last_exc

        wrapper.__wrapped__ = fn
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return deco


@dataclass
class DeviceProfile:
    """Device-specific screen characteristics for cloud testing."""

    name: str
    width: int
    height: int
    aspect_ratio: float
    safe_top: float  # % of screen height for status bar/notch
    safe_bottom: float  # % for navigation bar
    safe_left: float  # % for edges
    safe_right: float  # % for edges
    scroll_distance_pct: float  # Optimal scroll distance

    @property
    def usable_height_pct(self) -> float:
        """Content area excluding safe areas."""
        return 1.0 - self.safe_top - self.safe_bottom


# MIUI safe areas: thicker status bar (7%) + nav bar (10%) than Samsung
_MIUI_20_9 = (1080, 2400, 20 / 9, 0.07, 0.10, 0.03, 0.03, 0.30)

# Common cloud device registry
DEVICE_PROFILES = {
    # Samsung Galaxy devices
    "Galaxy S21 5G": DeviceProfile(
        "Galaxy S21 5G", 1080, 2400, 20 / 9, 0.05, 0.08, 0.02, 0.02, 0.35
    ),
    "Galaxy S20": DeviceProfile("Galaxy S20", 1440, 3200, 20 / 9, 0.05, 0.08, 0.02, 0.02, 0.35),
    "Galaxy S22": DeviceProfile("Galaxy S22", 1080, 2340, 19.5 / 9, 0.05, 0.08, 0.02, 0.02, 0.35),
    # Xiaomi / Redmi / MIUI devices
    "Redmi Note 13 Pro": DeviceProfile("Redmi Note 13 Pro", *_MIUI_20_9),
    "Redmi Note 12 Pro": DeviceProfile("Redmi Note 12 Pro", *_MIUI_20_9),
    "Redmi Note 11": DeviceProfile("Redmi Note 11", *_MIUI_20_9),
    "POCO F5": DeviceProfile("POCO F5", *_MIUI_20_9),
    # Fallback profiles by aspect ratio (for unknown devices)
    "FALLBACK_MIUI_20_9": DeviceProfile("Generic MIUI 20:9", *_MIUI_20_9),
    "FALLBACK_20_9": DeviceProfile(
        "Generic 20:9", 1080, 2400, 20 / 9, 0.05, 0.08, 0.02, 0.02, 0.35
    ),
    "FALLBACK_19_9": DeviceProfile(
        "Generic 19:9", 1080, 2280, 19 / 9, 0.04, 0.07, 0.02, 0.02, 0.40
    ),
    "FALLBACK_16_9": DeviceProfile(
        "Generic 16:9", 1080, 1920, 16 / 9, 0.03, 0.05, 0.02, 0.02, 0.40
    ),
}

ASPECT_RATIO_TOLERANCE = 0.1  # For fallback matching


@allure_step_class
class BasePage:
    """
    Base class for all page objects in the framework.

    ALLURE STEP REPORTING:
    This class is decorated with @allure_step_class, which automatically wraps
    PAGE-LEVEL business methods with allure.step() for automatic test reporting.

    LOW-LEVEL METHODS EXCLUDED:
    Infrastructure methods (find_element, tap, input_text, etc.) are automatically
    excluded from step reporting to keep Allure reports clean and business-focused.
    See src/reporting/allure_decorators.py for the complete exclusion list.

    WHAT APPEARS IN REPORTS:
    - ✓ Page-level methods in child classes (enter_phone_number, verify_login, etc.)
    - ✗ Low-level BasePage methods (find_element, tap, wait_for_element, etc.)

    All child page objects (LoginPage, HomePage, etc.) inherit this behavior,
    eliminating the need for manual 'with allure.step()' blocks in tests.
    """

    # Tab names from /client/config API (set once via configure_tab_names)
    # Defaults used until API config is loaded
    _tab_names: dict[str, str] = {
        "home": "Home",
        "reports": "Reports",
        "match-making": "Match",
        "almanac": "Almanac",
        "mychats": "All Chats",
    }

    @classmethod
    def configure_tab_names(cls, tabs: list[dict]) -> None:
        """Set tab names from tabNavigationConfigV2.tabs API response.

        Called once from conftest after fetching /client/config.
        All page objects inherit these values.
        """
        for tab in tabs:
            if tab.get("enabled"):
                cls._tab_names[tab["key"]] = tab["title"]
        logger.info(f"Tab names configured from API: {cls._tab_names}")

    # Tab name properties — all page objects inherit these
    @property
    def TAB_HOME(self):
        return self._tab_names["home"]

    @property
    def TAB_REPORTS(self):
        return self._tab_names["reports"]

    @property
    def TAB_MATCH(self):
        return self._tab_names["match-making"]

    @property
    def TAB_ALMANAC(self):
        return self._tab_names["almanac"]

    @property
    def TAB_CHATS(self):
        return self._tab_names.get("mychats", "All Chats")

    def __init__(self, driver: webdriver.Remote, timeout: int | None = None):
        """
        Initialize base page with driver and default timeout.

        Args:
            driver: Appium WebDriver instance
            timeout: Explicit wait timeout (default: from EXPLICIT_WAIT_SECONDS env var or 10s)

        Note:
            - Implicit wait is already set globally in driver (see AppiumClient)
            - This timeout is for explicit waits (WebDriverWait) only
        """
        self.driver = driver

        # Get explicit wait timeout from env or use provided/default
        if timeout is None:
            timeout = int(os.getenv("EXPLICIT_WAIT_SECONDS", "10"))

        self.timeout = timeout
        self.wait = WebDriverWait(driver, timeout)

        # Fluent wait poll frequency from env
        self.fluent_poll_frequency = float(os.getenv("FLUENT_WAIT_POLL_FREQUENCY", "0.5"))

        # Platform detection for cross-platform locator helpers
        self.platform = driver.capabilities.get("platformName", "Android").lower()

        # Device manufacturer detection (for OEM-specific workarounds)
        # Works on both local (from ADB) and LambdaTest (from capabilities)
        from scripts.lib.oem_families import resolve_oem_family

        from src.platform.oem_policy import OEM_POLICIES

        _caps = getattr(self.driver, "capabilities", None) or {}
        _device_name = _caps.get("deviceName", "") or ""
        _manufacturer = _caps.get("deviceManufacturer", "") or ""

        self.oem_family = resolve_oem_family(_device_name, _manufacturer)
        self.oem_policy = OEM_POLICIES.get(self.oem_family, OEM_POLICIES["unknown_android"])
        self.is_cloud_provider = self._detect_cloud_provider()
        self._supports_clear_accessibility_cache = not self.is_cloud_provider

        # Backward compat — derived from oem_family. Do NOT remove. 12+ call sites depend on these.
        self.is_miui = self.oem_family == "miui"
        self.is_chinese_oem = self.oem_family in {"miui", "coloros_funtouch", "emui_magicui"}

        # Device profile detection for cloud testing
        self._device_profile: DeviceProfile | None = None
        self._screen_size_cache: dict | None = None
        self._orientation_cache: str | None = None
        self._detect_device_profile()  # Initialize device characteristics

        # Self-healing locator support (opt-in via SELF_HEAL=true env var)
        self._self_heal_enabled = os.getenv("SELF_HEAL", "").lower() in (
            "true",
            "1",
            "yes",
        )

        logger.debug(
            f"Initialized {self.__class__.__name__} - "
            f"Explicit wait: {timeout}s, Fluent poll: {self.fluent_poll_frequency}s, "
            f"Platform: {self.platform}, Self-heal: {self._self_heal_enabled}"
        )

    def _detect_cloud_provider(self) -> bool:
        """Infer whether this session is running on a cloud Appium provider."""
        # Env signal — most reliable in CI. LT_USERNAME / BROWSERSTACK_USERNAME
        # are set unconditionally for cloud runs and survive any caps namespace drift.
        if os.getenv("LT_USERNAME") or os.getenv("BROWSERSTACK_USERNAME"):
            return True

        caps = getattr(self.driver, "capabilities", None) or {}
        if "lt:options" in caps or "bstack:options" in caps:
            return True

        executor = getattr(self.driver, "command_executor", None)
        executor_url = getattr(executor, "_url", "") if executor else ""
        if executor_url:
            hostname = urlparse(executor_url).hostname or ""
            if "lambdatest.com" in hostname or "browserstack.com" in hostname:
                return True

        return False

    def _detect_device_profile(self) -> None:
        """
        Detect device characteristics and cache for performance.
        Called once during __init__ to avoid repeated driver queries.
        """
        try:
            # Get and cache screen dimensions
            size = self.driver.get_window_size()
            self._screen_size_cache = size
            width, height = size["width"], size["height"]

            # Get device name from capabilities
            caps = self.driver.capabilities
            device_name = caps.get("deviceName") or caps.get("device") or "Unknown Device"

            # Calculate aspect ratio (portrait orientation)
            aspect_ratio = height / width if height > width else width / height
            self._orientation_cache = "portrait" if height > width else "landscape"

            # Try exact device match first
            if device_name in DEVICE_PROFILES:
                self._device_profile = DEVICE_PROFILES[device_name]
                logger.info(
                    f"📱 Device Profile: {device_name}\n"
                    f"   Screen: {width}x{height} ({aspect_ratio:.2f}:1)\n"
                    f"   Orientation: {self._orientation_cache}\n"
                    f"   Safe Areas: top={self._device_profile.safe_top * 100:.0f}%, "
                    f"bottom={self._device_profile.safe_bottom * 100:.0f}%\n"
                    f"   Optimal Scroll: {self._device_profile.scroll_distance_pct * 100:.0f}%"
                )
                return

            # Fallback: MIUI devices get MIUI-specific profile (thicker safe areas)
            if self.is_miui:
                for profile_key, profile in DEVICE_PROFILES.items():
                    if profile_key.startswith("FALLBACK_MIUI"):
                        if abs(profile.aspect_ratio - aspect_ratio) <= ASPECT_RATIO_TOLERANCE:
                            self._device_profile = profile
                            logger.info(
                                f"📱 Device Profile: {device_name} (using {profile.name} MIUI fallback)\n"
                                f"   Screen: {width}x{height} ({aspect_ratio:.2f}:1)"
                            )
                            return

            # Fallback to aspect ratio matching
            for profile_key, profile in DEVICE_PROFILES.items():
                if profile_key.startswith("FALLBACK_") and not profile_key.startswith(
                    "FALLBACK_MIUI"
                ):
                    if abs(profile.aspect_ratio - aspect_ratio) <= ASPECT_RATIO_TOLERANCE:
                        self._device_profile = profile
                        logger.info(
                            f"📱 Device Profile: {device_name} (using {profile.name} fallback)\n"
                            f"   Screen: {width}x{height} ({aspect_ratio:.2f}:1)"
                        )
                        return

            # Ultimate fallback
            fallback_key = "FALLBACK_MIUI_20_9" if self.is_miui else "FALLBACK_20_9"
            self._device_profile = DEVICE_PROFILES[fallback_key]
            logger.warning(f"⚠️ Unknown device: {device_name}, using {fallback_key} profile")

        except Exception as e:
            logger.error(f"❌ Device detection failed: {e}. Using safe defaults.")
            self._device_profile = DEVICE_PROFILES["FALLBACK_20_9"]
            self._screen_size_cache = {"width": 1080, "height": 2400}
            self._orientation_cache = "portrait"

    def _clear_accessibility_cache(self) -> None:
        """Clear stale accessibility cache for Chinese OEM devices.

        MIUI, ColorOS, FuntouchOS and other Chinese OEM skins cache the
        UiAutomator accessibility tree aggressively. After screen transitions,
        the tree is stale for 2-3s. This forces a refresh. Retries once on
        failure (LambdaTest cloud Appium servers may need a moment).
        """
        if not self._supports_clear_accessibility_cache:
            self._refresh_accessibility_snapshot()
            return

        for attempt in range(2):
            try:
                self.driver.execute_script("mobile: clearAccessibilityCache")
                logger.debug("clearAccessibilityCache: OK")
                return
            except Exception as e:
                if "Unknown mobile command" in str(e):
                    self._supports_clear_accessibility_cache = False
                    logger.info(
                        "clearAccessibilityCache unsupported by current Appium provider; "
                        "falling back to page-source refresh"
                    )
                    self._refresh_accessibility_snapshot()
                    return
                if attempt == 0:
                    time.sleep(0.5)
                    logger.info(f"clearAccessibilityCache retry (attempt 1 failed: {e})")
                else:
                    logger.warning(
                        f"clearAccessibilityCache FAILED after 2 attempts: {e}. "
                        f"Text detection may be unreliable on this device."
                    )

    def _refresh_accessibility_snapshot(self) -> None:
        """No-op fallback when clearAccessibilityCache is unsupported.

        DO NOT call driver.page_source here — on React Native + Fabric the
        XML tree is huge (180K+ chars) and the call can crash the Appium
        context, killing the worker. Brief sleep gives the device a moment
        to settle; tests will still find elements via UiAutomator queries.
        """
        time.sleep(0.2)

    def get_screen_size(self) -> dict:
        """Get cached screen size (width, height)."""
        if self._screen_size_cache is None:
            self._screen_size_cache = self.driver.get_window_size()
        return self._screen_size_cache

    def get_safe_swipe_bounds(self) -> tuple[float, float, float, float]:
        """
        Get safe area bounds for swipe operations (percentages).
        Returns: (safe_top_pct, safe_bottom_pct, safe_left_pct, safe_right_pct)
        """
        if self._device_profile:
            return (
                self._device_profile.safe_top,
                self._device_profile.safe_bottom,
                self._device_profile.safe_left,
                self._device_profile.safe_right,
            )
        return (0.05, 0.08, 0.02, 0.02)  # Fallback defaults

    def get_optimal_scroll_distance(self) -> float:
        """Get device-specific optimal scroll distance (% of screen height)."""
        if self._device_profile:
            return self._device_profile.scroll_distance_pct
        return 0.40  # Conservative default

    def calculate_swipe_coordinates(
        self,
        start_y_pct: float,
        end_y_pct: float,
        x_pct: float = 0.5,
        respect_safe_areas: bool = True,
    ) -> tuple[int, int, int, int]:
        """
        Calculate actual pixel coordinates for swipe considering device characteristics.

        Args:
            start_y_pct: Starting Y position (0.0-1.0)
            end_y_pct: Ending Y position (0.0-1.0)
            x_pct: X position (0.0-1.0)
            respect_safe_areas: If True, adjust for notches/nav bars

        Returns:
            Tuple[start_x, start_y, end_x, end_y] in pixels
        """
        size = self.get_screen_size()
        width, height = size["width"], size["height"]

        if respect_safe_areas and self._device_profile:
            safe_top, safe_bottom, safe_left, safe_right = self.get_safe_swipe_bounds()
            usable_height = height * (1.0 - safe_top - safe_bottom)
            usable_width = width * (1.0 - safe_left - safe_right)

            start_y = int(height * safe_top + usable_height * start_y_pct)
            end_y = int(height * safe_top + usable_height * end_y_pct)
            x = int(width * safe_left + usable_width * x_pct)

            return (x, start_y, x, end_y)
        else:
            start_y = int(height * start_y_pct)
            end_y = int(height * end_y_pct)
            x = int(width * x_pct)
            return (x, start_y, x, end_y)

    @staticmethod
    def optimize_for_react_native(driver: webdriver.Remote):
        """
        Apply UiAutomator2 settings to fix React Native animation slowness.
        Call ONCE after driver creation (in conftest.py or AppiumClient).

        PROBLEM: UiAutomator2 waits for accessibility event stream to become idle
        before AND after every interaction. React Native apps with animations NEVER
        become idle → 10s timeout fires TWICE per click = 20s+ per tap.

        FIX: Set waitForIdleTimeout to 0 (or small value) to skip idle waiting.
        Combined with disableWindowAnimation capability, this cuts click time from 22s → <1s.

        Settings applied (Android only):
            waitForIdleTimeout: 0       — skip waiting for accessibility idle state
            waitForSelectorTimeout: 0   — skip waiting for selector idle state

        These are UiAutomator2 Settings API values, NOT capabilities.
        They must be set AFTER session creation via driver.update_settings().

        Desired capabilities to set BEFORE session (in conftest.py):
            'appium:disableWindowAnimation': True   — disable window animations on device
            'appium:skipServerInstallation': True    — skip UIA2 server reinstall (saves 3-5s)
            'appium:skipDeviceInitialization': True  — skip settings app check (saves 1-2s)
        """
        platform = driver.capabilities.get("platformName", "Android").lower()
        if platform == "android":
            try:
                # UiAutomator2 settings API — works on both local & cloud (no ADB needed)
                # All 3 Android animation scales covered:
                #   window_animation_scale     → appium:disableWindowAnimation capability (set in appium_client.py)
                #   transition_animation_scale → appium:disableWindowAnimation capability (set in appium_client.py)
                #   animator_duration_scale    → animatorDurationScale setting below (Appium protocol)
                driver.update_settings(
                    {
                        "waitForIdleTimeout": 0,  # Default 10000ms — causes 10s+ delays per interaction
                        "waitForSelectorTimeout": 0,  # Default 10000ms — additional idle wait for selectors
                        "actionAcknowledgmentTimeout": 0,  # Default 3000ms — waits 3s after EVERY tap for confirmation
                        "keyInjectionDelay": 0,  # Faster typing into RN TextInput
                        "scrollAcknowledgmentTimeout": 0,  # Faster FlatList scrolls
                        # NOTE: allowInvisibleElements=True can match off-screen junk → kept default False
                        "enableMultiWindows": True,  # Required for popups/keyboard/system dialogs in RN
                        "shouldUseCompactResponses": True,  # Smaller payloads — fewer cloud network drops
                        "animatorDurationScale": 0,  # Force animator_duration_scale to 0 (via Appium, not ADB)
                        "enableNotificationListener": False,  # Saves CPU — we don't test notifications
                        "trackScrollEvents": False,  # We handle scroll verification ourselves
                    }
                )
                logger.info(
                    "⚡ React Native optimization applied: idle/selector/action/keyInjection/scroll=0, "
                    "allowInvisibleElements=True, enableMultiWindows=True, compactResponses=True"
                )
            except Exception as e:
                logger.warning(f"Failed to apply RN optimization settings: {e}")
        else:
            logger.debug("iOS detected — no waitForIdle optimization needed")

    # ==================== CROSS-PLATFORM LOCATOR HELPERS ====================
    # These return (AppiumBy, locator) tuples for the current platform.
    # All page objects inherit these — no need to redefine.
    #
    # SPEED SUMMARY:
    #   Android: resource-id (0.3s) > UiAutomator (0.5s) > XPath (2-5s) > Accessibility ID (30s+ ⛔)
    #   iOS:     accessibility_id (FAST) = id = name > predicate (FAST) > class chain (FAST) > XPath (10x slower)

    def _by_id(self, rid: str) -> tuple:
        """testID prop lookup — FASTEST on both platforms.
        Android: UiAutomator resourceId() — matches bare React Native testIDs
        iOS: accessibilityIdentifier via accessibility_id"""
        if self.platform == "android":
            return (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().resourceId("{rid}")')
        return (AppiumBy.ACCESSIBILITY_ID, rid)

    def _by_id_contains(self, rid_prefix: str) -> tuple:
        """Partial testID match — finds first element whose resource-id contains the prefix.
        Android: UiAutomator resourceIdMatches()
        iOS: predicate string on name contains"""
        if self.platform == "android":
            return (
                AppiumBy.ANDROID_UIAUTOMATOR,
                f'new UiSelector().resourceIdMatches(".*{rid_prefix}.*")',
            )
        return (AppiumBy.IOS_PREDICATE, f'name CONTAINS "{rid_prefix}"')

    def _by_desc(self, desc: str, instance: int | None = None) -> tuple:
        """accessibilityLabel / content-desc lookup.
        Android: UiAutomator description() (0.5s) — bypasses RN 30s delay
        iOS: accessibility_id (FAST — native lookup, no delay)
        ⚠️ On Android, NEVER use AppiumBy.ACCESSIBILITY_ID — it's 30x slower!

        instance: Android-only. Disambiguate when multiple elements share the same
        desc (e.g. duplicate sticky-footer + inline CTA). instance(0) = first match,
        instance(1) = second, etc. Ignored on iOS (use a different locator there).
        """
        if self.platform == "android":
            sel = f'new UiSelector().description("{desc}")'
            if instance is not None:
                sel += f".instance({instance})"
            return (AppiumBy.ANDROID_UIAUTOMATOR, sel)
        else:
            return (AppiumBy.ACCESSIBILITY_ID, desc)

    def _by_desc_contains(self, desc: str) -> tuple:
        """Partial content-desc match. Android: descriptionContains() | iOS: CONTAINS.
        Useful when content-desc has prefixes (emojis, icons) e.g. '🗑️ Delete Chat'."""
        if self.platform == "android":
            return (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().descriptionContains("{desc}")')
        else:
            return (AppiumBy.IOS_PREDICATE, f'name CONTAINS "{desc}"')

    def _by_text(self, text: str) -> tuple:
        """Exact text match. Android: UiAutomator text() | iOS: predicate label=="""
        if self.platform == "android":
            return (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{text}")')
        else:
            return (AppiumBy.IOS_PREDICATE, f'label == "{text}"')

    WEB_VIEW = "web_view"

    # Cross-platform native class names, keyed by a platform-neutral alias.
    _CLASS_ALIASES = {
        WEB_VIEW: {"android": "android.webkit.WebView", "ios": "XCUIElementTypeWebView"},
    }

    def _by_class(self, alias: str) -> tuple:
        """Native class-name match for a platform-neutral alias (e.g. WEB_VIEW).

        Use only for container widgets that carry no testID/label — never as a
        substitute for _by_id / _by_desc / _by_text.
        """
        try:
            class_name = self._CLASS_ALIASES[alias][self.platform]
        except KeyError as exc:
            raise ValueError(
                f"No native class for alias '{alias}' on platform '{self.platform}'"
            ) from exc
        return (AppiumBy.CLASS_NAME, class_name)

    def _by_text_contains(self, text: str, instance: int | None = None) -> tuple:
        """Partial text match. Android: UiAutomator textContains() | iOS: predicate CONTAINS

        instance: Android-only. Picks the Nth match when text appears multiple times
        on screen (e.g. sticky footer label + inline CTA). Ignored on iOS.
        """
        if self.platform == "android":
            sel = f'new UiSelector().textContains("{text}")'
            if instance is not None:
                sel += f".instance({instance})"
            return (AppiumBy.ANDROID_UIAUTOMATOR, sel)
        else:
            return (AppiumBy.IOS_PREDICATE, f'label CONTAINS "{text}"')

    def _by_predicate(self, predicate: str, android_xpath_fallback: str = "") -> tuple:
        """iOS-native predicate string (FAST — uses XCTest native).
        Android: falls back to XPath (provide android_xpath_fallback).

        Examples:
            _by_predicate('name == "done" AND type == "XCUIElementTypeButton"')
            _by_predicate('value BEGINSWITH "₹"', '//android.widget.TextView[starts-with(@text,"₹")]')
        """
        if self.platform == "ios":
            return (AppiumBy.IOS_PREDICATE, predicate)
        elif android_xpath_fallback:
            return (AppiumBy.XPATH, android_xpath_fallback)
        else:
            logger.warning(f"_by_predicate() called on Android without fallback: {predicate}")
            return (AppiumBy.XPATH, f'//*[@text="{predicate}"]')  # best-effort

    def _by_class_chain(self, chain: str, android_xpath_fallback: str = "") -> tuple:
        """iOS class chain query (FAST — predicate + hierarchy, native XCTest).
        Android: falls back to XPath (provide android_xpath_fallback).

        Example:
            _by_class_chain('**/XCUIElementTypeCell[`name == "item"`]/XCUIElementTypeButton[-1]',
                           '//android.view.ViewGroup[.//android.widget.TextView[@text="item"]]//android.widget.Button')
        """
        if self.platform == "ios":
            return (AppiumBy.IOS_CLASS_CHAIN, chain)
        elif android_xpath_fallback:
            return (AppiumBy.XPATH, android_xpath_fallback)
        else:
            logger.warning(f"_by_class_chain() called on Android without fallback: {chain}")
            return (AppiumBy.XPATH, "//*")  # best-effort

    # ==================== ELEMENT FINDING ====================

    def find_element(
        self,
        locator: str,
        by: AppiumBy = AppiumBy.ID,
        timeout: int | None = None,
        _skip_heal: bool = False,
    ) -> WebElement:
        """
        Find element with explicit wait for VISIBILITY (not just presence).

        Uses visibility_of_element_located — element must be visible on screen.
        When SELF_HEAL=true, broken locators are auto-healed via baseline JSON
        or Claude Haiku API (~$0.003/call).

        Args:
            locator: Element locator value
            by: Locator strategy (ID, XPATH, CLASS_NAME, etc.)
            timeout: Optional timeout override
            _skip_heal: Internal flag — skip healing when called from fallback chains

        Returns:
            WebElement: The found visible element

        Raises:
            TimeoutException: If element not found/visible within timeout
        """
        wait = self.wait if timeout is None else WebDriverWait(self.driver, timeout)

        try:
            element = wait.until(EC.visibility_of_element_located((by, locator)))
            logger.debug(f"Found visible element: {by}={locator}")
            return element
        except TimeoutException:
            # Try self-healing before giving up
            if self._self_heal_enabled and not _skip_heal:
                from src.utils.self_heal import try_heal_element

                heal_key = f"{by}|{locator}"
                healed = try_heal_element(
                    self.driver,
                    heal_key,
                    by,
                    locator,
                    timeout=timeout or self.timeout,
                )
                if healed:
                    return healed

            logger.error(f"Element not visible after {timeout or self.timeout}s: {by}={locator}")
            self.capture_screenshot(f"element_not_found_{locator.replace('/', '_')[:50]}")
            raise TimeoutException(f"Unable to locate visible element: {by}={locator}") from None

    def find_element_in_dom(
        self, locator: str, by: AppiumBy = AppiumBy.ID, timeout: int | None = None
    ) -> WebElement:
        """
        Find element by presence in DOM (may not be visible).

        Use this ONLY when you need to check DOM presence without visibility,
        e.g., checking if an element exists before scrolling to it.

        For most cases, use find_element() which waits for visibility.

        Args:
            locator: Element locator value
            by: Locator strategy
            timeout: Optional timeout override

        Returns:
            WebElement: The found element (may not be visible)
        """
        wait = self.wait if timeout is None else WebDriverWait(self.driver, timeout)

        try:
            element = wait.until(EC.presence_of_element_located((by, locator)))
            logger.debug(f"Found element in DOM: {by}={locator}")
            return element
        except TimeoutException:
            # Changed to DEBUG to avoid cluttering Allure reports with expected fallback attempts
            # (e.g., permission popups that may not exist)
            logger.debug(f"Element not in DOM after {timeout or self.timeout}s: {by}={locator}")
            raise TimeoutException(f"Unable to locate element in DOM: {by}={locator}") from None

    def find_elements(
        self, locator: str, by: AppiumBy = AppiumBy.ID, timeout: int | None = None
    ) -> list[WebElement]:
        """
        Find multiple elements matching the locator.

        Args:
            locator: Element locator value
            by: Locator strategy
            timeout: Optional timeout override

        Returns:
            List of WebElements (empty list if none found)
        """
        try:
            if timeout:
                wait = WebDriverWait(self.driver, timeout)
                wait.until(EC.presence_of_element_located((by, locator)))

            elements = self.driver.find_elements(by, locator)
            logger.debug(f"Found {len(elements)} elements: {by}={locator}")
            return elements
        except (TimeoutException, NoSuchElementException) as e:
            logger.warning(f"No elements found: {by}={locator} - {str(e)}")
            return []
        # Let InvalidSessionIdException + transport WebDriverExceptions propagate —
        # those are infra problems, not "element absent". Silently returning []
        # for a dead session masks RCA and lets dependent assertions fail later
        # with misleading 'element not visible' errors.

    @overload
    def find_element_with_fallbacks(
        self,
        locator_candidates: list[tuple[str, str]],
        action_description: str = "element",
        timeout: int = 5,
        required: Literal[True] = True,
    ) -> WebElement: ...

    @overload
    def find_element_with_fallbacks(
        self,
        locator_candidates: list[tuple[str, str]],
        action_description: str = "element",
        timeout: int = 5,
        required: Literal[False] = ...,
    ) -> WebElement | None: ...

    def find_element_with_fallbacks(
        self,
        locator_candidates: list[tuple[str, str]],
        action_description: str = "element",
        timeout: int = 5,
        required: bool = True,
    ) -> WebElement | None:
        """
        Try multiple locators in order until one succeeds.

        IMPORTANT: Put fastest/proven locator FIRST in list.
        Use cross-platform helpers (_by_id, _by_desc, etc.) to auto-pick fast strategy.

        Speed order:
          Android: resource-id 0.3s → UiAutomator 0.5s → XPath 2-5s → Acc. ID 30s+ (AVOID!)
          iOS:     accessibility_id (FAST) → predicate (FAST) → class chain (FAST) → XPath (SLOW)

        When SELF_HEAL=true and all locators fail, attempts self-healing ONCE
        using the first (preferred) locator with action_description as the key.

        Args:
            locator_candidates: List of (AppiumBy, locator_string) tuples in priority order
            action_description: Human-readable description for logging
            timeout: Timeout in seconds for each locator attempt
            required: If True, raise exception when all fail; if False, return None

        Returns:
            WebElement if found, None if not required and not found
        """
        last_error = None

        for by, locator in locator_candidates:
            try:
                # _skip_heal=True: don't heal per-locator, heal once after all fail
                element = self.find_element(locator, by, timeout=timeout, _skip_heal=True)
                logger.debug(f"✓ Found {action_description} via {by}={locator}")
                return element
            except (
                NoSuchElementException,
                TimeoutException,
                StaleElementReferenceException,
            ) as e:
                # Genuine "this locator missed" — try next candidate.
                logger.debug(f"✗ Failed to find {action_description} via {by}={locator}: {e}")
                last_error = e
                continue
            except InvalidSessionIdException:
                # Session is dead — no point trying more locators on the same driver.
                raise

        # All locators failed — try self-healing ONCE with the first (preferred) locator
        if self._self_heal_enabled and locator_candidates:
            from src.utils.self_heal import try_heal_element

            first_by, first_locator = locator_candidates[0]
            heal_key = action_description.replace(" ", "_").lower()
            healed = try_heal_element(
                self.driver, heal_key, first_by, first_locator, timeout=timeout
            )
            if healed:
                return healed

        error_msg = (
            f"Could not find {action_description} with any of {len(locator_candidates)} locators"
        )

        if required:
            logger.error(error_msg)
            if last_error:
                raise last_error
            raise Exception(error_msg)
        else:
            logger.warning(error_msg)
            return None

    # ==================== TAP / CLICK ACTIONS ====================

    def _gesture_tap_element(self, element: WebElement) -> None:
        """Tap element using W3C Touch Actions (input-level injection).

        Why W3C Actions (not element.click or mobile: clickGesture):
        - ``element.click()`` sends an accessibility ACTION_CLICK which
          React Native's JS bridge can silently drop.
        - ``mobile: clickGesture`` succeeds silently on OnePlus OxygenOS
          but doesn't propagate from child TextView to parent Pressable.
        - ALL three methods return success even when the app ignores the
          tap — fallback chains are useless (no exception to trigger them).
        - W3C Touch Actions inject MotionEvent at the InputManager level,
          BELOW the OEM accessibility layer — most reliable on all devices.

        NOTE: Since all tap methods succeed silently, callers that need
        guaranteed state change (e.g. tab switches) must verify the tap
        had an effect and retry if needed.
        """
        pointer = PointerInput("touch", "finger1")
        actions = ActionBuilder(self.driver, mouse=pointer)
        actions.pointer_action.move_to(element)
        actions.pointer_action.pointer_down()
        actions.pointer_action.pause(0.05)  # 50ms press — registers on all OEMs
        actions.pointer_action.pointer_up()
        actions.perform()

    @retry_on_stale()
    def tap(
        self,
        locator: str,
        by: AppiumBy = AppiumBy.ID,
        timeout: int | None = None,
        scroll_first: bool = False,
    ) -> "BasePage":
        """
        Tap on element — DEFAULT method for 95% of taps.

        Uses ``mobile: clickGesture`` (touch event) instead of
        ``element.click()`` for reliable React Native taps.

        PERFORMANCE: NO post-tap sleep. React Native elements are tappable
        if visible. The next wait_for_element/find_element call will handle
        waiting for the result of the tap.

        RULE: If element is visible → tap immediately. No clickable check needed.

        WHEN TO USE:
        - After wait_for_screen_ready() confirms element is visible
        - After is_displayed() returns True
        - For navigation, buttons, form submission — EVERYTHING

        WHEN NOT TO USE:
        - Element starts disabled, becomes enabled later → tap_when_clickable()
        - Element might be stale/covered by overlay → safe_tap()

        Args:
            locator: Element locator
            by: Locator strategy
            timeout: Optional timeout override
            scroll_first: If True, scroll element into view before tapping.
                Defaults to False — most taps happen after wait_for_screen_ready().
                Use True for below-fold elements without prior scroll.

        Returns:
            self for method chaining
        """
        if scroll_first:
            self._ensure_element_in_view(locator, by)

        element = self.find_element(locator, by, timeout)
        logger.info(f"Tapping element: {by}={locator}")
        self._gesture_tap_element(element)
        return self

    def double_tap(
        self, locator: str, by: AppiumBy = AppiumBy.ID, timeout: int | None = None
    ) -> "BasePage":
        """
        Double tap on element (two clicks, minimal gap).

        Args:
            locator: Element locator
            by: Locator strategy
            timeout: Optional timeout override

        Returns:
            self for method chaining
        """
        import time

        element = self.find_element(locator, by, timeout)
        logger.info(f"Double tapping element: {by}={locator}")
        self._gesture_tap_element(element)
        time.sleep(0.15)  # Minimal gap for double-tap recognition
        self._gesture_tap_element(element)
        logger.info("✓ Performed double-tap")
        return self

    @retry_on_stale()
    def tap_with_fallbacks(
        self,
        locator_candidates: list[tuple[str, str]],
        action_description: str = "element",
        timeout: int = 5,
    ) -> "BasePage":
        """
        Find element with fallback locators and tap it.

        Args:
            locator_candidates: List of (AppiumBy, locator_string) tuples in priority order
            action_description: Human-readable description for logging
            timeout: Timeout in seconds for each locator attempt

        Returns:
            self for method chaining
        """
        element = self.find_element_with_fallbacks(
            locator_candidates=locator_candidates,
            action_description=action_description,
            timeout=timeout,
            required=True,
        )

        logger.info(f"Tapping {action_description}")
        self._gesture_tap_element(element)
        logger.info(f"✓ {action_description} tapped successfully")
        return self

    def tap_and_verify(
        self,
        locator_candidates: list[tuple[str, str]],
        action_description: str = "element",
        timeout: int = 10,
        verify_disappear: bool = False,
        verify_appear: tuple[str, str] | None = None,
        verify_timeout: int = 5,
        max_retries: int = 3,
    ) -> "BasePage":
        """
        Tap with verification — retries if tap doesn't register.

        Solves the SILENT TAP FAILURE problem where Appium reports success
        but the tap doesn't actually register on the device (common on
        LambdaTest cloud + Chinese OEM devices).

        Three verification modes:

        1. verify_appear=(by, loc): New element should show up after tap.
           Use for: bottom sheets, modals, screen transitions, popups.
           The tapped element may or may not disappear — doesn't matter.

        2. verify_disappear=True: Tapped element should vanish.
           Use for: navigation buttons where button leaves the screen.

        3. Neither set: No verification, same as tap_with_fallbacks().

        Cost: ~0.1s extra when tap works. Auto-recovers when tap fails.

        Args:
            locator_candidates: List of (AppiumBy, locator) tuples
            action_description: Human-readable name for logging
            timeout: Timeout for finding the element to tap
            verify_disappear: If True, verify tapped element disappears
            verify_appear: (AppiumBy, locator) — verify this element appears after tap
            verify_timeout: How long to wait for verification (default: 5s)
            max_retries: Max tap attempts (default: 3)

        Returns:
            self for method chaining

        Raises:
            AssertionError: If tap doesn't register after max_retries

        Example (bottom sheet — new element appears, button stays):
            self.tap_and_verify(
                locator_candidates=[self._by_text("Add Cash")],
                action_description="Add Cash button",
                verify_appear=self._by_text("Enter Amount"),
            )

        Example (navigation — button disappears):
            self.tap_and_verify(
                locator_candidates=[self._by_text("Start Chat")],
                action_description="Start Chat button",
                verify_disappear=True,
            )

        Example (screen transition — button disappears + new screen):
            self.tap_and_verify(
                locator_candidates=[self._by_text("GET OTP")],
                action_description="GET OTP button",
                verify_appear=self._by_text("Enter OTP"),
            )
        """
        for attempt in range(1, max_retries + 1):
            # Tap the element
            self.tap_with_fallbacks(
                locator_candidates=locator_candidates,
                action_description=action_description,
                timeout=timeout,
            )

            # Verify tap had effect
            try:
                if verify_appear:
                    # Check: new element should show up (bottom sheet, modal, new screen)
                    appear_by, appear_loc = verify_appear
                    self.wait_for_element(
                        appear_loc, appear_by, timeout=verify_timeout, condition="visible"
                    )
                    logger.info(
                        f"✓ {action_description} tap verified "
                        f"(new element appeared) on attempt {attempt}"
                    )
                    return self

                elif verify_disappear:
                    # Check: tapped element should vanish (navigation).
                    # tap_with_fallbacks may have matched any candidate, not just [0].
                    # Verify all candidates are gone — whichever matched, it must now
                    # be absent. Checking only [0] mis-classifies success when locator
                    # 2 or 3 was the one that worked.
                    deadline = time.time() + verify_timeout
                    per_check_timeout = max(1, verify_timeout // max(len(locator_candidates), 1))
                    still_visible = None
                    while time.time() < deadline:
                        still_visible = None
                        for cand_by, cand_loc in locator_candidates:
                            if self.is_displayed(
                                cand_loc, cand_by, timeout=per_check_timeout, scroll_first=False
                            ):
                                still_visible = (cand_by, cand_loc)
                                break
                        if still_visible is None:
                            logger.info(
                                f"✓ {action_description} tap verified "
                                f"(element disappeared) on attempt {attempt}"
                            )
                            return self
                    raise TimeoutException(
                        f"{action_description} still visible via {still_visible} "
                        f"after {verify_timeout}s"
                    )

                else:
                    # No verification — same as tap_with_fallbacks
                    return self

            except Exception as err:
                if attempt < max_retries:
                    logger.warning(
                        f"⚠️ {action_description} tap not registered "
                        f"(attempt {attempt}/{max_retries}), retrying..."
                    )
                    self.hide_keyboard()
                else:
                    raise AssertionError(
                        f"{action_description} tap did not register "
                        f"after {max_retries} attempts! "
                        f"Element still visible / next screen not loaded."
                    ) from err

        return self  # unreachable, but satisfies type checker

    def tap_when_clickable(
        self,
        locator: str,
        by: AppiumBy = AppiumBy.ID,
        timeout: int = 15,
    ) -> "BasePage":
        """
        Wait for element to be clickable (visible + enabled), then tap.

        RARE USE — Only for elements that START DISABLED:
        - Button disabled until form is valid
        - Button behind loading overlay that will disappear
        - Button disabled during animation/transition

        DO NOT USE for normal visible elements — just use tap().

        Args:
            locator: Element locator
            by: Locator strategy
            timeout: Max wait time (default: 15s)

        Returns:
            self for method chaining
        """
        logger.info(f"Waiting for clickable: {by}={locator}")
        element = self.wait_for_element(
            locator=locator, by=by, timeout=timeout, condition="clickable"
        )
        self._gesture_tap_element(element)
        logger.info(f"✓ Tapped clickable element: {by}={locator}")
        return self

    def safe_tap(
        self,
        locator: str,
        by: AppiumBy = AppiumBy.ID,
        timeout: int = 15,
        retry_count: int = 3,
        scroll_into_view: bool = False,
    ) -> "BasePage":
        """
        Tap with retry logic for stale/flaky elements.

        OPTIMIZED: Removed redundant displayed + enabled checks.
        Just find visible element and tap. If it fails, retry.

        Use for:
        - Dynamic lists where elements might go stale
        - Elements that need scrolling into view
        - Elements that might be temporarily covered

        Args:
            locator: Element locator
            by: Locator strategy
            timeout: Max wait per attempt
            retry_count: Number of tap attempts (default: 3)
            scroll_into_view: Scroll to element if not visible

        Returns:
            self for method chaining
        """
        import time

        last_error = None

        for attempt in range(1, retry_count + 1):
            try:
                logger.info(f"Safe tap attempt {attempt}/{retry_count} for {by}={locator}")

                if scroll_into_view:
                    self._ensure_element_in_view(locator, by)

                # Just find visible element and tap — no redundant checks
                element = self.find_element(locator, by, timeout)
                self._gesture_tap_element(element)
                logger.info(f"✓ Safe tap successful on attempt {attempt}: {by}={locator}")
                return self

            except (
                StaleElementReferenceException,
                NoSuchElementException,
                TimeoutException,
            ) as e:
                # Transient lookup/staleness — retry is appropriate.
                last_error = e
                logger.warning(
                    f"Tap attempt {attempt} failed for {by}={locator}: {type(e).__name__}"
                )
                if attempt < retry_count:
                    time.sleep(0.5)  # Brief wait before retry
                continue
            except InvalidSessionIdException:
                # Dead session — retry on same driver pointless. Fail fast.
                raise

        error_msg = (
            f"Failed to tap {by}={locator} after {retry_count} attempts. "
            f"Last error: {type(last_error).__name__}: {str(last_error)}"
        )
        logger.error(error_msg)
        self.capture_screenshot(f"safe_tap_failed_{locator.replace('/', '_')[:50]}")
        raise Exception(error_msg) from last_error

    def wait_for_clickable_and_tap(
        self, locator: str, by: AppiumBy = AppiumBy.ID, timeout: int = 15
    ) -> "BasePage":
        """Alias for tap_when_clickable() for backward compatibility."""
        return self.tap_when_clickable(locator, by, timeout)

    # ==================== INSTANT TAP (for slow-rendering screens) ====================

    def tap_asap(
        self,
        locator: str,
        by: str = AppiumBy.ID,
        timeout: int = 60,
        poll: float = 0.2,
        description: str = "",
    ) -> WebElement:
        """
        Tap element the INSTANT it appears in DOM. Fastest possible element-based tap.

        WHY THIS EXISTS:
        Regular tap() → find_element() → WebDriverWait → visibility_of_element_located
        That chain has overhead:
          - visibility_of checks display state (extra round-trip)
          - WebDriverWait default poll is 0.5s (misses element by up to 500ms)
          - Implicit wait can compound with explicit wait

        THIS METHOD:
          - Bypasses WebDriverWait entirely
          - Uses find_elements() (plural) which returns [] instantly if not found
          - Temporarily sets implicit wait to 0 to avoid compounding
          - Polls every 0.2s (catches element ~300ms sooner on average)
          - Uses presence (DOM) not visibility (render) — catches ~1-2s sooner
          - Taps immediately on find — no clickable/visible recheck

        USE FOR:
          - Slow-rendering screens (home screen with 51s React Native render)
          - Any element that takes >5s to appear due to app-side loading
          - First element on screen after navigation (while rest of DOM is loading)

        DON'T USE FOR:
          - Normal taps on already-loaded screens — use regular tap()
          - Elements that need scrolling — use scroll_to_element() first

        Args:
            locator: Element locator string
            by: Locator strategy (default: AppiumBy.ID — fastest on Android)
            timeout: Max wait time in seconds (default: 60 for slow RN screens)
            poll: Poll interval in seconds (default: 0.2 — aggressive)
            description: Human-readable name for logging (e.g., "Wallet button")

        Returns:
            WebElement that was tapped

        Raises:
            TimeoutError: If element not found within timeout
        """
        import time

        desc = description or f"{by}={locator}"
        start = time.time()

        # Save the CURRENT implicit wait (not env default — session may already be 0).
        # Restoring env value after our work would silently flip global driver behavior.
        prev_implicit = None
        try:
            prev_implicit = self.driver.timeouts.implicit_wait
        except Exception:
            pass
        try:
            self.driver.implicitly_wait(0)
        except Exception:
            pass

        logger.info(f"⚡ tap_asap: Waiting for '{desc}' (poll={poll}s, timeout={timeout}s)")

        element = None
        attempts = 0

        # Narrow exception list — only swallow "element not found yet" cases.
        # Session/protocol errors must surface immediately, not burn the full timeout.
        transient_exc = (NoSuchElementException, StaleElementReferenceException)

        try:
            while time.time() - start < timeout:
                attempts += 1
                try:
                    elements = self.driver.find_elements(by, locator)
                    if elements:
                        element = elements[0]
                        elapsed = time.time() - start
                        logger.info(
                            f"⚡ tap_asap: Found '{desc}' in {elapsed:.1f}s "
                            f"({attempts} polls) — tapping NOW"
                        )
                        self._gesture_tap_element(element)
                        return element
                except transient_exc:
                    pass  # Element not in DOM yet, keep polling

                time.sleep(poll)

            elapsed = time.time() - start
            raise TimeoutError(
                f"⚡ tap_asap: '{desc}' not found after {elapsed:.1f}s "
                f"({attempts} polls at {poll}s intervals)"
            )

        finally:
            # Restore the value we captured, not env default
            if prev_implicit is not None:
                try:
                    self.driver.implicitly_wait(prev_implicit)
                except Exception:
                    pass

    def tap_asap_with_fallbacks(
        self,
        locator_candidates: list[tuple[str, str]],
        timeout: int = 60,
        poll: float = 0.2,
        description: str = "",
    ) -> WebElement:
        """
        Like tap_asap but tries multiple locators each poll cycle.

        Each poll iteration tries ALL locators (fastest first), so if resource-id
        isn't added yet but content-desc is, it catches whichever appears first.

        Args:
            locator_candidates: List of (AppiumBy, locator) tuples — fastest FIRST
            timeout: Max wait in seconds
            poll: Poll interval in seconds
            description: Human-readable name for logging

        Example:
            self.tap_asap_with_fallbacks([
                self._by_id("wallet-button"),           # 0.3s — if testID exists
                self._by_desc("Open wallet"),            # 0.5s — content-desc fallback
                self._by_text("Wallet"),                 # text fallback
            ], description="Wallet button")
        """
        import time

        desc = description or str(locator_candidates[0])
        start = time.time()

        # Capture current implicit wait — restore exactly what was set, not env default.
        prev_implicit = None
        try:
            prev_implicit = self.driver.timeouts.implicit_wait
        except Exception:
            pass
        try:
            self.driver.implicitly_wait(0)
        except Exception:
            pass

        logger.info(
            f"⚡ tap_asap_fallbacks: Waiting for '{desc}' "
            f"({len(locator_candidates)} locators, poll={poll}s, timeout={timeout}s)"
        )

        attempts = 0
        # Narrow exception list — surface session/protocol errors immediately.
        transient_exc = (NoSuchElementException, StaleElementReferenceException)

        try:
            while time.time() - start < timeout:
                attempts += 1
                for by, locator in locator_candidates:
                    try:
                        elements = self.driver.find_elements(by, locator)
                        if elements:
                            elapsed = time.time() - start
                            logger.info(
                                f"⚡ tap_asap_fallbacks: Found '{desc}' via "
                                f"{by}='{locator}' in {elapsed:.1f}s ({attempts} polls) — tapping"
                            )
                            self._gesture_tap_element(elements[0])
                            return elements[0]
                    except transient_exc:
                        continue

                time.sleep(poll)

            elapsed = time.time() - start
            tried = ", ".join(f"{by}={loc}" for by, loc in locator_candidates)
            raise TimeoutError(
                f"⚡ tap_asap_fallbacks: '{desc}' not found after {elapsed:.1f}s. Tried: [{tried}]"
            )

        finally:
            if prev_implicit is not None:
                try:
                    self.driver.implicitly_wait(prev_implicit)
                except Exception:
                    pass

    # ==================== TEXT INPUT ====================

    @retry_on_stale()
    def input_text(
        self,
        locator: str,
        text: str,
        by: AppiumBy = AppiumBy.ID,
        clear_first: bool = True,
        timeout: int | None = None,
    ) -> "BasePage":
        """
        Input text into element.

        Args:
            locator: Element locator
            text: Text to input
            by: Locator strategy
            clear_first: Clear field before typing (default: True)
            timeout: Optional timeout override

        Returns:
            self for method chaining
        """
        element = self.find_element(locator, by, timeout)

        if clear_first:
            try:
                element.clear()
            except Exception as e:
                logger.warning(f"Could not clear field: {e}")

        logger.info(f"Inputting text into {by}={locator}: {text[:20]}...")
        element.send_keys(text)
        return self

    def input_text_with_fallbacks(
        self,
        locator_candidates: list[tuple[str, str]],
        text: str,
        field_description: str = "input field",
        clear_first: bool = True,
        timeout: int = 5,
    ) -> "BasePage":
        """
        Find input field with fallback locators, then clear and enter text.

        Args:
            locator_candidates: List of (AppiumBy, locator_string) tuples in priority order
            text: Text to input
            field_description: Human-readable description for logging
            clear_first: Clear field before typing (default: True)
            timeout: Timeout per locator attempt

        Returns:
            self for method chaining
        """
        element = self.find_element_with_fallbacks(
            locator_candidates=locator_candidates,
            action_description=field_description,
            timeout=timeout,
            required=True,
        )

        if clear_first:
            try:
                element.clear()
                logger.debug(f"Cleared {field_description}")
            except Exception as e:
                logger.warning(f"Could not clear {field_description}: {e}")

        log_text = text[:20] + ("..." if len(text) > 20 else "")
        element.send_keys(text)
        logger.info(f"✓ Entered text into {field_description}: {log_text}")
        return self

    # ==================== TEXT READING ====================

    @retry_on_stale()
    def get_text(
        self,
        locator: str,
        by: AppiumBy = AppiumBy.ID,
        timeout: int | None = None,
        scroll_first: bool = True,
        _skip_heal: bool = False,
    ) -> str:
        """
        Get text from element.

        Args:
            locator: Element locator
            by: Locator strategy
            timeout: Optional timeout override
            scroll_first: If True, scroll element into view before reading.
            _skip_heal: Internal flag — skip self-heal in verify loops.

        Returns:
            Text content of the element
        """
        if scroll_first:
            self._ensure_element_in_view(locator, by)

        element = self.find_element(locator, by, timeout, _skip_heal=_skip_heal)
        text = element.text
        logger.debug(f"Got text from {by}={locator}: {text}")
        return text

    def get_element_text_with_fallbacks(
        self,
        locator_candidates: list[tuple[str, str]],
        field_description: str = "field",
        timeout: int = 5,
        default: str = "",
    ) -> str:
        """
        Find element with fallbacks and get its text value.

        Args:
            locator_candidates: List of (AppiumBy, locator_string) tuples
            field_description: Human-readable description
            timeout: Timeout per locator attempt
            default: Default value if not found

        Returns:
            Text value from element, or default if not found
        """
        element = self.find_element_with_fallbacks(
            locator_candidates=locator_candidates,
            action_description=field_description,
            timeout=timeout,
            required=False,
        )

        if element:
            text = element.text
            logger.debug(f"Got text from {field_description}: {text}")
            return text
        else:
            logger.warning(f"Could not get text from {field_description}, returning: {default}")
            return default

    # ==================== VISIBILITY & STATE CHECKS ====================

    @retry_on_stale()
    def is_displayed(
        self,
        locator: str,
        by: AppiumBy = AppiumBy.ID,
        timeout: int = 5,
        scroll_first: bool = True,
    ) -> bool:
        """
        Check if element is displayed — SINGLE Appium call.

        OPTIMIZED: Uses visibility_of_element_located directly.
        Old version did find_element(presence) + .is_displayed() = 2 calls.
        Now does 1 call: visibility check that returns True/False.

        Args:
            locator: Element locator
            by: Locator strategy
            timeout: Timeout for finding element (default: 5s)
            scroll_first: If True, scroll element into view before checking.
                Use for elements that may be below fold on different aspect ratios.

        Returns:
            True if element is visible, False otherwise
        """
        if scroll_first:
            self._ensure_element_in_view(locator, by)

        try:
            wait = WebDriverWait(self.driver, timeout)
            wait.until(EC.visibility_of_element_located((by, locator)))
            logger.debug(f"Element visible: {by}={locator}")
            return True
        except (TimeoutException, NoSuchElementException, StaleElementReferenceException):
            logger.debug(f"Element not visible: {by}={locator}")
            return False

    def _ensure_element_in_view(self, locator: str, by: AppiumBy, quick_timeout: int = 1) -> None:
        """
        Quick visibility check first, then scroll only if needed.

        Avoids expensive scroll operations when element is already on screen.
        Falls through to _scroll_element_into_view only when element is not visible.

        Args:
            locator: Element locator string
            by: Locator strategy
            quick_timeout: Fast visibility check timeout (default 1s)
        """
        try:
            WebDriverWait(self.driver, quick_timeout).until(
                EC.visibility_of_element_located((by, locator))
            )
            logger.debug(f"Element visible (no scroll needed): {by}={locator}")
            return  # Already visible, no scroll needed
        except (TimeoutException, NoSuchElementException, StaleElementReferenceException):
            pass
        self._scroll_element_into_view(locator, by)

    def _scroll_element_into_view(self, locator: str, by: AppiumBy) -> None:
        """
        Scroll element into view using UiScrollable (fast, aspect-ratio safe).

        Routes ALL locator types to fast UiScrollable on Android.
        UiScrollable handles both directions automatically.
        Swipe fallback for unknown locator types (1 swipe max, UP first then DOWN).

        Args:
            locator: Element locator string
            by: Locator strategy (determines scroll method)
        """
        try:
            if self.platform != "android":
                self.scroll_to_element(locator, by, direction="down", max_scrolls=1)
                return

            import re

            # --- Android: Route to UiScrollable by locator type ---

            if by == AppiumBy.ANDROID_UIAUTOMATOR:
                # text("...")
                text_match = re.search(r'\.text\("([^"]+)"\)', locator)
                if text_match:
                    self.scroll_into_view_by_text(text_match.group(1), exact=True)
                    return

                # textContains("...")
                text_contains_match = re.search(r'\.textContains\("([^"]+)"\)', locator)
                if text_contains_match:
                    self.scroll_into_view_by_text(text_contains_match.group(1), exact=False)
                    return

                # description("...")
                desc_match = re.search(r'\.description\("([^"]+)"\)', locator)
                if desc_match:
                    self.scroll_into_view_by_description(desc_match.group(1))
                    return

                # resourceId("...")
                rid_match = re.search(r'\.resourceId\("([^"]+)"\)', locator)
                if rid_match:
                    self.scroll_into_view_by_resource_id(rid_match.group(1))
                    return

            elif by == AppiumBy.ID:
                self.scroll_into_view_by_resource_id(locator)
                return

            elif by == AppiumBy.ACCESSIBILITY_ID:
                self.scroll_into_view_by_description(locator)
                return

            # Unknown locator type — try UP first, then DOWN (1 swipe each)
            try:
                self.scroll_to_element(locator, by, direction="up", max_scrolls=1)
                return
            except Exception:
                pass
            self.scroll_to_element(locator, by, direction="down", max_scrolls=1)

        except Exception:
            pass  # Caller checks visibility after this

    def is_text_visible_with_scroll(self, text: str, timeout: int = 3) -> bool:
        """
        Scroll into view by text, then check visibility.

        Self-contained: handles scroll first (aspect-ratio safe via UiScrollable),
        then verifies visibility. Includes Fabric View Culling retry for
        RN 0.83+/Expo 55+ where off-screen elements inside ScrollView are not
        mounted into the native view tree until physically scrolled into viewport.

        Args:
            text: Exact text to find and verify
            timeout: Visibility check timeout after scroll

        Returns:
            True if text is visible after scroll attempt, False otherwise
        """
        try:
            self.scroll_into_view_by_text(text)
        except Exception:
            pass  # May already be visible or not present

        by, loc = self._by_text(text)
        if self.is_displayed(loc, by, timeout=timeout, scroll_first=False):
            return True

        # Fabric View Culling retry (RN 0.83+/Expo 55+): off-screen elements
        # inside ScrollView are not mounted into the native view tree.
        # UiScrollable.scrollIntoView can't find unmounted elements.
        # A physical swipe reveals content, then direct-find (no UiScrollable).
        if self.platform == "android":
            logger.debug(f"Fabric View Culling retry for: '{text}'")
            self.swipe_by_percentage(0.5, 0.7, 0.5, 0.3, duration=500)
            by_contains, loc_contains = self._by_text_contains(text)
            return self.is_displayed(loc_contains, by_contains, timeout=timeout, scroll_first=False)

        return False

    def is_enabled(
        self,
        locator: str,
        by: AppiumBy = AppiumBy.ID,
        timeout: int | None = None,
        scroll_first: bool = True,
    ) -> bool:
        """
        Check if element is enabled.

        Args:
            locator: Element locator
            by: Locator strategy
            timeout: Optional timeout override
            scroll_first: If True, scroll element into view before checking.

        Returns:
            True if element is enabled, False otherwise
        """
        if scroll_first:
            self._ensure_element_in_view(locator, by)

        try:
            element = self.find_element(locator, by, timeout, _skip_heal=True)
            return element.is_enabled()
        except (TimeoutException, NoSuchElementException):
            return False

    # ==================== VERIFICATION & ASSERTIONS ====================
    # These are the BASE methods. Page objects define WHAT to check,
    # BasePage handles HOW to check and HOW to report failures.

    def verify_elements(
        self,
        checks: list[tuple],
        screen_name: str = "screen",
        timeout: int = 3,
        scroll_first: bool = True,
    ) -> dict:
        """
        Verify multiple elements are visible. Returns dict of MISSING elements.
        Empty dict = all elements found. Non-empty = tells you exactly what's missing.

        USE THIS instead of writing your own is_displayed() loops in page objects.

        Args:
            checks: List of tuples:
                - 2-tuple: (by, locator) — name extracted from locator
                - 3-tuple: (name, by, locator) — explicit name for clear reporting
            screen_name: Screen name for logging
            timeout: Timeout per element check (default: 3s — fast fail)
            scroll_first: If True, scroll each element into view before checking.
                Use for screens where elements may be below fold on different
                aspect ratios (e.g., transaction tabs, wallet sections).

        Returns:
            dict: Empty if all found. Otherwise: {element_name: {by, locator, error}}

        Example in page object:
            def verify_recharge_screen_elements(self) -> dict:
                return self.verify_elements([
                    ("Screen Title", *self._by_id(self.SCREEN_TITLE)),
                    ("Add Cash Button", *self._by_id(self.ADD_CASH_BTN)),
                    ("Balance Label", *self._by_id(self.BALANCE_LABEL)),
                ], screen_name="Recharge", scroll_first=True)

        Example in test:
            missing = page.verify_recharge_screen_elements()
            assert not missing, f"Missing elements: {missing}"
            # Output: {'Add Cash Button': {'by': 'id', 'locator': 'btn_add_cash'}}
        """
        missing = {}

        for item in checks:
            if len(item) == 3:
                name, by, locator = item
            else:
                by, locator = item
                name = self._extract_element_name(locator)

            if not self.is_displayed(locator, by, timeout=timeout, scroll_first=scroll_first):
                missing[name] = {"by": str(by), "locator": str(locator)}
                logger.warning(f"❌ MISSING on {screen_name}: '{name}' | {by}={locator}")
            else:
                logger.debug(f"✅ FOUND on {screen_name}: '{name}'")

        if missing:
            logger.error(
                f"🚨 {screen_name}: {len(missing)} element(s) missing: {', '.join(missing.keys())}"
            )
        else:
            logger.info(f"✅ {screen_name}: All {len(checks)} elements verified")

        return missing

    def verify_text(
        self,
        expected_text: dict[str, str],
        locator_map: dict[str, tuple],
        screen_name: str = "screen",
        timeout: int = 3,
        scroll_first: bool = True,
    ) -> dict:
        """
        Compare actual device text vs expected text. Returns dict of MISMATCHES.
        Empty dict = all text matches. Non-empty = tells you exactly what's wrong.

        USE THIS instead of writing your own get_text() comparison loops.

        IMPORTANT: Text mismatch = APP BUG. Do NOT auto-heal. FAIL the test.

        Args:
            expected_text: {field_name: expected_value} for static text only
            locator_map: {field_name: (by, locator)} matching expected_text keys
            screen_name: Screen name for logging
            timeout: Timeout per element (default: 3s)
            scroll_first: If True, scroll each element into view before reading text.

        Returns:
            dict: Empty if all match. Otherwise: {field: {expected, actual}}

        Example in page object:
            EXPECTED_TEXT = {
                "screen_title": "Recharge",
                "add_cash_button": "Add Cash",
                "balance_label": "Total Balance",
            }

            def verify_screen_text(self) -> dict:
                return self.verify_text(
                    expected_text=self.EXPECTED_TEXT,
                    locator_map={
                        "screen_title": self._by_id(self.SCREEN_TITLE),
                        "add_cash_button": self._by_id(self.ADD_CASH_BTN),
                        "balance_label": self._by_id(self.BALANCE_LABEL),
                    },
                    screen_name="Recharge",
                )

        Example in test:
            mismatches = page.verify_screen_text()
            assert not mismatches, f"Text mismatch: {mismatches}"
            # Output: {'add_cash_button': {'expected': 'Add Cash', 'actual': 'Add Money'}}
        """
        mismatches = {}

        for field, expected in expected_text.items():
            locator_entry = locator_map.get(field)
            if not locator_entry:
                logger.warning(f"⚠️ No locator mapped for text field '{field}' — skipping")
                continue

            by, locator = locator_entry

            try:
                actual = self.get_text(
                    locator, by, timeout=timeout, scroll_first=scroll_first, _skip_heal=True
                )

                if actual != expected:
                    mismatches[field] = {"expected": expected, "actual": actual}
                    logger.warning(
                        f"❌ TEXT MISMATCH on {screen_name}: '{field}' | "
                        f"expected='{expected}' | actual='{actual}'"
                    )
                else:
                    logger.debug(f"✅ TEXT MATCH on {screen_name}: '{field}' = '{actual}'")

            except TimeoutException:
                mismatches[field] = {"expected": expected, "actual": "ELEMENT NOT FOUND"}
                logger.error(
                    f"❌ ELEMENT NOT FOUND for text check '{field}' on {screen_name}: "
                    f"{by}={locator}"
                )
            except Exception as e:
                mismatches[field] = {"expected": expected, "actual": f"ERROR: {e}"}
                logger.error(f"❌ Error checking text '{field}' on {screen_name}: {e}")

        if mismatches:
            logger.error(
                f"🚨 {screen_name}: {len(mismatches)} text mismatch(es): "
                f"{', '.join(mismatches.keys())}"
            )
        else:
            logger.info(f"✅ {screen_name}: All {len(expected_text)} text fields verified")

        return mismatches

    def verify_dynamic_text(
        self,
        checks: list[tuple],
        screen_name: str = "screen",
        timeout: int = 3,
        scroll_first: bool = True,
    ) -> dict:
        """
        Verify dynamic text exists and matches expected FORMAT (not exact value).

        For values like balances, dates, counts — you can't hardcode the value,
        but you CAN verify the format.

        Args:
            checks: List of tuples: (field_name, by, locator, format_check_func)
                format_check_func: callable that takes str → bool
                    e.g., lambda t: t.startswith("₹")
                    e.g., lambda t: t != ""
                    e.g., lambda t: len(t) >= 2
            scroll_first: If True, scroll each element into view before reading.

        Returns:
            dict: Empty if all pass. Otherwise: {field: {actual, format_rule, error}}

        Example in page object:
            def verify_dynamic_values(self) -> dict:
                return self.verify_dynamic_text([
                    ("Balance", *self._by_id(self.BALANCE), lambda t: t.startswith("₹")),
                    ("User Name", *self._by_id(self.USER_NAME), lambda t: len(t) >= 2),
                    ("Date", *self._by_id(self.DATE), lambda t: "/" in t or "-" in t),
                ], screen_name="Recharge", scroll_first=True)
        """
        failures = {}

        for item in checks:
            name, by, locator, format_func = item

            try:
                actual = self.get_text(
                    locator, by, timeout=timeout, scroll_first=scroll_first, _skip_heal=True
                )

                if not format_func(actual):
                    failures[name] = {
                        "actual": actual,
                        "error": "Format check failed",
                    }
                    logger.warning(f"❌ FORMAT FAIL on {screen_name}: '{name}' | actual='{actual}'")
                else:
                    logger.debug(f"✅ FORMAT OK on {screen_name}: '{name}' = '{actual}'")

            except TimeoutException:
                failures[name] = {"actual": "ELEMENT NOT FOUND", "error": "Timeout"}
                logger.error(f"❌ NOT FOUND for format check '{name}' on {screen_name}")
            except Exception as e:
                failures[name] = {"actual": f"ERROR: {e}", "error": str(e)}

        return failures

    def assert_screen(
        self,
        screen_name: str,
        element_checks: list[tuple] | None = None,
        expected_text: dict[str, str] | None = None,
        text_locator_map: dict[str, tuple] | None = None,
        dynamic_checks: list[tuple] | None = None,
        scroll_first: bool = True,
    ) -> None:
        """
        ONE-CALL screen verification. Runs all checks and fails with full detail.

        This is the RECOMMENDED way to verify screens in tests.
        Runs element + text + dynamic checks in one call, reports ALL failures together.

        Args:
            screen_name: Screen name for error messages
            element_checks: For verify_elements() — list of (name, by, locator)
            expected_text: For verify_text() — {field: expected_value}
            text_locator_map: For verify_text() — {field: (by, locator)}
            dynamic_checks: For verify_dynamic_text() — list of (name, by, locator, format_func)
            scroll_first: If True, scroll each element into view before checking.
                Propagated to all sub-verification methods for aspect-ratio safety.

        Raises:
            AssertionError: With full detail of ALL failures

        Example in test:
            page.assert_screen(
                screen_name="Recharge",
                element_checks=[
                    ("Title", *page._by_id(page.SCREEN_TITLE)),
                    ("Add Cash", *page._by_id(page.ADD_CASH_BTN)),
                ],
                expected_text=page.EXPECTED_TEXT,
                text_locator_map={
                    "screen_title": page._by_id(page.SCREEN_TITLE),
                    "add_cash_button": page._by_id(page.ADD_CASH_BTN),
                },
                dynamic_checks=[
                    ("Balance", *page._by_id(page.BALANCE), lambda t: t.startswith("₹")),
                ],
                scroll_first=True,
            )
        """
        all_failures = {}

        # 1. Element presence checks
        if element_checks:
            missing = self.verify_elements(element_checks, screen_name, scroll_first=scroll_first)
            if missing:
                all_failures["missing_elements"] = missing

        # 2. Static text checks
        if expected_text and text_locator_map:
            text_mismatches = self.verify_text(
                expected_text,
                text_locator_map,
                screen_name,
                scroll_first=scroll_first,
            )
            if text_mismatches:
                all_failures["text_mismatches"] = text_mismatches

        # 3. Dynamic value format checks
        if dynamic_checks:
            format_failures = self.verify_dynamic_text(
                dynamic_checks,
                screen_name,
                scroll_first=scroll_first,
            )
            if format_failures:
                all_failures["format_failures"] = format_failures

        # Fail with ALL details at once
        if all_failures:
            failure_summary = f"\n🚨 SCREEN VERIFICATION FAILED: {screen_name}\n"

            if "missing_elements" in all_failures:
                failure_summary += f"\n  ❌ Missing elements: {all_failures['missing_elements']}"

            if "text_mismatches" in all_failures:
                failure_summary += f"\n  ❌ Text mismatches: {all_failures['text_mismatches']}"

            if "format_failures" in all_failures:
                failure_summary += f"\n  ❌ Format failures: {all_failures['format_failures']}"

            raise AssertionError(failure_summary)

    # ==================== WAIT STRATEGIES ====================

    def wait_for_element(
        self,
        locator: str,
        by: AppiumBy = AppiumBy.ID,
        timeout: int | None = None,
        condition: str = "visible",
    ) -> WebElement:
        """
        Wait for element with specified condition.

        CHANGED: Default condition is now "visible" (was "presence").
        Most use cases need visible elements, not just DOM presence.

        Args:
            locator: Element locator
            by: Locator strategy
            timeout: Optional timeout override
            condition: Wait condition ('presence', 'visible', 'clickable')

        Returns:
            WebElement when condition is met
        """
        wait = self.wait if timeout is None else WebDriverWait(self.driver, timeout)

        conditions = {
            "presence": EC.presence_of_element_located,
            "visible": EC.visibility_of_element_located,
            "clickable": EC.element_to_be_clickable,
        }

        condition_func = conditions.get(condition, EC.visibility_of_element_located)

        try:
            element = wait.until(condition_func((by, locator)))
            logger.info(f"Element became {condition}: {by}={locator}")
            return element
        except TimeoutException:
            logger.error(
                f"Element not {condition} after {timeout or self.timeout}s: {by}={locator}"
            )
            raise

    def wait_for_text(
        self,
        locator: str,
        expected_text: str,
        by: AppiumBy = AppiumBy.ID,
        timeout: int | None = None,
    ) -> "BasePage":
        """
        Wait for element to contain expected text.

        Args:
            locator: Element locator
            expected_text: Text to wait for
            by: Locator strategy
            timeout: Optional timeout override

        Returns:
            self for method chaining
        """
        wait = self.wait if timeout is None else WebDriverWait(self.driver, timeout)

        try:
            wait.until(EC.text_to_be_present_in_element((by, locator), expected_text))
            logger.info(f"Text '{expected_text}' appeared in {by}={locator}")
        except TimeoutException:
            actual_text = (
                self.get_text(locator, by, timeout=1)
                if self.is_displayed(locator, by, 1)
                else "N/A"
            )
            logger.error(f"Expected text '{expected_text}' not found. Actual: '{actual_text}'")
            raise

        return self

    def wait_for_element_to_disappear(
        self, locator: str, by: AppiumBy = AppiumBy.ID, timeout: int | None = None
    ) -> "BasePage":
        """
        Wait for element to disappear from screen.

        Args:
            locator: Element locator
            by: Locator strategy
            timeout: Optional timeout override

        Returns:
            self for method chaining
        """
        wait = self.wait if timeout is None else WebDriverWait(self.driver, timeout)

        try:
            wait.until(EC.invisibility_of_element_located((by, locator)))
            logger.info(f"Element disappeared: {by}={locator}")
        except TimeoutException:
            logger.warning(
                f"Element still visible after {timeout or self.timeout}s: {by}={locator}"
            )
            raise
        except ReadTimeoutError as e:
            # A single command blocked at the socket level (dead/stalled session),
            # raised by the per-command HTTP read timeout. Convert to a Selenium
            # TimeoutException so callers' existing timeout handling applies and
            # the suite keeps running — instead of an unwrapped urllib3 error.
            logger.warning(f"HTTP read timed out waiting for {by}={locator} to vanish: {e}")
            raise TimeoutException(
                f"Command timed out (dead session?) waiting for {by}={locator} to disappear"
            ) from e

        return self

    # ==================== SCREEN LOAD VERIFICATION ====================

    def wait_for_screen_ready(
        self,
        required_elements: list[tuple],
        screen_name: str = "screen",
        timeout: int = 15,
        fail_fast: bool = False,
    ) -> bool:
        """
        Wait until ALL required elements are visible — confirms screen is fully loaded.

        ELEMENT TIMING BREAKDOWN:
        Each element's load time is tracked individually to identify slow elements.

        Args:
            required_elements: List of tuples:
                - 2-tuple: (AppiumBy, locator)
                - 3-tuple: (AppiumBy, locator, display_name)
            screen_name: Human-readable screen name for logging
            timeout: Maximum wait time per element (default: 15s)
            fail_fast: If True, stop at first missing element

        Returns:
            True if all elements are visible

        Raises:
            TimeoutException: If any required element is not found within timeout
        """
        import time

        import allure

        # Clear stale accessibility cache — Chinese OEMs (MIUI, ColorOS, FuntouchOS, etc.)
        # These OEMs cache the UiAutomator accessibility tree aggressively.
        # After screen transitions, the tree is stale for 2-3s without this.
        if self.is_chinese_oem:
            self._clear_accessibility_cache()

        start_time = time.time()
        logger.info(f"⚡ Waiting for {screen_name} ({len(required_elements)} elements)...")

        def check_element(element_tuple):
            """Check a single element."""
            if len(element_tuple) == 3:
                by, locator, element_name = element_tuple
            else:
                by, locator = element_tuple
                element_name = self._extract_element_name(locator)

            element_start = time.time()

            try:
                self.wait_for_element(locator=locator, by=by, timeout=timeout, condition="visible")
                element_time = time.time() - element_start
                return {
                    "name": element_name,
                    "locator": locator[:60],
                    "time": element_time,
                    "status": "✅ FOUND",
                    "found": True,
                    "by": by,
                }
            except TimeoutException:
                element_time = time.time() - element_start
                return {
                    "name": element_name,
                    "locator": locator[:60],
                    "time": element_time,
                    "status": "❌ TIMEOUT",
                    "found": False,
                    "by": by,
                }

        # SERIALIZE element checks. Appium WebDriver client is NOT thread-safe — concurrent
        # commands on a single session interleave on one HTTP socket → non-deterministic
        # timeouts, stale results, sporadic screen-load failures hard to reproduce.
        # Previous implementation used ThreadPoolExecutor; correctness > marginal speed.
        missing_elements = []
        found_count = 0
        element_timings = []

        for elem in required_elements:
            result = check_element(elem)
            element_timings.append(result)

            if result["found"]:
                found_count += 1
                logger.info(f"  ✓ [{result['time']:.2f}s] {result['name']}")
            else:
                missing_elements.append(f"{result['name']}: {result['by']}={result['locator']}")
                logger.warning(f"  ✗ [{result['time']:.2f}s] {result['name']} - TIMEOUT")
                if fail_fast:
                    break

        total_time = time.time() - start_time

        # Generate and attach timing report
        timing_report = self._generate_element_timing_report(
            screen_name, element_timings, total_time
        )
        try:
            allure.attach(
                timing_report,
                name=f"📊 {screen_name} - Element Timing",
                attachment_type=allure.attachment_type.TEXT,
            )
        except Exception:
            pass

        if missing_elements:
            error_msg = (
                f"{screen_name} not loaded after {total_time:.1f}s. "
                f"Missing: {', '.join(missing_elements[:3])}"
            )
            logger.error(error_msg)
            self.capture_screenshot(f"screen_not_ready_{screen_name.replace(' ', '_')}")
            raise TimeoutException(error_msg)

        # Log slowest element
        if element_timings:
            slowest = max(element_timings, key=lambda x: x["time"])
            if slowest["time"] > 1.0:
                logger.warning(f"  ⚠️ SLOWEST: {slowest['name']} took {slowest['time']:.2f}s")

        logger.info(
            f"✓ {screen_name} loaded in {total_time:.1f}s "
            f"({found_count}/{len(required_elements)} elements)"
        )
        return True

    # ==================== SCROLL & SWIPE ====================

    def swipe(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int = 1000
    ) -> "BasePage":
        """
        Perform swipe gesture.

        Args:
            start_x, start_y: Starting coordinates
            end_x, end_y: Ending coordinates
            duration: Swipe duration in milliseconds

        Returns:
            self for method chaining
        """
        logger.debug(f"Swiping from ({start_x}, {start_y}) to ({end_x}, {end_y})")
        self.driver.swipe(start_x, start_y, end_x, end_y, duration)
        return self

    def swipe_by_percentage(
        self,
        start_x_percent: float,
        start_y_percent: float,
        end_x_percent: float,
        end_y_percent: float,
        duration: int = 1000,
        respect_safe_areas: bool = False,  # NEW: Opt-in parameter (default False for backward compat)
    ) -> "BasePage":
        """
        Swipe using percentage coordinates (0.0-1.0).

        Args:
            start_x_percent, start_y_percent: Starting position (0.0 to 1.0)
            end_x_percent, end_y_percent: Ending position (0.0 to 1.0)
            duration: Swipe duration in milliseconds
            respect_safe_areas: If True, adjust coordinates to avoid notches/nav bars

        Example:
            # Old usage (still works unchanged):
            self.swipe_by_percentage(0.5, 0.7, 0.5, 0.3)

            # New usage (device-aware):
            self.swipe_by_percentage(0.5, 0.7, 0.5, 0.3, respect_safe_areas=True)

        Returns:
            self for method chaining
        """
        size = self.get_screen_size()  # Uses cache
        width, height = size["width"], size["height"]

        if respect_safe_areas:
            # NEW: Device-aware calculation
            if abs(start_x_percent - end_x_percent) < 0.05:  # Vertical swipe
                start_x, start_y, end_x, end_y = self.calculate_swipe_coordinates(
                    start_y_pct=start_y_percent,
                    end_y_pct=end_y_percent,
                    x_pct=start_x_percent,
                    respect_safe_areas=True,
                )
            else:  # Horizontal swipe
                safe_top, safe_bottom, safe_left, safe_right = self.get_safe_swipe_bounds()
                start_x = int(
                    width * (safe_left + (1.0 - safe_left - safe_right) * start_x_percent)
                )
                end_x = int(width * (safe_left + (1.0 - safe_left - safe_right) * end_x_percent))
                start_y = int(height * start_y_percent)
                end_y = int(height * end_y_percent)
        else:
            # OLD: Direct percentage mapping (unchanged behavior)
            start_x = int(width * start_x_percent)
            start_y = int(height * start_y_percent)
            end_x = int(width * end_x_percent)
            end_y = int(height * end_y_percent)

        return self.swipe(start_x, start_y, end_x, end_y, duration)

    def scroll_to_element(
        self,
        locator: str,
        by: AppiumBy = AppiumBy.ID,
        direction: str = "down",
        max_scrolls: int = 3,
        respect_safe_areas: bool = True,  # NEW: Default True for better behavior
    ) -> WebElement:
        """
        Scroll until element is visible, with device-aware scroll distance.

        Args:
            locator: Element locator
            by: Locator strategy
            direction: 'up', 'down', 'left', or 'right'
            max_scrolls: Maximum scroll attempts
            respect_safe_areas: If True, use device-specific optimal scroll distance

        Returns:
            WebElement when found

        Raises:
            TimeoutException: If element not found after max scrolls
        """
        for i in range(max_scrolls):
            try:
                element = self.find_element(locator, by, timeout=2)
                if element.is_displayed():
                    logger.info(f"Element found after {i} scrolls")
                    return element
            except (TimeoutException, NoSuchElementException):
                pass

            # NEW: Device-aware scroll distance calculation
            if respect_safe_areas and self._device_profile:
                scroll_distance = self._device_profile.scroll_distance_pct
                safe_top, safe_bottom, _, _ = self.get_safe_swipe_bounds()

                if direction == "down":
                    start_y = 1.0 - safe_bottom - 0.05
                    end_y = max(safe_top + 0.05, start_y - scroll_distance)
                elif direction == "up":
                    start_y = safe_top + 0.05
                    end_y = min(1.0 - safe_bottom - 0.05, start_y + scroll_distance)
                else:  # left/right
                    if direction == "left":
                        self.swipe_by_percentage(0.8, 0.5, 0.2, 0.5, respect_safe_areas=False)
                    else:
                        self.swipe_by_percentage(0.2, 0.5, 0.8, 0.5, respect_safe_areas=False)
                    time.sleep(0.5)
                    continue

                self.swipe_by_percentage(0.5, start_y, 0.5, end_y, respect_safe_areas=True)
            else:
                # OLD: Fixed percentage scrolling (backward compatible)
                if direction == "down":
                    self.swipe_by_percentage(0.5, 0.7, 0.5, 0.3)
                elif direction == "up":
                    self.swipe_by_percentage(0.5, 0.3, 0.5, 0.7)
                elif direction == "left":
                    self.swipe_by_percentage(0.8, 0.5, 0.2, 0.5)
                else:
                    self.swipe_by_percentage(0.2, 0.5, 0.8, 0.5)

            time.sleep(0.5)

        raise TimeoutException(
            f"Element not found after {max_scrolls} scrolls.\n"
            f"Locator: by={by}, value={locator}\n"
            f"Device: {self._device_profile.name if self._device_profile else 'Unknown'}"
        )

    # ── UiScrollable / Native Scroll Helpers ──────────────────────────────

    def scroll_into_view_by_text(
        self,
        text: str,
        exact: bool = True,
        container_id: str | None = None,
        max_swipes: int = 8,
    ) -> WebElement:
        """
        Scroll until element with given text is visible. Uses UiScrollable on
        Android (fastest, aspect-ratio safe) and swipe-until-visible on iOS.

        Args:
            text: The visible text of the target element
            exact: True for exact text match, False for contains
            container_id: Optional resource-id of scrollable container
                          (use when screen has multiple scroll views)
            max_swipes: Max swipes for iOS fallback

        Returns:
            WebElement when found

        Raises:
            NoSuchElementException: If element not found after scrolling
        """
        if self.platform == "android":
            return self._android_scroll_into_view_by_text(text, exact, container_id)
        else:
            return self._ios_scroll_until_visible(
                *self._by_text(text) if exact else self._by_text_contains(text),
                max_swipes=max_swipes,
            )

    def scroll_into_view_by_resource_id(
        self,
        resource_id: str,
        container_id: str | None = None,
        max_swipes: int = 8,
    ) -> WebElement:
        """
        Scroll until element with given resource-id is visible. Uses
        UiScrollable on Android, swipe-until-visible on iOS.

        Args:
            resource_id: The resource-id (Android) or accessibility id (iOS)
            container_id: Optional resource-id of scrollable container
            max_swipes: Max swipes for iOS fallback

        Returns:
            WebElement when found

        Raises:
            NoSuchElementException: If element not found after scrolling
        """
        if self.platform == "android":
            return self._android_scroll_into_view_by_resource_id(resource_id, container_id)
        else:
            by, loc = self._by_id(resource_id)
            return self._ios_scroll_until_visible(by, loc, max_swipes=max_swipes)

    def scroll_into_view_by_description(
        self,
        description: str,
        container_id: str | None = None,
        max_swipes: int = 8,
    ) -> WebElement:
        """
        Scroll until element with given content-description is visible.

        Args:
            description: The content-desc (Android) or accessibility label (iOS)
            container_id: Optional resource-id of scrollable container
            max_swipes: Max swipes for iOS fallback

        Returns:
            WebElement when found

        Raises:
            NoSuchElementException: If element not found after scrolling
        """
        if self.platform == "android":
            return self._android_scroll_into_view_by_description(description, container_id)
        else:
            by, loc = self._by_desc(description)
            return self._ios_scroll_until_visible(by, loc, max_swipes=max_swipes)

    # ── Android UiScrollable (private helpers) ────────────────────────────

    def _android_scrollable_prefix(self, container_id: str | None = None) -> str:
        """Build the UiScrollable prefix, optionally scoped to a container."""
        if container_id:
            return f'new UiScrollable(new UiSelector().resourceId("{container_id}"))'
        return "new UiScrollable(new UiSelector().scrollable(true))"

    def _android_scroll_into_view_by_text(
        self, text: str, exact: bool = True, container_id: str | None = None
    ) -> WebElement:
        prefix = self._android_scrollable_prefix(container_id)
        if exact:
            selector = f'.scrollIntoView(new UiSelector().text("{text}"))'
        else:
            selector = f'.scrollIntoView(new UiSelector().textContains("{text}"))'
        ui = prefix + selector
        logger.info(f"Android UiScrollable scroll to text: '{text}' (exact={exact})")
        return self.driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, ui)

    def _android_scroll_into_view_by_resource_id(
        self, resource_id: str, container_id: str | None = None
    ) -> WebElement:
        prefix = self._android_scrollable_prefix(container_id)
        selector = f'.scrollIntoView(new UiSelector().resourceId("{resource_id}"))'
        ui = prefix + selector
        logger.info(f"Android UiScrollable scroll to resource-id: '{resource_id}'")
        return self.driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, ui)

    def _android_scroll_into_view_by_description(
        self, description: str, container_id: str | None = None
    ) -> WebElement:
        prefix = self._android_scrollable_prefix(container_id)
        selector = f'.scrollIntoView(new UiSelector().description("{description}"))'
        ui = prefix + selector
        logger.info(f"Android UiScrollable scroll to description: '{description}'")
        return self.driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, ui)

    def _android_scroll_horizontal_to_text(
        self, text: str, container_id: str | None = None
    ) -> WebElement:
        """Horizontal scroll using UiScrollable setAsHorizontalList."""
        prefix = self._android_scrollable_prefix(container_id)
        ui = f'{prefix}.setAsHorizontalList().scrollIntoView(new UiSelector().text("{text}"))'
        logger.info(f"Android horizontal scroll to text: '{text}'")
        return self.driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, ui)

    def swipe_carousel(self, anchor, direction: str = "left") -> "BasePage":
        """Swipe a horizontal carousel using a visible element as Y-anchor.

        Finds the anchor element, gets its Y position, then swipes
        horizontally at that Y. The direction parameter controls left or right.

        Why coordinates instead of UiScrollable or mobile:scrollGesture?
        React Native horizontal FlatList carousels are NOT exposed as
        scrollable=true to UiAutomator. mobile:scrollGesture bounces back,
        mobile:swipeGesture triggers back navigation. Coordinate swipe at
        the anchor's Y is the only reliable approach for nested carousels.

        Args:
            anchor: Either a text string (found via _by_text) or a
                    (by, locator) tuple for any locator strategy.
            direction: "left" (reveal next cards) or "right" (go back).

        Returns:
            self for method chaining

        Raises:
            TimeoutException: If anchor element is not found.
        """
        start_x = 0.8 if direction == "left" else 0.2
        end_x = 0.2 if direction == "left" else 0.8

        if isinstance(anchor, tuple):
            by, loc = anchor
            anchor_label = loc
        else:
            by, loc = self._by_text(anchor)
            anchor_label = anchor

        element = self.find_element(loc, by, timeout=3)
        screen = self.get_screen_size()
        swipe_y = element.location["y"] / screen["height"]
        swipe_y = max(0.1, min(swipe_y, 0.85))
        self.swipe_by_percentage(start_x, swipe_y, end_x, swipe_y, duration=500)
        logger.info(
            f"Carousel swipe {direction.upper()} at Y={swipe_y:.2f} (anchor='{anchor_label}')"
        )
        return self

    def swipe_carousel_left(self, anchor) -> "BasePage":
        """Swipe carousel left. See swipe_carousel()."""
        return self.swipe_carousel(anchor, direction="left")

    # ── iOS Native Scroll (private helper) ────────────────────────────────

    def _ios_scroll_until_visible(
        self,
        by: str,
        locator: str,
        max_swipes: int = 8,
        direction: str = "down",
    ) -> WebElement:
        """
        iOS: swipe in direction until element is visible.
        Uses mobile: swipe (more reliable than raw coordinate swipes on iOS).
        """
        # Map our direction to iOS mobile:swipe direction
        # mobile:swipe direction is the FINGER direction, so "up" scrolls page down
        ios_direction = "up" if direction == "down" else "down"

        for i in range(max_swipes):
            try:
                el = self.driver.find_element(by, locator)
                if el.is_displayed():
                    logger.info(f"iOS: element found after {i} swipes")
                    return el
            except NoSuchElementException:
                pass

            self.driver.execute_script("mobile: swipe", {"direction": ios_direction})
            time.sleep(0.3)

        raise NoSuchElementException(
            f"iOS: element not visible after {max_swipes} swipes. by={by}, locator={locator}"
        )

    def long_press(
        self,
        locator: str,
        by: AppiumBy = AppiumBy.ID,
        duration: int = 2000,
        timeout: int | None = None,
    ) -> "BasePage":
        """
        Perform long press on element using W3C Actions API.

        Args:
            locator: Element locator
            by: Locator strategy
            duration: Press duration in milliseconds
            timeout: Optional timeout for finding element

        Returns:
            self for method chaining
        """
        element = self.find_element(locator, by, timeout)
        logger.info(f"Long pressing {by}={locator} for {duration}ms")
        actions = ActionChains(self.driver)
        actions.click_and_hold(element).pause(duration / 1000.0).release().perform()
        return self

    def refresh(self) -> "BasePage":
        """Pull to refresh gesture."""
        logger.debug("Refreshing screen")
        self.swipe_by_percentage(0.5, 0.3, 0.5, 0.7, 1000)
        return self

    # ==================== FLUENT WAIT METHODS ====================

    def fluent_wait_for_element(
        self,
        locator: str,
        by: AppiumBy = AppiumBy.ID,
        timeout: int = 30,
        poll_frequency: float | None = None,
        ignored_exceptions: tuple = (NoSuchElementException,),
    ) -> WebElement:
        """
        Fluent wait for unpredictable elements (OTP, permissions, API responses).

        Args:
            locator: Element locator
            by: Locator strategy
            timeout: Maximum wait time (default: 30s)
            poll_frequency: Polling interval (default: from env or 0.5s)
            ignored_exceptions: Exceptions to ignore during polling

        Returns:
            WebElement when found
        """
        if poll_frequency is None:
            poll_frequency = self.fluent_poll_frequency

        logger.info(f"Fluent wait for {by}={locator} (timeout={timeout}s, poll={poll_frequency}s)")
        wait = FluentWait(self.driver, timeout, poll_frequency, ignored_exceptions)

        try:
            element = wait.until(EC.visibility_of_element_located((by, locator)))
            logger.info(f"✓ Found with fluent wait: {by}={locator}")
            return element
        except TimeoutException:
            logger.error(f"Fluent wait timeout after {timeout}s: {by}={locator}")
            self.capture_screenshot(f"fluent_wait_timeout_{locator.replace('/', '_')[:50]}")
            raise

    def fluent_wait_for_clickable(
        self,
        locator: str,
        by: AppiumBy = AppiumBy.ID,
        timeout: int = 30,
        poll_frequency: float | None = None,
    ) -> WebElement:
        """
        Fluent wait for element to be clickable (rare use — disabled buttons).

        Args:
            locator: Element locator
            by: Locator strategy
            timeout: Maximum wait time
            poll_frequency: Polling interval

        Returns:
            WebElement when clickable
        """
        if poll_frequency is None:
            poll_frequency = self.fluent_poll_frequency

        wait = FluentWait(self.driver, timeout, poll_frequency, (NoSuchElementException,))

        try:
            element = wait.until(EC.element_to_be_clickable((by, locator)))
            logger.info(f"✓ Element clickable: {by}={locator}")
            return element
        except TimeoutException:
            logger.error(f"Element not clickable after {timeout}s: {by}={locator}")
            raise

    def fluent_wait_for_condition(
        self,
        condition_func: Callable,
        timeout: int = 30,
        poll_frequency: float | None = None,
        error_message: str = "Condition not met",
    ) -> Any:
        """
        Fluent wait for custom condition with polling.

        Args:
            condition_func: Callable that returns truthy value when condition met
            timeout: Maximum wait time
            poll_frequency: Polling interval
            error_message: Error message for timeout

        Returns:
            Result of condition_func when truthy
        """
        if poll_frequency is None:
            poll_frequency = self.fluent_poll_frequency

        wait = FluentWait(
            self.driver,
            timeout,
            poll_frequency,
            (NoSuchElementException, StaleElementReferenceException),
        )

        try:
            result = wait.until(condition_func)
            logger.info(f"✓ Condition met: {error_message}")
            return result
        except TimeoutException:
            logger.error(f"✗ {error_message} after {timeout}s")
            raise TimeoutException(error_message) from None

    # ==================== DEVICE UTILITY ====================

    def capture_screenshot(self, name: str = "screenshot", attach: bool = True) -> str:
        """Capture screenshot with timestamp, save as PNG to disk, and attach JPEG to Allure.

        PNG stays on disk for debugging; JPEG goes to Allure so the report UI
        stays fast even with many screenshots. Pass `attach=False` to skip
        the Allure attachment (e.g. when the caller will attach explicitly).
        """
        screenshots_dir = Path("logs/screenshots")
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{name}_{timestamp}.png"
        screenshot_path = screenshots_dir / filename

        try:
            self.driver.save_screenshot(str(screenshot_path))
            logger.info(f"Screenshot saved: {screenshot_path}")
            if attach:
                try:
                    from src.reporting.allure_helpers import attach_as_jpeg

                    attach_as_jpeg(str(screenshot_path), name)
                except Exception as attach_exc:  # noqa: BLE001
                    logger.warning("Failed to auto-attach screenshot to Allure: %s", attach_exc)
            return str(screenshot_path)
        except Exception as e:
            logger.error(f"Failed to capture screenshot: {e}")
            return ""

    def back(self) -> "BasePage":
        """Press device back button."""
        logger.debug("Pressing back button")
        self.driver.back()
        return self

    def hide_keyboard(self) -> "BasePage":
        """Hide keyboard if visible."""
        try:
            self.driver.hide_keyboard()
            logger.debug("Keyboard hidden")
        except Exception:
            logger.debug("Keyboard not visible or could not be hidden")
        return self

    def get_page_source(self) -> str:
        """Get current page source (XML)."""
        return self.driver.page_source

    def get_window_size(self) -> dict:
        """Get window dimensions."""
        return self.driver.get_window_size()

    def get_device_info(self) -> dict:
        """Get device information."""
        return {
            "platform": self.driver.capabilities.get("platformName", "Unknown"),
            "version": self.driver.capabilities.get("platformVersion", "Unknown"),
            "device": self.driver.capabilities.get("deviceName", "Unknown"),
            "udid": self.driver.capabilities.get("udid", "Unknown"),
        }

    def get_element_attribute(
        self, locator: str, attribute: str, by: AppiumBy = AppiumBy.ID, timeout: int | None = None
    ) -> str:
        """Get attribute value from element."""
        element = self.find_element(locator, by, timeout)
        value = element.get_attribute(attribute)
        logger.debug(f"Element {by}={locator} attribute '{attribute}': {value}")
        return value or ""

    def handle_permission_popup(self, allow: bool = True, max_attempts: int = 3) -> "BasePage":
        """
        Handle Android permission popups (system + in-app custom dialogs).

        ADB pre-grants system permissions in conftest, but Chinese OEMs (OnePlus,
        Oppo, Xiaomi) still show in-app notification permission dialogs.
        Uses short timeouts to avoid wasting time when no popup is present.

        Args:
            allow: True to allow permission, False to deny
            max_attempts: Maximum number of permission dialogs to handle

        Returns:
            self for method chaining
        """
        button_text = "Allow" if allow else "Don't allow"
        for attempt in range(max_attempts):
            try:
                by, loc = self._by_text(button_text)
                if self.is_displayed(loc, by, timeout=2, scroll_first=False):
                    self.tap(loc, by, timeout=2)
                    logger.info(
                        f"✓ Permission popup handled: tapped '{button_text}' (attempt {attempt + 1})"
                    )
                    continue
            except Exception:
                pass
            break
        return self

    # ==================== HELPER METHODS ====================

    def _extract_element_name(self, locator: str) -> str:
        """Extract human-readable name from locator."""
        import re

        text_match = re.search(r"@text='([^']+)'", locator)
        if text_match:
            return text_match.group(1)

        id_match = re.search(r"@resource-id='([^']+)'", locator)
        if id_match:
            return id_match.group(1).split("/")[-1]

        desc_match = re.search(r"@content-desc='([^']+)'", locator)
        if desc_match:
            return desc_match.group(1)

        if "." in locator and "/" not in locator:
            return locator.split(".")[-1]

        return locator[:30] + "..." if len(locator) > 30 else locator

    def _generate_element_timing_report(
        self, screen_name: str, element_timings: list[dict], total_time: float
    ) -> str:
        """Generate element timing breakdown report for Allure."""
        lines = [
            f"📊 ELEMENT TIMING: {screen_name}",
            "=" * 60,
            "",
            f"{'Element':<30} │ {'Time':>8} │ {'Status':<10}",
            "─" * 30 + "─┼──────────┼───────────",
        ]

        sorted_timings = sorted(element_timings, key=lambda x: -x["time"])

        for elem in sorted_timings:
            name = elem["name"][:30]
            time_str = f"{elem['time']:.2f}s"
            status = elem["status"]

            if elem["time"] > 2.0:
                lines.append(f"🔴 {name:<27} │ {time_str:>8} │ {status:<10} ← SLOW!")
            elif elem["time"] > 1.0:
                lines.append(f"🟡 {name:<27} │ {time_str:>8} │ {status:<10}")
            else:
                lines.append(f"🟢 {name:<27} │ {time_str:>8} │ {status:<10}")

        lines.extend(
            [
                "─" * 60,
                f"TOTAL: {total_time:.2f}s",
                "",
                "🔴 > 2.0s = SLOW  🟡 > 1.0s = OK  🟢 < 1.0s = FAST",
            ]
        )

        return "\n".join(lines)
