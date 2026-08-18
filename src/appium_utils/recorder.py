"""
Action Recorder Class
Records user interactions and converts them to test scripts
"""

import threading
import time
from datetime import datetime
from typing import Any

from appium import webdriver
from selenium.webdriver.common.by import By

MONITOR_INTERVAL_SECONDS = 0.5
MONITOR_ERROR_BACKOFF_SECONDS = 1.0
REPLAY_DELAY_SECONDS = 1.0


class ActionRecorder:
    """Records user actions on mobile app"""

    def __init__(self, driver: webdriver.Remote | None = None):
        self.driver = driver
        self.recording = False
        self.actions = []
        self.recording_thread = None
        self.last_screenshot = None
        self.recording_start_time = None

    def start_recording(self) -> bool:
        """Start recording user actions"""
        if self.recording:
            return False

        self.recording = True
        self.actions = []
        self.recording_start_time = datetime.now()

        # Start monitoring thread
        self.recording_thread = threading.Thread(target=self._monitor_actions)
        self.recording_thread.daemon = True
        self.recording_thread.start()

        return True

    def stop_recording(self) -> list[dict[str, Any]]:
        """Stop recording and return recorded actions"""
        self.recording = False

        if self.recording_thread:
            self.recording_thread.join(timeout=1)

        return self.actions

    def pause_recording(self):
        """Pause recording temporarily"""
        self.recording = False

    def resume_recording(self):
        """Resume recording"""
        if not self.recording:
            self.recording = True
            self.recording_thread = threading.Thread(target=self._monitor_actions)
            self.recording_thread.daemon = True
            self.recording_thread.start()

    def add_action(self, action_type: str, **kwargs) -> dict[str, Any]:
        """Manually add an action to recording"""
        action = {
            "type": action_type,
            "timestamp": datetime.now().isoformat(),
            "elapsed_time": self._get_elapsed_time(),
            **kwargs,
        }

        self.actions.append(action)
        return action

    def clear_actions(self):
        """Clear all recorded actions"""
        self.actions = []

    def get_actions(self) -> list[dict[str, Any]]:
        """Get list of recorded actions"""
        return self.actions

    def convert_to_pytest(self) -> str:
        """Convert recorded actions to pytest script"""
        script_lines = [
            "import pytest",
            "from appium import webdriver",
            "from selenium.webdriver.common.by import By",
            "from selenium.webdriver.support.ui import WebDriverWait",
            "from selenium.webdriver.support import expected_conditions as EC",
            "import time",
            "",
            "",
            "class TestRecordedSession:",
            "    ",
            "    def test_recorded_actions(self, driver):",
            '        """Automatically recorded test"""',
        ]

        for i, action in enumerate(self.actions):
            script_lines.append(f"        # Action {i + 1}: {action['type']}")
            script_lines.append(self._action_to_pytest(action))
            script_lines.append("")

        return "\n".join(script_lines)

    def convert_to_maestro(self) -> str:
        """Convert recorded actions to Maestro YAML format"""
        yaml_lines = ["appId: com.example", "---"]

        for action in self.actions:
            yaml_lines.append(self._action_to_maestro(action))

        return "\n".join(yaml_lines)

    def convert_to_javascript(self) -> str:
        """Convert recorded actions to JavaScript/WebDriverIO"""
        script_lines = [
            "describe('Recorded Test Session', () => {",
            "    it('should execute recorded actions', async () => {",
        ]

        for i, action in enumerate(self.actions):
            script_lines.append(f"        // Action {i + 1}: {action['type']}")
            script_lines.append(self._action_to_javascript(action))
            script_lines.append("")

        script_lines.append("    });")
        script_lines.append("});")

        return "\n".join(script_lines)

    def save_recording(self, filepath: str, format: str = "json"):
        """Save recording to file"""
        import json

        data = {
            "metadata": {
                "start_time": self.recording_start_time.isoformat()
                if self.recording_start_time
                else None,
                "total_actions": len(self.actions),
                "duration": self._get_elapsed_time(),
            },
            "actions": self.actions,
        }

        if format == "json":
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
        elif format == "pytest":
            with open(filepath, "w") as f:
                f.write(self.convert_to_pytest())
        elif format == "maestro":
            with open(filepath, "w") as f:
                f.write(self.convert_to_maestro())

        return filepath

    def load_recording(self, filepath: str) -> bool:
        """Load recording from file"""
        import json

        try:
            with open(filepath) as f:
                data = json.load(f)
                self.actions = data.get("actions", [])
                return True
        except Exception as e:
            print(f"Error loading recording: {e}")
            return False

    def _monitor_actions(self) -> None:
        """Monitor and record user actions (runs in separate thread)."""
        last_page_source = None

        while self.recording and self.driver:
            try:
                current_source = self.driver.page_source

                # Detect changes in page
                if current_source != last_page_source:
                    # Page changed, likely due to user action
                    # Try to detect what action caused it
                    self._detect_action(last_page_source, current_source)
                    last_page_source = current_source

                time.sleep(MONITOR_INTERVAL_SECONDS)

            except Exception as e:
                print(f"Monitoring error: {e}")
                time.sleep(MONITOR_ERROR_BACKOFF_SECONDS)

    def _detect_action(self, old_source: str | None, new_source: str | None) -> None:
        """Detect what action caused page change"""
        # This is simplified - in reality, you'd need more sophisticated detection
        timestamp = datetime.now().isoformat()

        # For now, just record a generic action
        action = {
            "type": "page_change",
            "timestamp": timestamp,
            "elapsed_time": self._get_elapsed_time(),
            "detected": True,
        }

        self.actions.append(action)

    def _action_to_pytest(self, action: dict[str, Any]) -> str:
        """Convert single action to pytest code"""
        action_type = action["type"]

        if action_type == "click":
            element = action.get("element", {})
            if element.get("id"):
                return f"        driver.find_element(By.ID, '{element['id']}').click()"
            elif element.get("xpath"):
                return f"        driver.find_element(By.XPATH, '{element['xpath']}').click()"
            else:
                return "        # Click action - element not specified"

        elif action_type == "send_keys":
            element = action.get("element", {})
            text = action.get("text", "")
            if element.get("id"):
                return f"        driver.find_element(By.ID, '{element['id']}').send_keys('{text}')"
            elif element.get("xpath"):
                return f"        driver.find_element(By.XPATH, '{element['xpath']}').send_keys('{text}')"
            else:
                return f"        # Send keys: '{text}'"

        elif action_type == "swipe":
            return (
                f"        driver.swipe({action.get('start_x', 0)}, {action.get('start_y', 0)}, "
                f"{action.get('end_x', 0)}, {action.get('end_y', 0)}, {action.get('duration', 500)})"
            )

        elif action_type == "wait":
            return f"        time.sleep({action.get('duration', 1)})"

        elif action_type == "assert":
            element = action.get("element", {})
            if element.get("id"):
                return (
                    f"        assert driver.find_element(By.ID, '{element['id']}').is_displayed()"
                )
            else:
                return "        # Assert action"

        else:
            return f"        # {action_type} action"

    def _action_to_maestro(self, action: dict[str, Any]) -> str:
        """Convert single action to Maestro YAML"""
        action_type = action["type"]

        if action_type == "click":
            element = action.get("element", {})
            if element.get("text"):
                return f"- tapOn: '{element['text']}'"
            elif element.get("id"):
                return f"- tapOn:\n    id: '{element['id']}'"
            else:
                return "- tapOn: # Specify element"

        elif action_type == "send_keys":
            text = action.get("text", "")
            return f"- inputText: '{text}'"

        elif action_type == "swipe":
            direction = action.get("direction", "UP")
            return f"- swipe:\n    direction: {direction}"

        elif action_type == "wait":
            return "- waitForAnimationToEnd"

        elif action_type == "assert":
            element = action.get("element", {})
            if element.get("text"):
                return f"- assertVisible: '{element['text']}'"
            else:
                return "- assertVisible: # Specify element"

        else:
            return f"# {action_type}"

    def _action_to_javascript(self, action: dict[str, Any]) -> str:
        """Convert single action to JavaScript/WebDriverIO"""
        action_type = action["type"]

        if action_type == "click":
            element = action.get("element", {})
            if element.get("id"):
                return f"        await $('#{element['id']}').click();"
            elif element.get("xpath"):
                return f"        await $('{element['xpath']}').click();"
            else:
                return "        // Click action"

        elif action_type == "send_keys":
            element = action.get("element", {})
            text = action.get("text", "")
            if element.get("id"):
                return f"        await $('#{element['id']}').setValue('{text}');"
            else:
                return f"        // Send keys: '{text}'"

        elif action_type == "swipe":
            return "        await driver.touchPerform([/* swipe gesture */]);"

        elif action_type == "wait":
            duration = action.get("duration", 1000)
            return f"        await driver.pause({duration * 1000});"

        else:
            return f"        // {action_type} action"

    def _get_elapsed_time(self) -> float:
        """Get elapsed time since recording started"""
        if self.recording_start_time:
            return (datetime.now() - self.recording_start_time).total_seconds()
        return 0.0

    def replay_actions(self, speed: float = 1.0) -> bool:
        """Replay recorded actions on current driver"""
        if not self.driver or not self.actions:
            return False

        for action in self.actions:
            try:
                self._execute_action(action)

                # Add delay between actions (adjusted by speed)
                time.sleep(REPLAY_DELAY_SECONDS / speed)

            except Exception as e:
                print(f"Error replaying action: {e}")
                return False

        return True

    def _execute_action(self, action: dict[str, Any]):
        """Execute a single recorded action"""
        action_type = action["type"]

        if action_type == "click":
            element = action.get("element", {})
            if element.get("id"):
                self.driver.find_element(By.ID, element["id"]).click()
            elif element.get("xpath"):
                self.driver.find_element(By.XPATH, element["xpath"]).click()

        elif action_type == "send_keys":
            element = action.get("element", {})
            text = action.get("text", "")
            if element.get("id"):
                elem = self.driver.find_element(By.ID, element["id"])
                elem.clear()
                elem.send_keys(text)

        elif action_type == "wait":
            time.sleep(action.get("duration", 1))
