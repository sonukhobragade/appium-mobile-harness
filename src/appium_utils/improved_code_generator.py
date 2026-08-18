"""
Improved Code Generator for Smart Checkpoint Recorder
Generates ACTUAL executable test code, not TODO skeletons
"""

from typing import Any


class ImprovedCodeGenerator:
    """Generates better, more complete test code from checkpoints"""

    def __init__(self, platform: str = "android"):
        self.platform = platform

    def generate_pytest(self, checkpoints: list[dict[str, Any]]) -> str:
        """Generate complete pytest code from checkpoints

        Args:
            checkpoints: List of checkpoint dictionaries

        Returns:
            Complete pytest code as string
        """
        if not checkpoints:
            return "# No checkpoints recorded"

        lines = self._generate_imports()
        lines.extend(self._generate_test_function(checkpoints))
        lines.extend(self._generate_helper_functions())

        return "\n".join(lines)

    def _generate_imports(self) -> list[str]:
        """Generate import statements"""
        return [
            "import pytest",
            "import time",
            "from appium.webdriver.common.appiumby import AppiumBy",
            "from selenium.webdriver.support.ui import WebDriverWait",
            "from selenium.webdriver.support import expected_conditions as EC",
            "",
            "",
        ]

    def _generate_test_function(self, checkpoints: list[dict[str, Any]]) -> list[str]:
        """Generate main test function"""
        lines = [
            "def test_recorded_flow(driver):",
            '    """',
            f"    Auto-generated test from {len(checkpoints)} checkpoints",
            f"    Platform: {self.platform}",
            f"    Generated: {checkpoints[0]['timestamp'][:19]}",
            '    """',
            "",
        ]

        for i, checkpoint in enumerate(checkpoints):
            lines.extend(self._generate_checkpoint_code(i, checkpoint, checkpoints))

        return lines

    def _generate_checkpoint_code(
        self, index: int, checkpoint: dict[str, Any], all_checkpoints: list[dict[str, Any]]
    ) -> list[str]:
        """Generate code for a single checkpoint"""
        lines = []

        # Header comment
        lines.append("    # " + "=" * 60)
        lines.append(f"    # CHECKPOINT {checkpoint['index']}: {checkpoint['name']}")
        lines.append("    # " + "=" * 60)

        # Add inferred action if this isn't the first checkpoint
        if index > 0:
            inferred = checkpoint.get("inferred_action", "")
            if inferred:
                lines.append(f"    # Detected: {inferred}")

            # Generate action code based on inference
            action_lines = self._generate_action_code(checkpoint, all_checkpoints[index - 1])
            lines.extend(action_lines)
            lines.append("")

        # Generate validation code
        validation_lines = self._generate_validation_code(checkpoint)
        lines.extend(validation_lines)
        lines.append("")

        return lines

    def _generate_action_code(self, current: dict[str, Any], previous: dict[str, Any]) -> list[str]:
        """Generate action code based on detected changes"""
        lines = []

        # PRIORITY: Use manual action if provided
        manual_action = current.get("manual_action")
        if manual_action:
            return self._generate_manual_action_code(manual_action, current)

        # FALLBACK: Use inferred action
        inferred = current.get("inferred_action", "")
        changes = current.get("changes", {})

        # Back button detection
        if "Back button" in inferred or "back" in inferred.lower():
            lines.append("    # Press back button")
            lines.append("    driver.press_keycode(4)  # KEYCODE_BACK")
            lines.append("    time.sleep(1)")
            return lines

        # Text input detection
        if "Text input" in inferred or changes.get("text_changes"):
            text_changes = changes.get("text_changes", [])
            if text_changes:
                lines.append("    # Text was entered")
                for tc in text_changes[:2]:  # Show first 2 text changes
                    element_key = tc["element"]
                    new_text = tc["new_text"]

                    # Try to find this element
                    element = self._find_element_by_key(element_key, current["elements"])
                    if element:
                        locator = self._get_best_locator(element)
                        if locator:
                            lines.append(f"    # Enter: {new_text}")
                            lines.append(
                                f"    field = wait_for_element(driver, {locator[0]}, '{locator[1]}')"
                            )
                            lines.append("    field.clear()")
                            lines.append(f"    field.send_keys('{new_text}')")
                        else:
                            lines.append(f"    # TODO: Enter '{new_text}' into field")
            else:
                lines.append("    # TODO: Enter text (element not identified)")
            return lines

        # Navigation forward (new screen appeared)
        if "Navigation to new screen" in inferred:
            # Try to infer which element was clicked
            _appeared = changes.get("appeared", [])
            _disappeared = changes.get("disappeared", [])

            # Look for clickable elements in previous screen
            clickable_elements = [
                e
                for e in previous["elements"]
                if e.get("clickable") and (e.get("text") or e.get("id"))
            ]

            if clickable_elements:
                # Use the first clickable element as likely candidate
                elem = clickable_elements[0]
                locator = self._get_best_locator(elem)

                if locator:
                    text_hint = elem.get("text") or elem.get("id", "")[:30]
                    lines.append(f"    # Click: {text_hint}")
                    lines.append(
                        f"    button = wait_for_element(driver, {locator[0]}, '{locator[1]}')"
                    )
                    lines.append("    button.click()")
                    lines.append("    time.sleep(1)")
                else:
                    lines.append("    # TODO: Click button to navigate (check screenshot)")
            else:
                lines.append("    # TODO: Click element to navigate forward")
            return lines

        # Swipe/scroll detection
        if "scroll" in inferred.lower() or "swipe" in inferred.lower():
            lines.append("    # Swipe gesture")
            lines.append("    # TODO: Add swipe coordinates based on screen size")
            lines.append("    # driver.swipe(start_x, start_y, end_x, end_y, duration=800)")
            return lines

        # Default: some interaction happened but can't infer what
        lines.append("    # Screen changed (see screenshot for details)")
        lines.append(f"    # TODO: Add the action that caused: {inferred}")

        return lines

    def _generate_manual_action_code(
        self, manual_action: dict[str, Any], checkpoint: dict[str, Any]
    ) -> list[str]:
        """Generate code from manually recorded action"""
        lines = []
        action_type = manual_action.get("type", "unknown")

        if action_type == "click":
            element_id = manual_action.get("element_id")
            element_text = manual_action.get("element_text")
            desc = manual_action.get("description", "button")

            lines.append(f"    # Click: {desc}")

            if element_id:
                lines.append(f"    button = wait_for_element(driver, AppiumBy.ID, '{element_id}')")
                lines.append("    button.click()")
            elif element_text:
                if self.platform == "android":
                    uiautomator = f'new UiSelector().text("{element_text}")'
                    lines.append(
                        f"    button = wait_for_element(driver, AppiumBy.ANDROID_UIAUTOMATOR, '{uiautomator}')"
                    )
                else:
                    lines.append(
                        f"    button = wait_for_element(driver, AppiumBy.ACCESSIBILITY_ID, '{element_text}')"
                    )
                lines.append("    button.click()")
            else:
                lines.append(f"    # TODO: Add element locator for {desc}")

        elif action_type == "send_keys":
            element_id = manual_action.get("element_id")
            text = manual_action.get("text", "")

            lines.append(f"    # Enter text: {text}")

            if element_id:
                lines.append(f"    field = wait_for_element(driver, AppiumBy.ID, '{element_id}')")
                lines.append("    field.clear()")
                lines.append(f"    field.send_keys('{text}')")
            else:
                lines.append("    # TODO: Add element locator for input field")
                lines.append(f"    # field.send_keys('{text}')")

        elif action_type == "press_back":
            lines.append("    # Press back button")
            lines.append("    driver.press_keycode(4)  # KEYCODE_BACK")

        elif action_type == "swipe":
            start_x = manual_action.get("start_x", 500)
            start_y = manual_action.get("start_y", 1000)
            end_x = manual_action.get("end_x", 500)
            end_y = manual_action.get("end_y", 200)
            duration = manual_action.get("duration", 800)

            lines.append("    # Swipe gesture")
            lines.append(f"    driver.swipe({start_x}, {start_y}, {end_x}, {end_y}, {duration})")

        elif action_type == "manual":
            # Just a description provided
            desc = manual_action.get("description", "action")
            lines.append(f"    # {desc}")
            lines.append(f"    # TODO: Implement action: {desc}")

        else:
            lines.append(f"    # Action: {action_type}")
            lines.append(f"    # TODO: Implement {action_type}")

        lines.append("    time.sleep(1)")
        return lines

    def _generate_validation_code(self, checkpoint: dict[str, Any]) -> list[str]:
        """Generate validation assertions for checkpoint"""
        lines = []
        elements = checkpoint.get("elements", [])

        # Filter for good validation candidates
        good_elements = self._get_validation_elements(elements)

        if not good_elements:
            lines.append("    # No identifiable elements to validate")
            return lines

        lines.append("    # Validate screen elements")

        # Generate assertions for top elements
        for elem in good_elements[:5]:  # Top 5 validation points
            locator = self._get_best_locator(elem)

            if not locator:
                continue

            # Create readable description
            desc = self._get_element_description(elem)

            lines.append(f"    # Verify: {desc}")

            if elem.get("text"):
                # Assert element with text exists
                lines.append(f"    elem = wait_for_element(driver, {locator[0]}, '{locator[1]}')")
                lines.append(f"    assert elem.is_displayed(), '{desc} not visible'")
                if elem["text"].strip():
                    lines.append(f"    assert '{elem['text']}' in elem.text, 'Text mismatch'")
            else:
                # Just verify element exists
                lines.append(
                    f"    assert wait_for_element(driver, {locator[0]}, '{locator[1]}').is_displayed()"
                )

            lines.append("")

        return lines

    def _get_validation_elements(self, elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Select best elements for validation

        Priority:
        1. Elements with resource-id/name (most stable)
        2. Elements with text content (readable)
        3. Clickable elements (important UI)
        """
        scored = []

        for elem in elements:
            score = 0

            # ID is most stable identifier
            if elem.get("id"):
                score += 10

            # Text makes test readable
            if elem.get("text") and elem.get("text").strip():
                score += 5

            # Labels/names are good too
            if elem.get("label") or elem.get("name"):
                score += 5

            # Clickable elements are usually important
            if elem.get("clickable"):
                score += 3

            # Button types are important
            if "button" in elem.get("type", "").lower():
                score += 2

            if score > 0:
                scored.append((score, elem))

        # Sort by score, return top elements
        scored.sort(reverse=True, key=lambda x: x[0])
        return [elem for score, elem in scored[:8]]

    def _get_best_locator(self, element: dict[str, Any]) -> tuple:
        """Get the most reliable locator for an element

        Returns:
            Tuple of (locator_type, locator_value) or None
        """
        # Priority order for Android
        if self.platform == "android":
            if element.get("id"):
                return ("AppiumBy.ID", element["id"])

            if element.get("text") and element.get("text").strip():
                # Use Android UIAutomator for text
                text = element["text"].replace("'", "\\'")
                return ("AppiumBy.ANDROID_UIAUTOMATOR", f'new UiSelector().text("{text}")')

            if element.get("content_desc"):
                desc = element["content_desc"].replace("'", "\\'")
                return ("AppiumBy.ACCESSIBILITY_ID", desc)

        # Priority order for iOS
        else:
            if element.get("name"):
                return ("AppiumBy.ACCESSIBILITY_ID", element["name"])

            if element.get("label"):
                return ("AppiumBy.ACCESSIBILITY_ID", element["label"])

            if element.get("id"):
                return ("AppiumBy.ID", element["id"])

        return None

    def _get_element_description(self, element: dict[str, Any]) -> str:
        """Get human-readable element description"""
        elem_type = element.get("type", "Element")

        if element.get("text"):
            return f"{elem_type}: '{element['text'][:40]}'"

        if element.get("label"):
            return f"{elem_type}: '{element['label'][:40]}'"

        if element.get("name"):
            return f"{elem_type}: '{element['name'][:40]}'"

        if element.get("id"):
            id_short = element["id"].split("/")[-1][:30]
            return f"{elem_type} ({id_short})"

        return elem_type

    def _find_element_by_key(self, key: str, elements: list[dict[str, Any]]) -> dict[str, Any]:
        """Find element by its element_key"""
        for elem in elements:
            if elem.get("element_key") == key:
                return elem
        return None

    def _generate_helper_functions(self) -> list[str]:
        """Generate helper functions used in the test"""
        return [
            "",
            "",
            "def wait_for_element(driver, by, value, timeout=10):",
            '    """Wait for element to be present and return it"""',
            "    return WebDriverWait(driver, timeout).until(",
            "        EC.presence_of_element_located((by, value))",
            "    )",
            "",
        ]
