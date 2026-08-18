"""
Mobile Inspector Class
Handles element inspection, hierarchy extraction, and element finding
"""

import json
import xml.etree.ElementTree as ET
from typing import Any

from appium import webdriver
from selenium.webdriver.common.by import By

CLICKABLE_ELEMENT_LIMIT = 30


class MobileInspector:
    """Inspector for mobile app elements"""

    def __init__(self, driver: webdriver.Remote | None = None):
        self.driver = driver
        self.current_hierarchy = None
        self.current_screenshot = None

    def capture_screen(self) -> dict[str, Any]:
        """Capture current screen with screenshot and hierarchy"""
        if not self.driver:
            return {"error": "No active driver session"}

        try:
            # Get screenshot
            screenshot_base64 = self.driver.get_screenshot_as_base64()
            self.current_screenshot = screenshot_base64

            # Get page source (XML hierarchy)
            page_source = self.driver.page_source
            self.current_hierarchy = page_source

            # Parse hierarchy
            elements = self._parse_hierarchy(page_source)

            return {
                "screenshot": screenshot_base64,
                "elements": elements,
                "timestamp": self._get_timestamp(),
            }
        except Exception as e:
            return {"error": str(e)}

    def get_element_tree(self) -> list[dict[str, Any]]:
        """Get element hierarchy as structured tree"""
        if not self.driver:
            return []

        try:
            page_source = self.driver.page_source
            return self._parse_hierarchy(page_source)
        except Exception as e:
            print(f"Error getting element tree: {e}")
            return []

    def find_element(self, search_type: str, search_value: str) -> dict[str, Any] | None:
        """Find element by various strategies"""
        if not self.driver:
            return None

        try:
            element = None

            if search_type == "id":
                element = self.driver.find_element(By.ID, search_value)
            elif search_type == "xpath":
                element = self.driver.find_element(By.XPATH, search_value)
            elif search_type == "class":
                element = self.driver.find_element(By.CLASS_NAME, search_value)
            elif search_type == "text":
                # For Android
                element = self.driver.find_element(
                    By.XPATH, f"//*[@text='{search_value}' or @content-desc='{search_value}']"
                )

            if element:
                return self._element_to_dict(element)

        except Exception as e:
            print(f"Element not found: {e}")

        return None

    def get_element_properties(self, element_id: str) -> dict[str, Any]:
        """Get detailed properties of an element"""
        if not self.driver:
            return {}

        try:
            element = self.driver.find_element(By.ID, element_id)
            return self._element_to_dict(element)
        except Exception as e:
            return {"error": str(e)}

    def highlight_element(self, element_id: str, duration: int = 2):
        """Highlight element on screen (visual feedback)"""
        if not self.driver:
            return False

        try:
            # Execute JavaScript to highlight (for WebView)
            script = f"""
            var element = document.getElementById('{element_id}');
            if (element) {{
                var original = element.style.border;
                element.style.border = '3px solid red';
                setTimeout(function() {{
                    element.style.border = original;
                }}, {duration * 1000});
            }}
            """
            self.driver.execute_script(script)
            return True
        except Exception:
            # For native apps, we can't highlight directly
            # Return element bounds for UI overlay
            try:
                element = self.driver.find_element(By.ID, element_id)
                return {"bounds": element.rect, "location": element.location, "size": element.size}
            except Exception:
                return False

    def get_clickable_elements(self) -> list[dict[str, Any]]:
        """Get all clickable elements on current screen"""
        if not self.driver:
            return []

        clickables = []

        try:
            # Android
            elements = self.driver.find_elements(By.XPATH, "//*[@clickable='true']")

            for elem in elements[:CLICKABLE_ELEMENT_LIMIT]:
                try:
                    clickables.append(self._element_to_dict(elem))
                except Exception:
                    continue

        except Exception as e:
            print(f"Error getting clickable elements: {e}")

        return clickables

    def get_input_fields(self) -> list[dict[str, Any]]:
        """Get all input fields on current screen"""
        if not self.driver:
            return []

        inputs = []

        try:
            # Android EditText fields
            elements = self.driver.find_elements(By.CLASS_NAME, "android.widget.EditText")

            # iOS TextField
            if not elements:
                elements = self.driver.find_elements(By.CLASS_NAME, "XCUIElementTypeTextField")

            for elem in elements:
                try:
                    inputs.append(self._element_to_dict(elem))
                except Exception:
                    continue

        except Exception as e:
            print(f"Error getting input fields: {e}")

        return inputs

    def generate_xpath(self, element_dict: dict[str, Any]) -> str:
        """Generate XPath for element based on its properties"""
        xpath_parts = []

        # Start with class/type
        if element_dict.get("class"):
            xpath_parts.append(f"//{element_dict['class']}")
        else:
            xpath_parts.append("//*")

        # Add conditions
        conditions = []

        if element_dict.get("resource-id"):
            conditions.append(f"@resource-id='{element_dict['resource-id']}'")

        if element_dict.get("text"):
            conditions.append(f"@text='{element_dict['text']}'")

        if element_dict.get("content-desc"):
            conditions.append(f"@content-desc='{element_dict['content-desc']}'")

        if conditions:
            xpath = xpath_parts[0] + "[" + " and ".join(conditions) + "]"
        else:
            xpath = xpath_parts[0]

        return xpath

    def _parse_hierarchy(self, xml_source: str) -> list[dict[str, Any]]:
        """Parse XML hierarchy into structured elements list"""
        elements = []

        try:
            root = ET.fromstring(xml_source)
            self._extract_elements(root, elements, "")
        except Exception as e:
            print(f"Error parsing hierarchy: {e}")

        return elements

    def _extract_elements(
        self,
        node: ET.Element,
        elements: list[dict[str, Any]],
        xpath_prefix: str,
    ) -> None:
        """Recursively extract elements from XML tree"""
        # Build current element's xpath
        tag = node.tag.split("}")[-1] if "}" in node.tag else node.tag

        # Create element dict
        elem_dict = {
            "type": tag,
            "class": node.get("class", ""),
            "resource-id": node.get("resource-id", ""),
            "text": node.get("text", ""),
            "content-desc": node.get("content-desc", ""),
            "clickable": node.get("clickable", "false") == "true",
            "enabled": node.get("enabled", "false") == "true",
            "focusable": node.get("focusable", "false") == "true",
            "scrollable": node.get("scrollable", "false") == "true",
            "bounds": node.get("bounds", ""),
            "index": node.get("index", "0"),
        }

        # Generate XPath
        if elem_dict["resource-id"]:
            elem_dict["xpath"] = f"//*[@resource-id='{elem_dict['resource-id']}']"
        elif elem_dict["text"]:
            elem_dict["xpath"] = f"//{tag}[@text='{elem_dict['text']}']"
        else:
            elem_dict["xpath"] = f"{xpath_prefix}/{tag}[{elem_dict['index']}]"

        # Add to list if it's interesting (has ID, text, or is clickable)
        if elem_dict["resource-id"] or elem_dict["text"] or elem_dict["clickable"]:
            elements.append(elem_dict)

        # Process children
        for child in node:
            self._extract_elements(child, elements, elem_dict["xpath"])

    def _element_to_dict(self, element) -> dict[str, Any]:
        """Convert WebElement to dictionary"""
        return {
            "tag_name": element.tag_name,
            "text": element.text,
            "location": element.location,
            "size": element.size,
            "rect": element.rect,
            "is_displayed": element.is_displayed(),
            "is_enabled": element.is_enabled(),
            "resource_id": element.get_attribute("resource-id"),
            "content_desc": element.get_attribute("content-desc"),
            "class": element.get_attribute("class"),
            "clickable": element.get_attribute("clickable") == "true",
        }

    def _get_timestamp(self) -> str:
        """Get current timestamp"""
        from datetime import datetime

        return datetime.now().isoformat()

    def export_hierarchy(self, format: str = "json") -> str:
        """Export current hierarchy in specified format"""
        if not self.current_hierarchy:
            return ""

        elements = self._parse_hierarchy(self.current_hierarchy)

        if format == "json":
            return json.dumps(elements, indent=2)
        elif format == "csv":
            import csv
            import io

            output = io.StringIO()
            if elements:
                writer = csv.DictWriter(output, fieldnames=elements[0].keys())
                writer.writeheader()
                writer.writerows(elements)

            return output.getvalue()
        else:
            return self.current_hierarchy  # Return raw XML
