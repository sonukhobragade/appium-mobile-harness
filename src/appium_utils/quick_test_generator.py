"""
Quick Test Generator - Fast test generation from page hierarchy
Uses only page_source (XML) - no slow element queries
"""

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

from .utils import detect_platform


class QuickTestGenerator:
    """Generate tests from page hierarchy without slow element queries."""

    def __init__(self, driver) -> None:
        self.driver = driver
        self.platform = detect_platform(driver)

    def generate_test(self, screen_name: str = "screen") -> dict[str, Any]:
        """Generate test from current screen hierarchy

        Args:
            screen_name: Name for the test (e.g., "login", "home")

        Returns:
            dict with test_code, elements_found, and metadata
        """
        try:
            # Get page source (FAST - single call)
            page_source = self.driver.page_source

            # Parse XML
            root = ET.fromstring(page_source)

            # Find interactive elements from XML
            elements = self._parse_elements_from_xml(root)

            # Generate pytest code
            test_code = self._generate_pytest_code(elements, screen_name)

            return {
                "success": True,
                "test_code": test_code,
                "elements_found": len(elements),
                "elements": elements,
                "screen_name": screen_name,
                "platform": self.platform,
                "generated_at": datetime.now().isoformat(),
            }

        except Exception as e:
            return {"success": False, "error": str(e), "elements_found": 0}

    def _parse_elements_from_xml(self, root: ET.Element) -> list[dict[str, str]]:
        """Parse interactive elements directly from XML"""
        elements = []

        if self.platform == "android":
            elements = self._parse_android_xml(root)
        else:
            elements = self._parse_ios_xml(root)

        return elements

    def _parse_android_xml(self, root: ET.Element) -> list[dict[str, str]]:
        """Parse Android XML hierarchy"""
        elements = []

        for elem in root.iter():
            # Get attributes
            attribs = elem.attrib

            # Check if interactive
            clickable = attribs.get("clickable", "false") == "true"
            focusable = attribs.get("focusable", "false") == "true"
            enabled = attribs.get("enabled", "false") == "true"

            # Get identifying info
            resource_id = attribs.get("resource-id", "")
            text = attribs.get("text", "")
            content_desc = attribs.get("content-desc", "")
            class_name = attribs.get("class", "")

            # Only include interactive elements with identifiers
            if (clickable or focusable) and enabled:
                if resource_id or text or content_desc:
                    element_info = {
                        "type": class_name.split(".")[-1] if class_name else "Unknown",
                        "id": resource_id,
                        "text": text,
                        "content_desc": content_desc,
                        "clickable": clickable,
                        "bounds": attribs.get("bounds", ""),
                    }
                    elements.append(element_info)

        return elements

    def _parse_ios_xml(self, root: ET.Element) -> list[dict[str, str]]:
        """Parse iOS XML hierarchy"""
        elements = []

        for elem in root.iter():
            attribs = elem.attrib

            # Get element type
            elem_type = attribs.get("type", "")

            # Interactive types
            interactive_types = [
                "XCUIElementTypeButton",
                "XCUIElementTypeTextField",
                "XCUIElementTypeSecureTextField",
                "XCUIElementTypeLink",
                "XCUIElementTypeCell",
                "XCUIElementTypeSwitch",
            ]

            if elem_type in interactive_types:
                name = attribs.get("name", "")
                label = attribs.get("label", "")
                value = attribs.get("value", "")

                if name or label:
                    element_info = {
                        "type": elem_type.replace("XCUIElementType", ""),
                        "name": name,
                        "label": label,
                        "value": value,
                        "enabled": attribs.get("enabled", "true"),
                    }
                    elements.append(element_info)

        return elements

    def _generate_pytest_code(self, elements: list[dict[str, str]], screen_name: str) -> str:
        """Generate pytest test code from elements"""

        # Generate test method name
        test_name = f"test_{screen_name}_elements"

        # Start building test code
        code_lines = [
            "import pytest",
            "from appium.webdriver.common.appiumby import AppiumBy",
            "from selenium.webdriver.support.ui import WebDriverWait",
            "from selenium.webdriver.support import expected_conditions as EC",
            "",
            "",
            f"def {test_name}(driver):",
            f'    """Test to validate {screen_name} screen elements"""',
            "",
        ]

        # Generate assertions for each element
        for idx, elem in enumerate(elements[:10], 1):  # Limit to first 10 elements
            if self.platform == "android":
                code_lines.extend(self._generate_android_assertion(elem, idx))
            else:
                code_lines.extend(self._generate_ios_assertion(elem, idx))
            code_lines.append("")

        return "\n".join(code_lines)

    def _generate_android_assertion(self, elem: dict[str, str], idx: int) -> list[str]:
        """Generate assertion code for Android element"""
        lines = []
        lines.append(f"    # Validate: {elem['type']} - {elem['text'] or elem['id'] or 'Element'}")

        # Determine best locator strategy
        if elem["id"]:
            locator_type = "ID"
            locator_value = elem["id"]
        elif elem["text"]:
            locator_type = "XPATH"
            locator_value = f"//*[@text='{elem['text']}']"
        elif elem["content_desc"]:
            locator_type = "ACCESSIBILITY_ID"
            locator_value = elem["content_desc"]
        else:
            return lines

        lines.append(f"    element_{idx} = WebDriverWait(driver, 10).until(")

        if locator_type == "XPATH":
            lines.append(
                f'        EC.presence_of_element_located((AppiumBy.XPATH, "{locator_value}"))'
            )
        else:
            lines.append(
                f"        EC.presence_of_element_located((AppiumBy.{locator_type}, '{locator_value}'))"
            )

        lines.append("    )")
        lines.append(f"    assert element_{idx}.is_displayed(), '{elem['type']} should be visible'")

        if elem["text"]:
            lines.append(f"    assert element_{idx}.text == '{elem['text']}', 'Text mismatch'")

        return lines

    def _generate_ios_assertion(self, elem: dict[str, str], idx: int) -> list[str]:
        """Generate assertion code for iOS element"""
        lines = []
        lines.append(f"    # Validate: {elem['type']} - {elem['label'] or elem['name']}")

        if elem["name"]:
            locator_type = "ACCESSIBILITY_ID"
            locator_value = elem["name"]
        elif elem["label"]:
            locator_type = "XPATH"
            locator_value = f"//*[@label='{elem['label']}']"
        else:
            return lines

        lines.append(f"    element_{idx} = WebDriverWait(driver, 10).until(")

        if locator_type == "XPATH":
            lines.append(
                f'        EC.presence_of_element_located((AppiumBy.XPATH, "{locator_value}"))'
            )
        else:
            lines.append(
                f"        EC.presence_of_element_located((AppiumBy.ACCESSIBILITY_ID, '{locator_value}'))"
            )

        lines.append("    )")
        lines.append(f"    assert element_{idx}.is_displayed(), '{elem['type']} should be visible'")

        return lines

    def save_test_to_file(self, test_code: str, filename: str | None = None) -> str:
        """Save generated test code to file"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"test_generated_{timestamp}.py"

        with open(filename, "w") as f:
            f.write(test_code)

        return filename
