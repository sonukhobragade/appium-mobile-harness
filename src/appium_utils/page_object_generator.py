"""
Page Object Generator — Auto-generate Page Objects from live Appium screen.

Connects to running Appium session, scans the current screen, and generates
a complete Page Object class matching the framework pattern:
- Extends BasePage
- Uses @allure_step_class and @screen_timing_class decorators
- Locator constants using FASTEST strategy per platform
- Business methods (tap, enter, verify, wait)
- Screen timing baselines

Usage:
    # From running test or interactive session:
    from src.appium_utils.page_object_generator import PageObjectGenerator

    gen = PageObjectGenerator(driver)
    gen.generate("settings")  # Generates src/pages/settings_page.py

    # CLI usage:
    python -m src.appium_utils.page_object_generator --screen settings
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

from .utils import detect_platform


class PageObjectGenerator:
    """Generate Page Object files from live Appium screen."""

    # Android class → human-readable type
    ANDROID_TYPE_MAP = {
        "android.widget.Button": "Button",
        "android.widget.ImageButton": "ImageButton",
        "android.widget.EditText": "EditText",
        "android.widget.TextView": "TextView",
        "android.widget.ImageView": "ImageView",
        "android.widget.CheckBox": "CheckBox",
        "android.widget.RadioButton": "RadioButton",
        "android.widget.Switch": "Switch",
        "android.widget.ToggleButton": "ToggleButton",
        "android.widget.Spinner": "Spinner",
        "android.widget.SeekBar": "SeekBar",
        "android.widget.ProgressBar": "ProgressBar",
        "android.widget.ScrollView": "ScrollView",
        "android.widget.ListView": "ListView",
        "android.widget.RecyclerView": "RecyclerView",
        "android.view.View": "View",
        "android.view.ViewGroup": "ViewGroup",
    }

    # iOS class → human-readable type
    IOS_TYPE_MAP = {
        "XCUIElementTypeButton": "Button",
        "XCUIElementTypeTextField": "TextField",
        "XCUIElementTypeSecureTextField": "SecureTextField",
        "XCUIElementTypeStaticText": "StaticText",
        "XCUIElementTypeImage": "Image",
        "XCUIElementTypeSwitch": "Switch",
        "XCUIElementTypeCell": "Cell",
        "XCUIElementTypeLink": "Link",
        "XCUIElementTypeSlider": "Slider",
        "XCUIElementTypeNavigationBar": "NavBar",
        "XCUIElementTypeTabBar": "TabBar",
        "XCUIElementTypeScrollView": "ScrollView",
        "XCUIElementTypeTable": "Table",
        "XCUIElementTypeOther": "Other",
    }

    # Interactive Android classes
    ANDROID_INTERACTIVE = {
        "android.widget.Button",
        "android.widget.ImageButton",
        "android.widget.EditText",
        "android.widget.CheckBox",
        "android.widget.RadioButton",
        "android.widget.Switch",
        "android.widget.ToggleButton",
        "android.widget.Spinner",
        "android.widget.SeekBar",
    }

    # Interactive iOS types
    IOS_INTERACTIVE = {
        "XCUIElementTypeButton",
        "XCUIElementTypeTextField",
        "XCUIElementTypeSecureTextField",
        "XCUIElementTypeSwitch",
        "XCUIElementTypeLink",
        "XCUIElementTypeSlider",
        "XCUIElementTypeCell",
    }

    def __init__(self, driver: Any) -> None:
        self.driver = driver
        self.platform = detect_platform(driver)

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def generate(
        self,
        screen_name: str,
        output_dir: str = "src/pages",
        dry_run: bool = False,
    ) -> str:
        """
        Generate a Page Object file from the current screen.

        Args:
            screen_name: Name of the screen (e.g. "settings", "profile", "chat")
            output_dir: Where to save the file (default: src/pages/)
            dry_run: If True, print code instead of writing file

        Returns:
            Generated code as string
        """
        # 1. Get page source (single fast call)
        page_source = self.driver.page_source

        # 2. Parse elements from XML
        elements = self._parse_page_source(page_source)

        # 3. Categorize elements
        interactive, display, all_elements = self._categorize_elements(elements)

        # 4. Generate Page Object code
        code = self._generate_page_object(screen_name, interactive, display, all_elements)

        # 5. Output
        if dry_run:
            print(code)
        else:
            file_path = Path(output_dir) / f"{screen_name}_page.py"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(code)
            print(f"\n{'=' * 60}")
            print(f"  Page Object generated: {file_path}")
            print(f"  Platform: {self.platform}")
            print(f"  Interactive elements: {len(interactive)}")
            print(f"  Display elements: {len(display)}")
            print(f"{'=' * 60}\n")

        return code

    def scan(self, silent: bool = False) -> dict[str, list] | None:
        """
        Scan current screen and print element summary with best locators.
        Quick alternative to Appium Inspector.

        Args:
            silent: If True, suppress output and return elements dict

        Returns:
            If silent=True, returns dict with 'interactive' and 'display' keys
            Otherwise returns None (prints to stdout)
        """
        page_source = self.driver.page_source
        elements = self._parse_page_source(page_source)
        interactive, display, _ = self._categorize_elements(elements)

        if silent:
            return {
                "interactive": interactive,
                "display": display,
            }

        print(f"\n{'=' * 70}")
        print(f"  SCREEN SCAN — {self.platform.upper()}")
        print(f"  Interactive: {len(interactive)} | Display: {len(display)}")
        print(f"{'=' * 70}\n")

        if interactive:
            print("--- INTERACTIVE ELEMENTS (buttons, inputs, toggles) ---\n")
            for i, elem in enumerate(interactive, 1):
                self._print_element_summary(i, elem)

        if display:
            print("\n--- DISPLAY ELEMENTS (text, headers, labels) ---\n")
            for i, elem in enumerate(display[:15], 1):  # Limit display elements
                self._print_element_summary(i, elem)

        print(f"\n{'=' * 70}")
        print("  TIP: Run gen.generate('screen_name') to create Page Object")
        print(f"{'=' * 70}\n")

        return None

    # =========================================================================
    # XML PARSING
    # =========================================================================

    def _parse_page_source(self, xml_source: str) -> list[dict]:
        """Parse page source XML into element dicts."""
        root = ET.fromstring(xml_source)

        if self.platform == "android":
            return self._parse_android_xml(root)
        return self._parse_ios_xml(root)

    def _parse_android_xml(self, root: ET.Element) -> list[dict]:
        """Parse Android XML hierarchy."""
        elements = []
        seen_ids = set()

        for elem in root.iter():
            attribs = elem.attrib
            if not attribs:
                continue

            resource_id = attribs.get("resource-id", "")
            text = attribs.get("text", "")
            content_desc = attribs.get("content-desc", "")
            class_name = attribs.get("class", "")
            clickable = attribs.get("clickable", "false") == "true"
            enabled = attribs.get("enabled", "false") == "true"
            displayed = attribs.get("displayed", "true") == "true"
            focusable = attribs.get("focusable", "false") == "true"

            # Skip elements with no identifiers
            if not (resource_id or text or content_desc):
                continue

            # Deduplicate by resource-id
            dedup_key = resource_id or f"{text}:{class_name}"
            if dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)

            # Determine short type name
            type_name = self.ANDROID_TYPE_MAP.get(
                class_name, class_name.split(".")[-1] if class_name else "Unknown"
            )

            # Determine if interactive
            is_interactive = clickable or focusable or class_name in self.ANDROID_INTERACTIVE

            elements.append(
                {
                    "platform": "android",
                    "resource_id": resource_id,
                    "text": text,
                    "content_desc": content_desc,
                    "class_name": class_name,
                    "type": type_name,
                    "clickable": clickable,
                    "enabled": enabled,
                    "displayed": displayed,
                    "is_interactive": is_interactive and enabled,
                    "is_input": class_name == "android.widget.EditText",
                }
            )

        return elements

    def _parse_ios_xml(self, root: ET.Element) -> list[dict]:
        """Parse iOS XML hierarchy."""
        elements = []
        seen_ids = set()

        for elem in root.iter():
            attribs = elem.attrib
            if not attribs:
                continue

            elem_type = attribs.get("type", "")
            name = attribs.get("name", "")
            label = attribs.get("label", "")
            value = attribs.get("value", "")
            enabled = attribs.get("enabled", "true") == "true"
            visible = attribs.get("visible", "true") == "true"

            # Skip elements with no identifiers
            if not (name or label):
                continue

            # Deduplicate
            dedup_key = name or f"{label}:{elem_type}"
            if dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)

            type_name = self.IOS_TYPE_MAP.get(elem_type, elem_type.replace("XCUIElementType", ""))

            is_interactive = elem_type in self.IOS_INTERACTIVE
            is_input = elem_type in {
                "XCUIElementTypeTextField",
                "XCUIElementTypeSecureTextField",
            }

            elements.append(
                {
                    "platform": "ios",
                    "name": name,
                    "label": label,
                    "value": value,
                    "type_raw": elem_type,
                    "type": type_name,
                    "enabled": enabled,
                    "visible": visible,
                    "is_interactive": is_interactive and enabled,
                    "is_input": is_input,
                }
            )

        return elements

    # =========================================================================
    # ELEMENT CATEGORIZATION
    # =========================================================================

    def _categorize_elements(self, elements: list[dict]):
        """Split into interactive vs display elements."""
        interactive = [e for e in elements if e["is_interactive"]]
        display = [e for e in elements if not e["is_interactive"] and self._has_text(e)]
        return interactive, display, elements

    def _has_text(self, elem: dict) -> bool:
        """Check if element has visible text."""
        if self.platform == "android":
            return bool(elem.get("text", "").strip())
        return bool(elem.get("label", "").strip() or elem.get("name", "").strip())

    # =========================================================================
    # LOCATOR GENERATION (FAST — no XPath!)
    # =========================================================================

    def _best_locator(self, elem: dict) -> tuple[str, str, str]:
        """
        Return (AppiumBy_constant, locator_value, strategy_name) using fastest strategy.

        Priority:
            Android: ID → Accessibility ID → UiAutomator2 (text/class) → XPath (last resort)
            iOS:     Accessibility ID → iOS Predicate → iOS Class Chain → XPath (last resort)
        """
        if self.platform == "android":
            return self._best_android_locator(elem)
        return self._best_ios_locator(elem)

    def _best_android_locator(self, elem: dict) -> tuple[str, str, str]:
        """Get fastest Android locator."""
        resource_id = elem.get("resource_id", "")
        text = elem.get("text", "")
        content_desc = elem.get("content_desc", "")
        class_name = elem.get("class_name", "")

        # 1. resource-id (fastest)
        if resource_id:
            # Strip package prefix for cleaner ID
            short_id = resource_id.split("/")[-1] if "/" in resource_id else resource_id
            return ("AppiumBy.ID", resource_id, f"ID: {short_id}")

        # 2. content-desc → Accessibility ID (fast, cross-platform)
        if content_desc:
            return ("AppiumBy.ACCESSIBILITY_ID", content_desc, f"Acc: {content_desc}")

        # 3. UiAutomator2 by text (fast, Android-native)
        if text:
            escaped_text = text.replace('"', '\\"')
            uia = f'new UiSelector().text("{escaped_text}")'
            return ("AppiumBy.ANDROID_UIAUTOMATOR", uia, f"UiA: text={text[:30]}")

        # 4. UiAutomator2 by class (if unique enough)
        if class_name:
            uia = f'new UiSelector().className("{class_name}")'
            return ("AppiumBy.ANDROID_UIAUTOMATOR", uia, f"UiA: class={class_name.split('.')[-1]}")

        # 5. Fallback XPath (slow — avoid)
        return ("AppiumBy.XPATH", f"//*[@class='{class_name}']", "XPath (SLOW)")

    def _best_ios_locator(self, elem: dict) -> tuple[str, str, str]:
        """Get fastest iOS locator."""
        name = elem.get("name", "")
        label = elem.get("label", "")
        type_raw = elem.get("type_raw", "")

        # 1. name → Accessibility ID (fastest, cross-platform)
        if name:
            return ("AppiumBy.ACCESSIBILITY_ID", name, f"Acc: {name}")

        # 2. iOS Predicate String by label (fast, iOS-native)
        if label:
            escaped = label.replace('"', '\\"')
            predicate = f'label == "{escaped}"'
            return ("AppiumBy.IOS_PREDICATE", predicate, f"Pred: label={label[:30]}")

        # 3. iOS Class Chain (fast)
        if type_raw:
            chain = f"**/{type_raw}"
            return ("AppiumBy.IOS_CLASS_CHAIN", chain, f"Chain: {type_raw}")

        # 4. Fallback XPath (slow)
        return ("AppiumBy.XPATH", f"//*[@type='{type_raw}']", "XPath (SLOW)")

    # =========================================================================
    # CONSTANT NAME GENERATION
    # =========================================================================

    def _to_constant_name(self, elem: dict) -> str:
        """Generate a UPPER_SNAKE_CASE constant name from element."""
        # Build a descriptive name
        if self.platform == "android":
            base = elem.get("resource_id", "").split("/")[-1] if elem.get("resource_id") else ""
            if not base:
                base = elem.get("content_desc", "") or elem.get("text", "")
        else:
            base = elem.get("name", "") or elem.get("label", "")

        if not base:
            base = elem.get("type", "element")

        # Clean and convert to UPPER_SNAKE_CASE
        name = re.sub(r"[^a-zA-Z0-9\s_-]", "", base)
        name = re.sub(r"[\s-]+", "_", name)
        name = re.sub(r"_+", "_", name).strip("_")

        # Add type suffix for clarity
        type_suffix = ""
        if elem.get("is_input"):
            type_suffix = "_INPUT"
        elif elem.get("clickable") or elem.get("type", "").lower() in ("button", "imagebutton"):
            type_suffix = "_BUTTON"
        elif elem.get("type", "").lower() in ("textview", "statictext"):
            type_suffix = "_TEXT"

        result = name.upper()
        if type_suffix and not result.endswith(type_suffix):
            result += type_suffix

        return result or "UNNAMED_ELEMENT"

    def _to_method_name(self, elem: dict) -> str:
        """Generate a snake_case method name from element."""
        const = self._to_constant_name(elem)
        # Remove type suffix for method name
        for suffix in ("_BUTTON", "_INPUT", "_TEXT"):
            if const.endswith(suffix):
                const = const[: -len(suffix)]
        return const.lower()

    # =========================================================================
    # PAGE OBJECT CODE GENERATION
    # =========================================================================

    def _generate_page_object(
        self,
        screen_name: str,
        interactive: list[dict],
        display: list[dict],
        all_elements: list[dict],
    ) -> str:
        """Generate complete Page Object class code."""

        class_name = self._to_class_name(screen_name)
        file_desc = f"{class_name.replace('Page', ' Page')} Object for the app Mobile Application."

        # Build sections
        locator_block = self._gen_locator_constants(interactive, display)
        init_method = self._gen_init(class_name)
        wait_method = self._gen_wait_for_screen(screen_name, display)
        verify_method = self._gen_verify_elements(screen_name, display)
        business_methods = self._gen_business_methods(interactive)

        code = f'''"""
{file_desc}

Auto-generated by PageObjectGenerator on {datetime.now().strftime("%Y-%m-%d %H:%M")}.
Platform: {self.platform}
Interactive elements: {len(interactive)}
Display elements: {len(display)}

Review and customize before use:
- Adjust SCREEN_TIMING baselines after real device testing
- Remove unused locators/methods
- Add business logic specific to your flow
"""

import logging

from appium.webdriver.common.appiumby import AppiumBy

from src.pages.base_page import BasePage
from src.reporting.allure_decorators import allure_step_class
from src.reporting.screen_timing import screen_timing_class

logger = logging.getLogger(__name__)


@allure_step_class
@screen_timing_class
class {class_name}(BasePage):
    """Page Object for {screen_name.replace("_", " ").title()} screen."""

    # ========================================================================
    # SCREEN TIMING BASELINES
    # ========================================================================
    SCREEN_TIMING = {{
        "{screen_name}_screen": {{"expected": 3.0, "tolerance": 1.5}},
    }}

    # ========================================================================
    # LOCATOR DEFINITIONS — Fastest strategy per platform
    # Platform: {self.platform} | Generated: {datetime.now().strftime("%Y-%m-%d")}
    # ========================================================================
{locator_block}
{init_method}
{wait_method}
{verify_method}
{business_methods}'''

        return code

    def _to_class_name(self, screen_name: str) -> str:
        """Convert screen name to PascalCase class name."""
        parts = screen_name.replace("-", "_").split("_")
        pascal = "".join(p.capitalize() for p in parts)
        if not pascal.endswith("Page"):
            pascal += "Page"
        return pascal

    def _gen_locator_constants(self, interactive: list[dict], display: list[dict]) -> str:
        """Generate locator constant definitions."""
        lines = []

        if interactive:
            lines.append("    # --- Interactive Elements ---")
            seen_names = set()
            for elem in interactive:
                const_name = self._to_constant_name(elem)
                if const_name in seen_names:
                    continue
                seen_names.add(const_name)

                by, value, strategy = self._best_locator(elem)
                comment = f"  # {elem['type']} | {strategy}"
                lines.append(f"    {const_name} = ({by}, {value!r}){comment}")
            lines.append("")

        if display:
            lines.append("    # --- Display / Text Elements ---")
            seen_names = set()
            for elem in display[:15]:  # Limit display elements
                const_name = self._to_constant_name(elem)
                if const_name in seen_names:
                    continue
                seen_names.add(const_name)

                by, value, strategy = self._best_locator(elem)
                text_preview = self._get_display_text(elem)[:40]
                comment = f'  # "{text_preview}"'
                lines.append(f"    {const_name} = ({by}, {value!r}){comment}")
            lines.append("")

        return "\n".join(lines) if lines else "    # No identifiable elements found\n"

    def _gen_init(self, class_name: str) -> str:
        """Generate __init__ method."""
        return f'''
    def __init__(self, driver, timeout: int = 10):
        super().__init__(driver, timeout)
        logger.info("{class_name} initialized")
'''

    def _gen_wait_for_screen(self, screen_name: str, display: list[dict]) -> str:
        """Generate wait_for_xxx_screen method."""
        method_name = f"wait_for_{screen_name}_screen"

        # Pick top 2-3 display elements as screen indicators
        indicators = display[:3]
        if not indicators:
            return f'''
    def {method_name}(self, timeout: int = 15) -> "{self._to_class_name(screen_name)}":
        """Wait for {screen_name.replace("_", " ")} screen to be fully loaded."""
        # TODO: Add screen indicator elements
        import time
        time.sleep(2)  # Replace with proper wait
        return self
'''

        elem_lines = []
        for elem in indicators:
            by, value, _ = self._best_locator(elem)
            text_preview = self._get_display_text(elem)[:30]
            elem_lines.append(f'            ({by}, {value!r}, "{text_preview}"),')

        return f'''
    def {method_name}(self, timeout: int = 15) -> "{self._to_class_name(screen_name)}":
        """Wait for {screen_name.replace("_", " ")} screen to be fully loaded."""
        required_elements = [
{chr(10).join(elem_lines)}
        ]
        self.wait_for_screen_ready(
            required_elements=required_elements,
            screen_name="{screen_name.replace("_", " ").title()} Screen",
            timeout=timeout,
        )
        return self
'''

    def _gen_verify_elements(self, screen_name: str, display: list[dict]) -> str:
        """Generate verify_xxx_elements method."""
        method_name = f"verify_{screen_name}_elements"

        if not display:
            return f'''
    def {method_name}(self) -> bool:
        """Verify key elements on {screen_name.replace("_", " ")} screen."""
        # TODO: Add element verification
        return True
'''

        check_lines = []
        seen = set()
        for elem in display[:8]:
            const_name = self._to_constant_name(elem)
            if const_name in seen:
                continue
            seen.add(const_name)
            text_preview = self._get_display_text(elem)[:30]
            check_lines.append(f'            ("{text_preview}", self.{const_name}),')

        return f'''
    def {method_name}(self) -> bool:
        """Verify key elements on {screen_name.replace("_", " ")} screen."""
        elements_to_check = [
{chr(10).join(check_lines)}
        ]

        all_visible = True
        for element_name, (by, locator) in elements_to_check:
            if not self.is_displayed(locator, by, timeout=3):
                logger.warning(f"Missing: {{element_name}}")
                all_visible = False
            else:
                logger.debug(f"Found: {{element_name}}")
        return all_visible
'''

    def _gen_business_methods(self, interactive: list[dict]) -> str:
        """Generate business methods for interactive elements."""
        methods = []
        seen_methods = set()

        for elem in interactive:
            method_base = self._to_method_name(elem)
            const_name = self._to_constant_name(elem)

            if method_base in seen_methods:
                continue
            seen_methods.add(method_base)

            if elem.get("is_input"):
                # Input field → generate enter_xxx method
                methods.append(self._gen_input_method(method_base, const_name, elem))
            elif elem.get("clickable") or elem.get("type", "").lower() in (
                "button",
                "imagebutton",
                "cell",
                "link",
            ):
                # Button → generate tap_xxx method
                methods.append(self._gen_tap_method(method_base, const_name, elem))
            else:
                # Generic interactive → generate tap method
                methods.append(self._gen_tap_method(method_base, const_name, elem))

        return "\n".join(methods)

    def _gen_tap_method(self, method_base: str, const_name: str, elem: dict) -> str:
        """Generate a tap method for a button/clickable element."""
        desc = self._get_display_text(elem)[:40] or method_base
        _class_hint = self._to_class_name(
            method_base.split("_")[0] if "_" in method_base else method_base
        )

        return f'''
    def tap_{method_base}(self) -> "BasePage":
        """Tap {desc}."""
        by, locator = self.{const_name}
        self.tap(locator, by)
        return self
'''

    def _gen_input_method(self, method_base: str, const_name: str, elem: dict) -> str:
        """Generate an input method for an EditText/TextField."""
        desc = self._get_display_text(elem)[:40] or method_base

        return f'''
    def enter_{method_base}(self, text: str) -> "BasePage":
        """Enter text into {desc}."""
        by, locator = self.{const_name}
        self.input_text(locator, text, by)
        return self
'''

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _get_display_text(self, elem: dict) -> str:
        """Get human-readable text from element."""
        if self.platform == "android":
            return (
                elem.get("text", "")
                or elem.get("content_desc", "")
                or elem.get("resource_id", "").split("/")[-1]
            )
        return elem.get("label", "") or elem.get("name", "")

    def _print_element_summary(self, idx: int, elem: dict) -> None:
        """Print concise element summary for scan output."""
        by, value, strategy = self._best_locator(elem)
        text = self._get_display_text(elem)[:50]
        elem_type = elem.get("type", "?")
        const_name = self._to_constant_name(elem)

        marker = ">" if elem.get("is_input") else "*"
        print(f"  {idx:2}. [{elem_type:12}] {marker} {text}")
        print(f"      Locator:  {strategy}")
        print(f"      Constant: {const_name}")
        print(f"      Code:     ({by}, {value!r})")
        print()


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import argparse
    import time

    from appium import webdriver

    parser = argparse.ArgumentParser(description="Generate Page Object from live Appium screen")
    parser.add_argument("--screen", required=True, help="Screen name (e.g. 'settings', 'profile')")
    parser.add_argument("--output", default="src/pages", help="Output directory")
    parser.add_argument("--dry-run", action="store_true", help="Print code instead of writing file")
    parser.add_argument(
        "--scan-only", action="store_true", help="Only scan elements, don't generate"
    )
    parser.add_argument("--host", default="http://localhost:4723", help="Appium server URL")
    parser.add_argument(
        "--platform", default="android", choices=["android", "ios"], help="Platform"
    )
    parser.add_argument("--udid", help="Device UDID")
    parser.add_argument("--package", default="com.example.app", help="App package (Android)")
    parser.add_argument("--bundle-id", default="com.example.app", help="Bundle ID (iOS)")

    args = parser.parse_args()

    # Build capabilities
    if args.platform == "android":
        caps = {
            "platformName": "Android",
            "appium:automationName": "UiAutomator2",
            "appium:appPackage": args.package,
            "appium:appActivity": ".MainActivity",
            "appium:autoGrantPermissions": True,
            "appium:noReset": True,
        }
        if args.udid:
            caps["appium:udid"] = args.udid
    else:
        caps = {
            "platformName": "iOS",
            "appium:automationName": "XCUITest",
            "appium:bundleId": args.bundle_id,
            "appium:noReset": True,
        }
        if args.udid:
            caps["appium:udid"] = args.udid

    print(f"Connecting to Appium at {args.host}...")
    from appium.options.common.base import AppiumOptions

    options = AppiumOptions()
    options.load_capabilities(caps)
    driver = webdriver.Remote(args.host, options=options)

    print("Waiting for screen to settle...")
    time.sleep(3)

    gen = PageObjectGenerator(driver)

    if args.scan_only:
        gen.scan()
    else:
        gen.generate(args.screen, output_dir=args.output, dry_run=args.dry_run)

    driver.quit()
    print("Done!")
