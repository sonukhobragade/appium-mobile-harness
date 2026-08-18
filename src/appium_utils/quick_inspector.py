from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from appium import webdriver
from appium.webdriver.common.appiumby import AppiumBy

from .utils import detect_platform

CLICKABLE_SAMPLE_LIMIT = 30
SCREEN_CAPTURE_DELAY_SECONDS = 3


class QuickElementFinder:
    """Lightweight inspector that surfaces locator metadata and assertion ideas."""

    def __init__(self, driver) -> None:
        self.driver = driver
        self.platform = detect_platform(driver)

    def inspect_screen(self, suggest_assertions: bool = True) -> None:
        """Print all clickable elements with their locators."""
        try:
            _ = self.driver.page_source  # Ensure session is alive

            # Find all interactive elements based on platform
            if self.platform == "ios":
                elements = self._find_ios_elements()
            else:
                elements = self._find_android_elements()

            if len(elements) > CLICKABLE_SAMPLE_LIMIT:
                elements = elements[:CLICKABLE_SAMPLE_LIMIT]

            print("\n" + "=" * 80)
            print(f"Platform: {self.platform.upper()}")
            print(f"Found {len(elements)} interactive elements on this screen")
            print("=" * 80 + "\n")

            assertion_candidates = []

            for idx, element in enumerate(elements, 1):
                assertion_suggestion = self._print_element_info(idx, element)
                if assertion_suggestion:
                    assertion_candidates.append(assertion_suggestion)

            # Print assertion suggestions
            if suggest_assertions and assertion_candidates:
                self._print_assertion_suggestions(assertion_candidates)

        except Exception as e:
            print(f"Error inspecting screen: {e}")

    def _find_android_elements(self) -> list[Any]:
        """Find interactive elements on Android"""
        elements = []
        try:
            # Buttons
            elements.extend(self.driver.find_elements(AppiumBy.XPATH, "//android.widget.Button"))
            elements.extend(
                self.driver.find_elements(AppiumBy.XPATH, "//android.widget.ImageButton")
            )

            # Text inputs
            elements.extend(self.driver.find_elements(AppiumBy.XPATH, "//android.widget.EditText"))

            # Clickable elements
            elements.extend(self.driver.find_elements(AppiumBy.XPATH, "//*[@clickable='true']"))

            # Text views that are clickable
            elements.extend(
                self.driver.find_elements(
                    AppiumBy.XPATH, "//android.widget.TextView[@clickable='true']"
                )
            )
        except Exception as e:
            print(f"Warning: Error finding Android elements: {e}")

        # Remove duplicates
        return list(dict.fromkeys(elements))

    def _find_ios_elements(self) -> list[Any]:
        """Find interactive elements on iOS"""
        elements = []
        try:
            # Buttons
            elements.extend(self.driver.find_elements(AppiumBy.XPATH, "//XCUIElementTypeButton"))

            # Text fields
            elements.extend(self.driver.find_elements(AppiumBy.XPATH, "//XCUIElementTypeTextField"))
            elements.extend(
                self.driver.find_elements(AppiumBy.XPATH, "//XCUIElementTypeSecureTextField")
            )

            # Links
            elements.extend(self.driver.find_elements(AppiumBy.XPATH, "//XCUIElementTypeLink"))

            # Other interactive elements
            elements.extend(self.driver.find_elements(AppiumBy.XPATH, "//XCUIElementTypeCell"))
            elements.extend(self.driver.find_elements(AppiumBy.XPATH, "//XCUIElementTypeSwitch"))
        except Exception as e:
            print(f"Warning: Error finding iOS elements: {e}")

        # Remove duplicates
        return list(dict.fromkeys(elements))

    def _print_element_info(self, idx: int, element: Any) -> dict[str, str] | None:
        """Print detailed information about an element"""
        try:
            # Get element properties
            text = self._get_element_text(element)
            element_id = self._get_element_id(element)
            class_name = (
                element.get_attribute("class") or element.get_attribute("type") or "Unknown"
            )
            bounds = self._get_element_bounds(element)
            is_displayed = element.is_displayed() if hasattr(element, "is_displayed") else True
            is_enabled = element.is_enabled() if hasattr(element, "is_enabled") else True

            print(f"{idx}. {text or '[No text]'}")
            print(f"   Class: {class_name}")
            if element_id:
                print(f"   ID: {element_id}")
            if bounds:
                print(f"   Bounds: {bounds}")
            print(
                f"   State: {'Displayed' if is_displayed else 'Hidden'}, {'Enabled' if is_enabled else 'Disabled'}"
            )

            print("   Locators:")

            # Generate locators
            locators = {}
            if element_id:
                if self.platform == "android":
                    locators["id"] = element_id
                    print(f"     - AppiumBy.ID: '{element_id}'")
                else:
                    locators["accessibility_id"] = element_id
                    print(f"     - AppiumBy.ACCESSIBILITY_ID: '{element_id}'")

            if text:
                locators["text"] = text
                if self.platform == "android":
                    print(f"     - AppiumBy.XPATH: \"//*[@text='{text}']\"")
                else:
                    print(f"     - AppiumBy.XPATH: \"//*[@name='{text}']\"")
                    print(f"     - AppiumBy.XPATH: \"//*[@label='{text}']\"")

            # Generate full XPath
            xpath = self.generate_xpath(element)
            if xpath:
                locators["xpath"] = xpath
                print(f'     - AppiumBy.XPATH: "{xpath}"')

            # Determine if this element should be validated
            assertion_info = self._should_assert_element(
                text, element_id, class_name, is_displayed, is_enabled
            )

            if assertion_info:
                print(f"   ⚠️  VALIDATE THIS: {assertion_info['reason']}")
                print(f"   Suggested assertion: {assertion_info['assertion_type']}")

            print()

            return assertion_info if assertion_info else None

        except Exception as e:
            print(f"   Error getting element info: {e}\n")
            return None

    def _get_element_text(self, element: Any) -> str | None:
        """Get text from element"""
        try:
            text = element.text
            if not text:
                text = element.get_attribute("text")
            if not text:
                text = element.get_attribute("label")
            if not text:
                text = element.get_attribute("name")
            if not text:
                text = element.get_attribute("content-desc")
            return text
        except Exception:
            return None

    def _get_element_id(self, element: Any) -> str | None:
        """Get ID/resource-id from element"""
        try:
            element_id = element.get_attribute("resource-id")
            if not element_id:
                element_id = element.get_attribute("id")
            if not element_id:
                element_id = element.get_attribute("name")
            if not element_id:
                element_id = element.get_attribute("accessibility id")
            return element_id
        except Exception:
            return None

    def _get_element_bounds(self, element: Any) -> str | None:
        """Get element bounds/location"""
        try:
            location = element.location
            size = element.size
            return f"x:{location['x']}, y:{location['y']}, w:{size['width']}, h:{size['height']}"
        except Exception:
            return None

    def _should_assert_element(
        self,
        text: str | None,
        element_id: str | None,
        class_name: str,
        is_displayed: bool,
        is_enabled: bool,
    ) -> dict[str, str] | None:
        """Determine if an element should be validated in tests"""
        if not text and not element_id:
            return None

        text_lower = (text or "").lower()
        id_lower = (element_id or "").lower()

        # Keywords that indicate validation-worthy elements
        validation_keywords = {
            "error": ("Error Message", "assert_error_message"),
            "success": ("Success Message", "assert_success_message"),
            "fail": ("Failure Message", "assert_failure_message"),
            "invalid": ("Validation Message", "assert_validation_message"),
            "required": ("Required Field Indicator", "assert_required_field"),
            "title": ("Page Title", "assert_page_title"),
            "header": ("Header Text", "assert_header"),
            "heading": ("Heading Text", "assert_heading"),
            "confirm": ("Confirmation Message", "assert_confirmation"),
            "alert": ("Alert Message", "assert_alert"),
            "warning": ("Warning Message", "assert_warning"),
            "welcome": ("Welcome Message", "assert_welcome_message"),
            "loading": ("Loading State", "assert_loading_state"),
            "price": ("Price Display", "assert_price"),
            "total": ("Total Amount", "assert_total"),
            "balance": ("Balance Display", "assert_balance"),
            "status": ("Status Display", "assert_status"),
            "name": ("User Name", "assert_user_name"),
            "email": ("Email Display", "assert_email"),
            "profile": ("Profile Info", "assert_profile_info"),
            "completed": ("Completion Status", "assert_completion"),
            "enabled": ("Feature Status", "assert_feature_enabled"),
            "disabled": ("Feature Status", "assert_feature_disabled"),
            "visible": ("Visibility State", "assert_visibility"),
            "hidden": ("Visibility State", "assert_hidden"),
        }

        # Check for validation keywords
        for keyword, (reason, assertion_type) in validation_keywords.items():
            if keyword in text_lower or keyword in id_lower:
                return {
                    "text": text,
                    "element_id": element_id,
                    "reason": reason,
                    "assertion_type": assertion_type,
                    "is_displayed": is_displayed,
                    "is_enabled": is_enabled,
                    "class_name": class_name,
                }

        # Check class names for specific types
        if "TextView" in class_name or "Label" in class_name or "Text" in class_name:
            # Text elements with meaningful content should be validated
            if (
                text
                and len(text) > 3
                and not any(x in text_lower for x in ["button", "click", "tap"])
            ):
                return {
                    "text": text,
                    "element_id": element_id,
                    "reason": "Display Text/Label",
                    "assertion_type": "assert_text_displayed",
                    "is_displayed": is_displayed,
                    "is_enabled": is_enabled,
                    "class_name": class_name,
                }

        return None

    def _print_assertion_suggestions(self, assertion_candidates: Sequence[dict[str, str]]) -> None:
        """Print a summary of suggested assertions"""
        print("\n" + "=" * 80)
        print("SUGGESTED TEST ASSERTIONS")
        print("=" * 80 + "\n")

        # Group by assertion type
        grouped = {}
        for candidate in assertion_candidates:
            assertion_type = candidate["assertion_type"]
            if assertion_type not in grouped:
                grouped[assertion_type] = []
            grouped[assertion_type].append(candidate)

        # Print by category
        for assertion_type, candidates in grouped.items():
            print(f"\n{assertion_type.replace('_', ' ').title()}:")
            print("-" * 60)

            for candidate in candidates:
                locator = self._get_best_locator(candidate)
                print(f"\n  Element: {candidate['text'] or candidate['element_id']}")
                print(f"  Reason: {candidate['reason']}")
                print(
                    f"  State: {'Displayed' if candidate['is_displayed'] else 'Hidden'}, {'Enabled' if candidate['is_enabled'] else 'Disabled'}"
                )
                print("\n  Python Code:")
                self._generate_assertion_code(candidate, locator)

        # Print complete test method template
        print("\n" + "=" * 80)
        print("COMPLETE TEST METHOD TEMPLATE")
        print("=" * 80 + "\n")
        self._generate_test_template(assertion_candidates)

    def _get_best_locator(self, candidate: dict[str, str]) -> tuple[str, str]:
        """Get the best locator strategy for an element"""
        if candidate["element_id"]:
            if self.platform == "android":
                return ("id", candidate["element_id"])
            else:
                return ("accessibility_id", candidate["element_id"])
        elif candidate["text"]:
            if self.platform == "android":
                return ("xpath", f"//*[@text='{candidate['text']}']")
            else:
                return ("xpath", f"//*[@name='{candidate['text']}']")
        return ("xpath", "//element")

    def _generate_assertion_code(self, candidate: dict[str, str], locator: tuple[str, str]) -> None:
        """Generate pytest assertion code"""
        locator_type, locator_value = locator

        if locator_type == "id":
            find_code = f"element = driver.find_element(AppiumBy.ID, '{locator_value}')"
        elif locator_type == "accessibility_id":
            find_code = (
                f"element = driver.find_element(AppiumBy.ACCESSIBILITY_ID, '{locator_value}')"
            )
        else:
            find_code = f'element = driver.find_element(AppiumBy.XPATH, "{locator_value}")'

        print(f"  {find_code}")

        # Generate specific assertions based on type
        if "error" in candidate["assertion_type"] or "warning" in candidate["assertion_type"]:
            print('  assert element.is_displayed(), "Error message should be visible"')
            if candidate["text"]:
                print(
                    f"  assert element.text == '{candidate['text']}', \"Error message text mismatch\""
                )

        elif "success" in candidate["assertion_type"] or "confirm" in candidate["assertion_type"]:
            print('  assert element.is_displayed(), "Success message should be visible"')
            if candidate["text"]:
                print(
                    f"  assert '{candidate['text']}' in element.text, \"Success message not found\""
                )

        elif "title" in candidate["assertion_type"] or "header" in candidate["assertion_type"]:
            print('  assert element.is_displayed(), "Page title should be visible"')
            if candidate["text"]:
                print(f"  assert element.text == '{candidate['text']}', \"Page title mismatch\"")

        elif candidate["is_enabled"]:
            print('  assert element.is_displayed(), "Element should be visible"')
            print('  assert element.is_enabled(), "Element should be enabled"')
            if candidate["text"]:
                print(f"  assert element.text == '{candidate['text']}', \"Text content mismatch\"")
        else:
            print('  assert element.is_displayed(), "Element should be visible"')
            if candidate["text"]:
                print(
                    f"  assert '{candidate['text']}' in element.text, \"Expected text not found\""
                )

    def _generate_test_template(self, assertion_candidates: Sequence[dict[str, str]]) -> None:
        """Generate a complete pytest test method"""
        print("def test_screen_validation(driver):")
        print('    """Test to validate screen elements"""')
        print("    from appium.webdriver.common.appiumby import AppiumBy")
        print("    from selenium.webdriver.support.ui import WebDriverWait")
        print("    from selenium.webdriver.support import expected_conditions as EC")
        print()

        for idx, candidate in enumerate(assertion_candidates, 1):
            locator = self._get_best_locator(candidate)
            locator_type, locator_value = locator

            print(f"    # Validate: {candidate['reason']}")

            if locator_type == "id":
                print(f"    element_{idx} = WebDriverWait(driver, 10).until(")
                print(f"        EC.presence_of_element_located((AppiumBy.ID, '{locator_value}'))")
                print("    )")
            elif locator_type == "accessibility_id":
                print(f"    element_{idx} = WebDriverWait(driver, 10).until(")
                print(
                    f"        EC.presence_of_element_located((AppiumBy.ACCESSIBILITY_ID, '{locator_value}'))"
                )
                print("    )")
            else:
                print(f"    element_{idx} = WebDriverWait(driver, 10).until(")
                print(
                    f'        EC.presence_of_element_located((AppiumBy.XPATH, "{locator_value}"))'
                )
                print("    )")

            print(f"    assert element_{idx}.is_displayed()")
            if candidate["text"] and "error" not in candidate["assertion_type"]:
                print(f"    assert element_{idx}.text == '{candidate['text']}'")
            print()

    def generate_xpath(self, element: Any) -> str | None:
        """Generate a unique XPath for the element"""
        try:
            class_name = element.get_attribute("class") or element.get_attribute("type")
            if not class_name:
                return None

            # Get index among siblings of same type
            parent = element.find_element(AppiumBy.XPATH, "./..")
            siblings = parent.find_elements(AppiumBy.CLASS_NAME, class_name.split(".")[-1])

            index = None
            for i, sibling in enumerate(siblings, 1):
                if sibling == element:
                    index = i
                    break

            if index and len(siblings) > 1:
                return f"//{class_name}[{index}]"
            else:
                return f"//{class_name}"

        except Exception:
            # Fallback to simple class name
            try:
                class_name = element.get_attribute("class") or element.get_attribute("type")
                return f"//{class_name}" if class_name else None
            except Exception:
                return None

    def find_by_text(self, text: str) -> Any | None:
        """Quick helper to find element by text"""
        if self.platform == "android":
            xpath = f"//*[@text='{text}']"
        else:
            xpath = f"//*[@name='{text}' or @label='{text}']"

        try:
            element = self.driver.find_element(AppiumBy.XPATH, xpath)
            print(f"Found element with text: {text}")
            return element
        except Exception:
            print(f"Element not found with text: {text}")
            return None

    def save_screenshot(self, filename: str = "screen_inspection.png") -> bool:
        """Save screenshot for reference"""
        try:
            self.driver.save_screenshot(filename)
            print(f"Screenshot saved: {filename}")
        except Exception as e:
            print(f"Error saving screenshot: {e}")

    def save_assertions_to_file(
        self,
        assertion_candidates: Sequence[dict[str, str]],
        filename: str = "test_assertions.py",
    ) -> bool:
        """Save assertion suggestions to a Python test file"""
        try:
            with open(filename, "w") as f:
                f.write("import pytest\n")
                f.write("from appium.webdriver.common.appiumby import AppiumBy\n")
                f.write("from selenium.webdriver.support.ui import WebDriverWait\n")
                f.write("from selenium.webdriver.support import expected_conditions as EC\n\n\n")

                f.write("class TestScreenValidation:\n")
                f.write('    """Auto-generated test assertions from screen inspection"""\n\n')

                # Generate test method
                f.write("    def test_validate_elements(self, driver):\n")
                f.write('        """Validate all key elements on the screen"""\n')

                for idx, candidate in enumerate(assertion_candidates, 1):
                    locator = self._get_best_locator(candidate)
                    locator_type, locator_value = locator

                    f.write(
                        f"\n        # Validate: {candidate['reason']} - {candidate['text'] or candidate['element_id']}\n"
                    )

                    if locator_type == "id":
                        f.write(f"        element_{idx} = WebDriverWait(driver, 10).until(\n")
                        f.write(
                            f"            EC.presence_of_element_located((AppiumBy.ID, '{locator_value}'))\n"
                        )
                        f.write("        )\n")
                    elif locator_type == "accessibility_id":
                        f.write(f"        element_{idx} = WebDriverWait(driver, 10).until(\n")
                        f.write(
                            f"            EC.presence_of_element_located((AppiumBy.ACCESSIBILITY_ID, '{locator_value}'))\n"
                        )
                        f.write("        )\n")
                    else:
                        f.write(f"        element_{idx} = WebDriverWait(driver, 10).until(\n")
                        f.write(
                            f'            EC.presence_of_element_located((AppiumBy.XPATH, "{locator_value}"))\n'
                        )
                        f.write("        )\n")

                    f.write(
                        f'        assert element_{idx}.is_displayed(), "{candidate["reason"]} should be visible"\n'
                    )
                    if candidate["text"] and "error" not in candidate["assertion_type"]:
                        f.write(
                            f"        assert element_{idx}.text == '{candidate['text']}', \"Text content mismatch\"\n"
                        )

                f.write("\n\n    def test_individual_assertions(self, driver):\n")
                f.write('        """Individual test methods for each validation type"""\n')
                f.write("        # TODO: Split into separate test methods for better granularity\n")
                f.write("        pass\n")

            print(f"✅ Test assertions saved to: {filename}")
            return True
        except Exception as e:
            print(f"Error saving assertions to file: {e}")
            return False


# Usage Example
if __name__ == "__main__":
    # Android capabilities
    android_caps = {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:udid": "ZA222TCMXL",
        "appium:appPackage": "com.example.app",
        "appium:appActivity": ".MainActivity",
        "appium:autoGrantPermissions": True,
        "appium:skipUnlock": True,
    }

    # iOS capabilities
    ios_caps = {
        "platformName": "iOS",
        "automationName": "XCUITest",
        "deviceName": "iPhone 15",
        "platformVersion": "17.0",
        "app": "/path/to/your/app.app",
        # or use bundleId for installed apps
        # "bundleId": "com.example.app"
    }

    try:
        # Connect to Appium server
        driver = webdriver.Remote("http://localhost:4723", android_caps)  # or ios_caps

        # Wait for app to load
        time.sleep(SCREEN_CAPTURE_DELAY_SECONDS)

        # Create inspector and inspect current screen
        finder = QuickElementFinder(driver)

        # Inspect screen with assertion suggestions
        print("Inspecting screen and generating test assertions...")
        finder.inspect_screen(suggest_assertions=True)

        # Save screenshot for reference
        finder.save_screenshot("current_screen.png")

        # The assertion candidates are stored, so we can save them to a file
        # Re-run to capture assertion candidates for saving
        page_source = driver.page_source
        if finder.platform == "ios":
            elements = finder._find_ios_elements()
        else:
            elements = finder._find_android_elements()

        assertion_candidates = []
        for element in elements:
            text = finder._get_element_text(element)
            element_id = finder._get_element_id(element)
            class_name = (
                element.get_attribute("class") or element.get_attribute("type") or "Unknown"
            )
            is_displayed = element.is_displayed() if hasattr(element, "is_displayed") else True
            is_enabled = element.is_enabled() if hasattr(element, "is_enabled") else True

            assertion_info = finder._should_assert_element(
                text, element_id, class_name, is_displayed, is_enabled
            )
            if assertion_info:
                assertion_candidates.append(assertion_info)

        # Save assertions to test file
        if assertion_candidates:
            finder.save_assertions_to_file(assertion_candidates, "test_generated_assertions.py")
            print(f"\n✅ Generated {len(assertion_candidates)} test assertions")

        # Example: Find specific element by text
        # element = finder.find_by_text("Login")
        # if element:
        #     element.click()

        # Keep session open for manual inspection
        input("\nPress Enter to close session...")

        driver.quit()

    except Exception as e:
        print(f"Error: {e}")
