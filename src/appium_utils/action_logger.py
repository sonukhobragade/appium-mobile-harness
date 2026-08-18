"""
Action Logger - Records user actions with element details
Captures "User tapped on X", "User entered Y", "User swiped Z"
"""

from datetime import datetime
from typing import Any


class ActionLogger:
    """Logs user actions in human-readable format with element details"""

    def __init__(self):
        self.actions: list[dict[str, Any]] = []

    def log_tap(self, element_info: dict[str, Any], description: str = None) -> dict[str, Any]:
        """
        Log a tap/click action

        Args:
            element_info: Element dict with id, text, class, etc.
            description: Optional custom description

        Returns:
            Logged action dict

        Example:
            logger.log_tap(
                {"id": "language_english", "text": "English", "type": "TextView"},
                description="Select English language"
            )
            → "User tapped on 'English' button [id=language_english]"
        """
        # Build readable description
        if not description:
            text = (
                element_info.get("text")
                or element_info.get("label")
                or element_info.get("content_desc")
            )
            elem_type = element_info.get("type", "element")

            if text:
                description = f"User tapped on '{text}'"
            else:
                description = f"User tapped on {elem_type}"

        # Add element ID if available
        elem_id = element_info.get("id") or element_info.get("resource-id")
        if elem_id:
            id_short = elem_id.split("/")[-1] if "/" in elem_id else elem_id
            description += f" [id={id_short}]"

        action = {
            "type": "tap",
            "description": description,
            "element": element_info,
            "timestamp": datetime.now().isoformat(),
        }

        self.actions.append(action)
        return action

    def log_input(
        self, element_info: dict[str, Any], text: str, description: str = None
    ) -> dict[str, Any]:
        """
        Log text input action

        Args:
            element_info: Element dict (input field)
            text: Text that was entered
            description: Optional custom description

        Returns:
            Logged action dict

        Example:
            logger.log_input(
                {"id": "phone_input", "type": "EditText"},
                text="5550000000"
            )
            → "User entered '5550000000' into phone number field [id=phone_input]"
        """
        if not description:
            field_name = element_info.get("text") or element_info.get("hint") or "field"
            description = f"User entered '{text}' into {field_name}"

            elem_id = element_info.get("id") or element_info.get("resource-id")
            if elem_id:
                id_short = elem_id.split("/")[-1] if "/" in elem_id else elem_id
                description += f" [id={id_short}]"

        action = {
            "type": "input",
            "description": description,
            "element": element_info,
            "text": text,
            "timestamp": datetime.now().isoformat(),
        }

        self.actions.append(action)
        return action

    def log_swipe(self, direction: str, description: str = None) -> dict[str, Any]:
        """
        Log swipe/scroll action

        Args:
            direction: 'up', 'down', 'left', 'right'
            description: Optional custom description

        Returns:
            Logged action dict

        Example:
            logger.log_swipe('up')
            → "User swiped up"
        """
        if not description:
            description = f"User swiped {direction}"

        action = {
            "type": "swipe",
            "description": description,
            "direction": direction,
            "timestamp": datetime.now().isoformat(),
        }

        self.actions.append(action)
        return action

    def log_back(self, description: str = None) -> dict[str, Any]:
        """
        Log back button press

        Returns:
            Logged action dict
        """
        if not description:
            description = "User pressed back button"

        action = {
            "type": "press_back",
            "description": description,
            "timestamp": datetime.now().isoformat(),
        }

        self.actions.append(action)
        return action

    def log_custom(self, description: str, **kwargs) -> dict[str, Any]:
        """
        Log custom action

        Args:
            description: Action description
            **kwargs: Additional action data

        Returns:
            Logged action dict
        """
        action = {
            "type": "custom",
            "description": description,
            "timestamp": datetime.now().isoformat(),
            **kwargs,
        }

        self.actions.append(action)
        return action

    def get_actions(self) -> list[dict[str, Any]]:
        """Get all logged actions"""
        return self.actions

    def clear(self):
        """Clear all logged actions"""
        self.actions = []

    def get_last_action(self) -> dict[str, Any] | None:
        """Get the most recent action"""
        return self.actions[-1] if self.actions else None

    def generate_code(self, platform: str = "android") -> str:
        """
        Generate test code from logged actions

        Args:
            platform: 'android' or 'ios'

        Returns:
            Generated Python test code
        """
        lines = [
            "# Generated from Action Logger",
            "from appium.webdriver.common.appiumby import AppiumBy",
            "from selenium.webdriver.support.ui import WebDriverWait",
            "from selenium.webdriver.support import expected_conditions as EC",
            "",
            "",
        ]

        for i, action in enumerate(self.actions, 1):
            lines.append(f"# Step {i}: {action['description']}")

            if action["type"] == "tap":
                element = action["element"]
                locator = self._get_locator(element, platform)

                if locator:
                    lines.append("element = WebDriverWait(driver, 10).until(")
                    lines.append(f"    EC.element_to_be_clickable(({locator[0]}, '{locator[1]}'))")
                    lines.append(")")
                    lines.append("element.click()")
                else:
                    lines.append("# TODO: Add locator for element")

            elif action["type"] == "input":
                element = action["element"]
                text = action["text"]
                locator = self._get_locator(element, platform)

                if locator:
                    lines.append("field = WebDriverWait(driver, 10).until(")
                    lines.append(
                        f"    EC.presence_of_element_located(({locator[0]}, '{locator[1]}'))"
                    )
                    lines.append(")")
                    lines.append("field.clear()")
                    lines.append(f"field.send_keys('{text}')")
                else:
                    lines.append(f"# TODO: Enter text: {text}")

            elif action["type"] == "swipe":
                direction = action["direction"]
                lines.append(f"# Swipe {direction}")
                if direction == "up":
                    lines.append("driver.swipe(500, 1500, 500, 500, 800)")
                elif direction == "down":
                    lines.append("driver.swipe(500, 500, 500, 1500, 800)")
                elif direction == "left":
                    lines.append("driver.swipe(800, 1000, 200, 1000, 800)")
                elif direction == "right":
                    lines.append("driver.swipe(200, 1000, 800, 1000, 800)")

            elif action["type"] == "press_back":
                lines.append("driver.press_keycode(4)  # KEYCODE_BACK")

            else:
                lines.append(f"# {action['description']}")

            lines.append("")

        return "\n".join(lines)

    def _get_locator(self, element: dict[str, Any], platform: str) -> tuple | None:
        """Get best locator for element"""
        if platform == "android":
            if element.get("id") or element.get("resource-id"):
                elem_id = element.get("id") or element.get("resource-id")
                return ("AppiumBy.ID", elem_id)

            if element.get("text"):
                text = element["text"].replace("'", "\\'")
                return ("AppiumBy.ANDROID_UIAUTOMATOR", f'new UiSelector().text("{text}")')

            if element.get("content-desc") or element.get("content_desc"):
                desc = element.get("content-desc") or element.get("content_desc")
                return ("AppiumBy.ACCESSIBILITY_ID", desc)

        else:  # iOS
            if element.get("name"):
                return ("AppiumBy.ACCESSIBILITY_ID", element["name"])

            if element.get("label"):
                return ("AppiumBy.ACCESSIBILITY_ID", element["label"])

        return None

    def export_as_dict(self) -> dict[str, Any]:
        """Export actions as dictionary"""
        return {
            "actions": self.actions,
            "count": len(self.actions),
            "timestamp": datetime.now().isoformat(),
        }
