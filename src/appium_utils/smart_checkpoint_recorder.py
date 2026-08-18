"""
Smart Checkpoint Recorder - Captures screen states and detects changes
"""

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

from .utils import detect_platform

SIGNIFICANT_DISAPPEAR_COUNT = 3
SIGNIFICANT_APPEAR_COUNT = 5

# Android KeyCodes for hardware buttons
KEYCODE_BACK = 4
KEYCODE_HOME = 3
KEYCODE_APP_SWITCH = 187  # Recent Apps


class SmartCheckpointRecorder:
    """Records screen checkpoints and detects changes between them."""

    def __init__(self, driver) -> None:
        self.driver = driver
        self.checkpoints: list[dict[str, Any]] = []
        self.platform = detect_platform(driver)
        self.recording = False

    def start_recording(self) -> bool:
        """Start a new recording session"""
        self.recording = True
        self.checkpoints = []
        return True

    def stop_recording(self) -> list[dict[str, Any]]:
        """Stop recording and return checkpoints"""
        self.recording = False
        return self.checkpoints

    def add_manual_action(self, action_type: str, **kwargs) -> dict[str, Any]:
        """Manually record an action that was performed

        Use this BEFORE capturing checkpoint to record what you just did.

        Args:
            action_type: Type of action ('click', 'send_keys', 'swipe', 'press_back', etc.)
            **kwargs: Additional action details (element_id, text, coordinates, etc.)

        Returns:
            Action dict that was recorded

        Example:
            recorder.add_manual_action('click', element_id='login_button')
            recorder.capture_checkpoint('After clicking login')
        """
        action = {"type": action_type, "timestamp": datetime.now().isoformat(), **kwargs}

        # Store pending action (will be attached to next checkpoint)
        if not hasattr(self, "_pending_action"):
            self._pending_action = None

        self._pending_action = action
        return action

    def capture_checkpoint(
        self, checkpoint_name: str = None, manual_action: str = None
    ) -> dict[str, Any]:
        """Capture current screen state as a checkpoint

        Args:
            checkpoint_name: Optional name for this checkpoint
            manual_action: Optional manual description of action that was just performed

        Returns:
            dict with checkpoint data including detected changes
        """
        try:
            # Capture screenshot first
            screenshot_base64 = self.driver.get_screenshot_as_base64()

            # Get page source
            page_source = self.driver.page_source

            # Parse XML
            root = ET.fromstring(page_source)

            # Extract elements
            elements = self._parse_elements_from_xml(root)

            # Detect changes from previous checkpoint
            changes = None
            inferred_action = None
            manual_action_data = None

            # Check if there's a pending manual action
            if hasattr(self, "_pending_action") and self._pending_action:
                manual_action_data = self._pending_action
                self._pending_action = None  # Clear it after using

            # Also check if manual_action string was provided
            if manual_action:
                manual_action_data = {"type": "manual", "description": manual_action}

            if len(self.checkpoints) > 0:
                previous = self.checkpoints[-1]
                changes = self._detect_changes(previous["elements"], elements)
                inferred_action = self._infer_action(changes)

            # Create checkpoint
            checkpoint = {
                "index": len(self.checkpoints) + 1,
                "name": checkpoint_name or f"Checkpoint {len(self.checkpoints) + 1}",
                "timestamp": datetime.now().isoformat(),
                "platform": self.platform,
                "elements": elements,
                "element_count": len(elements),
                "changes": changes,
                "inferred_action": inferred_action,
                "manual_action": manual_action_data,  # Store manual action if provided
                "page_source_hash": hash(page_source),  # To detect if screen changed
                "screenshot": screenshot_base64,  # Store screenshot as base64
            }

            self.checkpoints.append(checkpoint)
            return checkpoint

        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def _parse_elements_from_xml(self, root: ET.Element) -> list[dict[str, str]]:
        """Parse interactive elements from XML"""
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
            attribs = elem.attrib

            # Get identifying info
            resource_id = attribs.get("resource-id", "")
            text = attribs.get("text", "")
            content_desc = attribs.get("content-desc", "")
            class_name = attribs.get("class", "")
            clickable = attribs.get("clickable", "false") == "true"
            displayed = attribs.get("displayed", "true") == "true"

            # Only include visible interactive elements with identifiers
            if displayed and (resource_id or text or content_desc):
                element_info = {
                    "type": class_name.split(".")[-1] if class_name else "Unknown",
                    "id": resource_id,
                    "text": text,
                    "content_desc": content_desc,
                    "clickable": clickable,
                    "bounds": attribs.get("bounds", ""),
                    "element_key": resource_id or text or content_desc,  # Unique key
                }
                elements.append(element_info)

        return elements

    def _parse_ios_xml(self, root: ET.Element) -> list[dict[str, str]]:
        """Parse iOS XML hierarchy"""
        elements = []

        for elem in root.iter():
            attribs = elem.attrib
            elem_type = attribs.get("type", "")
            name = attribs.get("name", "")
            label = attribs.get("label", "")
            value = attribs.get("value", "")
            visible = attribs.get("visible", "true") == "true"

            if visible and (name or label):
                element_info = {
                    "type": elem_type.replace("XCUIElementType", ""),
                    "name": name,
                    "label": label,
                    "value": value,
                    "element_key": name or label,
                }
                elements.append(element_info)

        return elements

    def _detect_changes(
        self,
        old_elements: list[dict[str, Any]],
        new_elements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Detect what changed between two screen states"""

        # Create sets of element keys for comparison
        old_keys = {e["element_key"] for e in old_elements if e["element_key"]}
        new_keys = {e["element_key"] for e in new_elements if e["element_key"]}

        disappeared = old_keys - new_keys
        appeared = new_keys - old_keys

        # Find elements with changed text
        common_keys = old_keys & new_keys
        text_changes = []

        old_map = {e["element_key"]: e for e in old_elements if e["element_key"]}
        new_map = {e["element_key"]: e for e in new_elements if e["element_key"]}

        for key in common_keys:
            old_text = old_map[key].get("text", "") or old_map[key].get("value", "")
            new_text = new_map[key].get("text", "") or new_map[key].get("value", "")

            if old_text != new_text:
                text_changes.append({"element": key, "old_text": old_text, "new_text": new_text})

        return {
            "disappeared": list(disappeared),
            "appeared": list(appeared),
            "text_changes": text_changes,
            "significant": (
                len(disappeared) > SIGNIFICANT_DISAPPEAR_COUNT
                or len(appeared) > SIGNIFICANT_APPEAR_COUNT
            ),
        }

    def _infer_action(self, changes: dict[str, Any]) -> str:
        """Infer what action likely caused these changes"""

        if not changes or not changes.get("significant"):
            return "Minor change (data update or animation)"

        disappeared = changes.get("disappeared", [])
        appeared = changes.get("appeared", [])
        text_changes = changes.get("text_changes", [])

        # Heuristics to infer action type
        if len(appeared) > len(disappeared):
            return f"Navigation to new screen (Appeared: {len(appeared)} elements)"
        elif len(disappeared) > len(appeared):
            # High ratio of disappeared elements suggests Back button
            if len(disappeared) > 10 and len(appeared) < 5:
                return "Android Back button pressed"
            return f"Navigation back or dialog closed (Disappeared: {len(disappeared)} elements)"
        elif text_changes:
            return f"Text input or data update ({len(text_changes)} fields changed)"
        else:
            return "Screen transition detected"

    def generate_test_from_checkpoints(self) -> str:
        """Generate pytest test from recorded checkpoints - IMPROVED VERSION"""

        if not self.checkpoints:
            return "# No checkpoints recorded"

        # Use the improved code generator
        from .improved_code_generator import ImprovedCodeGenerator

        generator = ImprovedCodeGenerator(platform=self.platform)
        return generator.generate_pytest(self.checkpoints)

    def clear_checkpoints(self):
        """Clear all recorded checkpoints"""
        self.checkpoints = []

    def get_checkpoints_summary(self) -> list[dict[str, Any]]:
        """Get summary of all checkpoints"""
        return [
            {
                "index": cp["index"],
                "name": cp["name"],
                "timestamp": cp["timestamp"],
                "element_count": cp["element_count"],
                "inferred_action": cp.get("inferred_action", "Initial state"),
            }
            for cp in self.checkpoints
        ]
