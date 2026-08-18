"""
Test Runner Class
Executes tests and manages test sessions
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.platform.appium_client import AppiumClient
from src.platform.driver_factory import DriverFactory


class TestRunner:
    """Manages test execution and Appium sessions"""

    def __init__(self):
        self.driver = None
        self.client: AppiumClient | None = None
        self.session_info = {}
        self.test_results = []
        self.current_test = None

    def load_device_config(self, config_path: str | None = None) -> dict[str, Any]:
        """Load and validate the device configuration JSON file."""
        path = Path(config_path) if config_path else Path(__file__).with_name("device_config.json")

        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Device config not found: {path}") from exc

        try:
            config = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

        self._validate_device_config(config)
        return config

    def _validate_device_config(self, config: dict[str, Any]) -> None:
        """Ensure the loaded config has the expected structure."""
        if not isinstance(config, dict):
            raise ValueError("Device config must be a JSON object")

        devices = config.get("devices")
        if not isinstance(devices, dict) or not devices:
            raise ValueError("Device config requires a non-empty 'devices' section")

        default_device = config.get("default_device")
        if default_device and default_device not in devices:
            raise ValueError(f"default_device '{default_device}' not found in devices")

        for profile_id, capabilities in devices.items():
            if not isinstance(capabilities, dict):
                raise ValueError(f"Device profile '{profile_id}' must be an object")

            platform_name = str(capabilities.get("platformName", "")).lower()
            if platform_name not in {"android", "ios"}:
                raise ValueError(f"Device '{profile_id}' missing valid platformName (android/ios)")

            udid = capabilities.get("appium:udid") or capabilities.get("udid")
            if not udid:
                raise ValueError(f"Device '{profile_id}' missing 'appium:udid' capability")

    def get_device_profiles(self) -> list[dict[str, Any]]:
        """Get list of available device profiles from config"""
        try:
            config = self.load_device_config()
        except (FileNotFoundError, ValueError) as exc:
            print(f"Device profile load error: {exc}")
            return []

        profiles = []
        for device_id, device_caps in config["devices"].items():
            profiles.append(
                {
                    "id": device_id,
                    "name": device_caps.get("name", device_id),
                    "platform": device_caps.get("platformName", "Unknown"),
                    "udid": device_caps.get("appium:udid", "Unknown"),
                }
            )
        return profiles

    def _normalize_appium_url(self, appium_url: str | None) -> str:
        """Normalize Appium server URL for Appium 2.x (without /wd/hub)."""
        url = appium_url or os.getenv("APPIUM_SERVER_URL", "http://localhost:4723")
        if url.endswith("/"):
            url = url.rstrip("/")
        if url.lower().endswith("/wd/hub"):
            url = url[: -len("/wd/hub")]
        return url

    def _start_session(self, client: AppiumClient, profile_label: str | None = None) -> tuple:
        """Start Appium session via AppiumClient and store metadata."""
        try:
            driver = client.start_session()
        except Exception as exc:
            self.driver = None
            self.client = None
            return False, f"Connection failed: {exc}"

        self.client = client
        self.driver = driver

        capabilities = getattr(driver, "capabilities", {}) or {}
        platform = (capabilities.get("platformName") or client.platform or "unknown").title()
        device_name = (
            capabilities.get("deviceName")
            or capabilities.get("appium:deviceName")
            or profile_label
            or "Unknown device"
        )
        package = (
            capabilities.get("appPackage")
            or capabilities.get("appium:appPackage")
            or capabilities.get("bundleId")
            or capabilities.get("appium:bundleId")
            or "Unknown"
        )

        self.session_info = {
            "platform": platform,
            "device": device_name,
            "app_package": package,
            "profile": profile_label or device_name,
            "session_id": getattr(driver, "session_id", "unknown"),
            "connected_at": datetime.now().isoformat(),
        }

        return True, f"Successfully connected to {self.session_info['profile']}"

    def _create_client_from_caps(
        self, capabilities: dict[str, Any], appium_url: str
    ) -> AppiumClient:
        """Build AppiumClient from legacy capability dict."""
        if not capabilities:
            raise ValueError("No capabilities provided")

        platform = str(
            capabilities.get("platformName") or capabilities.get("platform") or "android"
        ).lower()

        base_kwargs: dict[str, Any] = {
            "device_id": None,
            "app_package": None,
            "app_activity": None,
        }
        extra_caps: dict[str, Any] = {}

        for raw_key, value in capabilities.items():
            if raw_key in {"name", "platformName", "platform"}:
                continue

            key = raw_key.replace("appium:", "")
            lowered = key.lower()

            if lowered == "udid":
                base_kwargs["device_id"] = value
            elif lowered == "apppackage":
                base_kwargs["app_package"] = value
            elif lowered == "appactivity":
                base_kwargs["app_activity"] = value
            elif lowered == "bundleid":
                base_kwargs["app_package"] = value
            else:
                extra_caps[raw_key] = value

        return DriverFactory.create_driver(
            platform=platform,
            device_id=base_kwargs["device_id"],
            app_package=base_kwargs["app_package"],
            app_activity=base_kwargs["app_activity"],
            appium_server_url=self._normalize_appium_url(appium_url),
            **extra_caps,
        )

    def connect_from_config(self, device_profile_id: str = None, config_path: str = None) -> tuple:
        """Connect to device using capabilities from config file

        Args:
            device_profile_id: ID of device profile (e.g., 'android_dev')
                             If None, uses default_device from config
            config_path: Path to config file (optional)

        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            # Load config
            config = self.load_device_config(config_path)
            if not config:
                return False, "Failed to load device configuration"

            # Get device profile
            if device_profile_id is None:
                device_profile_id = config.get("default_device")
                if not device_profile_id:
                    return False, "No default device specified in config"

            if device_profile_id not in config.get("devices", {}):
                return False, f"Device profile '{device_profile_id}' not found in config"

            device_caps = config["devices"][device_profile_id]
            appium_url = config.get("appium_url")

            client = self._create_client_from_caps(device_caps, appium_url)
            profile_label = device_caps.get("name", device_profile_id)
            return self._start_session(client, profile_label)

        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def connect_android(
        self,
        device_name: str,
        app_package: str = None,
        app_activity: str = None,
        appium_url: str = None,
    ) -> tuple:
        """Connect to Android device"""
        try:
            client = DriverFactory.create_driver(
                platform=DriverFactory.ANDROID,
                device_id=device_name,
                app_package=app_package,
                app_activity=app_activity or ".MainActivity",
                appium_server_url=self._normalize_appium_url(appium_url),
            )
            return self._start_session(client, profile_label=device_name)
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def connect_ios(
        self, device_name: str, bundle_id: str = None, udid: str = None, appium_url: str = None
    ) -> tuple:
        """Connect to iOS device"""
        try:
            client = DriverFactory.create_driver(
                platform=DriverFactory.IOS,
                device_id=udid or device_name,
                app_package=bundle_id,
                appium_server_url=self._normalize_appium_url(appium_url),
            )
            return self._start_session(client, profile_label=device_name)
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def disconnect(self) -> bool:
        """Disconnect from device"""
        try:
            if self.client:
                self.client.quit_session()
            elif self.driver:
                self.driver.quit()
            return True
        except Exception as e:
            print(f"Error disconnecting: {e}")
            return False
        finally:
            self.driver = None
            self.client = None
            self.session_info = {}

    def run_pytest_file(self, test_file: str, markers: str = None) -> dict[str, Any]:
        """Run pytest file and capture results"""
        start_time = datetime.now()

        # Build pytest command
        cmd = [sys.executable, "-m", "pytest", test_file, "-v", "--json-report"]

        if markers:
            cmd.extend(["-m", markers])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes timeout
                env={**os.environ, "PYTEST_CURRENT_TEST": test_file},
            )

            duration = (datetime.now() - start_time).total_seconds()

            # Try to read JSON report
            test_result = {
                "test_file": test_file,
                "start_time": start_time.isoformat(),
                "duration": duration,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "return_code": result.returncode,
                "status": "passed" if result.returncode == 0 else "failed",
            }

            try:
                with open(".pytest_cache/json_report.json", encoding="utf-8") as f:
                    json_report = json.load(f)
                    test_result["summary"] = json_report.get("summary", {})
                    test_result["tests"] = json_report.get("tests", [])
            except (FileNotFoundError, json.JSONDecodeError):
                # JSON report plugin optional; ignore missing/invalid file
                pass

            self.test_results.append(test_result)
            return test_result

        except subprocess.TimeoutExpired:
            return {
                "test_file": test_file,
                "status": "timeout",
                "error": "Test execution timeout (5 minutes)",
                "duration": 300,
            }
        except Exception as e:
            return {"test_file": test_file, "status": "error", "error": str(e), "duration": 0}

    def run_maestro_flow(self, flow_file: str) -> dict[str, Any]:
        """Run Maestro flow file"""
        start_time = datetime.now()

        try:
            result = subprocess.run(
                ["maestro", "test", flow_file], capture_output=True, text=True, timeout=300
            )

            duration = (datetime.now() - start_time).total_seconds()

            test_result = {
                "flow_file": flow_file,
                "start_time": start_time.isoformat(),
                "duration": duration,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "return_code": result.returncode,
                "status": "passed" if result.returncode == 0 else "failed",
            }

            self.test_results.append(test_result)
            return test_result

        except Exception as e:
            return {"flow_file": flow_file, "status": "error", "error": str(e), "duration": 0}

    def run_script_directly(self, script_content: str, language: str = "python") -> dict[str, Any]:
        """Run ad-hoc script content (trusted input only)."""
        if not self.driver:
            return {"status": "error", "error": "No active driver session"}

        start_time = datetime.now()

        try:
            if language == "python":
                if not script_content or not script_content.strip():
                    return {"status": "error", "error": "Script content is empty"}

                try:
                    compile(script_content, "<dynamic-script>", "exec")
                except SyntaxError as exc:
                    return {
                        "status": "error",
                        "error": f"Script has syntax error: {exc}",
                        "duration": 0,
                    }

                # Create namespace with driver and imports
                namespace = {
                    "driver": self.driver,
                    "By": By,
                    "WebDriverWait": WebDriverWait,
                    "EC": EC,
                    "time": time,
                }

                # Execute the script
                exec(script_content, namespace)

                duration = (datetime.now() - start_time).total_seconds()

                return {
                    "status": "passed",
                    "duration": duration,
                    "message": "Script executed successfully",
                }

        except AssertionError as e:
            return {
                "status": "failed",
                "error": f"Assertion failed: {str(e)}",
                "duration": (datetime.now() - start_time).total_seconds(),
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "duration": (datetime.now() - start_time).total_seconds(),
            }

    def run_test_suite(self, test_files: list[str]) -> dict[str, Any]:
        """Run multiple test files as a suite"""
        suite_start = datetime.now()
        suite_results = {
            "suite_name": f"Test Suite {suite_start.strftime('%Y%m%d_%H%M%S')}",
            "start_time": suite_start.isoformat(),
            "tests": [],
            "total": len(test_files),
            "passed": 0,
            "failed": 0,
            "errors": 0,
        }

        for test_file in test_files:
            result = self.run_pytest_file(test_file)
            suite_results["tests"].append(result)

            if result["status"] == "passed":
                suite_results["passed"] += 1
            elif result["status"] == "failed":
                suite_results["failed"] += 1
            else:
                suite_results["errors"] += 1

        suite_results["duration"] = (datetime.now() - suite_start).total_seconds()
        suite_results["pass_rate"] = (
            (suite_results["passed"] / suite_results["total"] * 100)
            if suite_results["total"] > 0
            else 0
        )

        return suite_results

    def get_test_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent test execution history"""
        return self.test_results[-limit:] if self.test_results else []

    def export_results(self, format: str = "json", filepath: str = None) -> str:
        """Export test results in specified format"""
        if not filepath:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = f"test_results_{timestamp}.{format}"

        if format == "json":
            with open(filepath, "w") as f:
                json.dump(self.test_results, f, indent=2)

        elif format == "html":
            html_content = self._generate_html_report()
            with open(filepath, "w") as f:
                f.write(html_content)

        elif format == "csv":
            import csv

            with open(filepath, "w", newline="") as f:
                if self.test_results:
                    writer = csv.DictWriter(f, fieldnames=self.test_results[0].keys())
                    writer.writeheader()
                    writer.writerows(self.test_results)

        return filepath

    def _generate_html_report(self) -> str:
        """Generate HTML test report"""
        html = """
        <html>
        <head>
            <title>Test Results Report</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; }
                h1 { color: #333; }
                table { border-collapse: collapse; width: 100%; }
                th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
                th { background-color: #4CAF50; color: white; }
                .passed { color: green; }
                .failed { color: red; }
                .error { color: orange; }
            </style>
        </head>
        <body>
            <h1>Test Execution Report</h1>
            <table>
                <tr>
                    <th>Test</th>
                    <th>Status</th>
                    <th>Duration (s)</th>
                    <th>Timestamp</th>
                </tr>
        """

        for result in self.test_results:
            status_class = result.get("status", "error")
            html += f"""
                <tr>
                    <td>{result.get("test_file", "Unknown")}</td>
                    <td class="{status_class}">{result.get("status", "N/A")}</td>
                    <td>{result.get("duration", 0):.2f}</td>
                    <td>{result.get("start_time", "N/A")}</td>
                </tr>
            """

        html += """
            </table>
        </body>
        </html>
        """

        return html

    def is_connected(self) -> bool:
        """Check if driver is connected"""
        return self.driver is not None

    def get_session_info(self) -> dict[str, Any]:
        """Get current session information"""
        return self.session_info
