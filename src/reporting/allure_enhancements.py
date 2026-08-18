"""
Allure Report Enhancement Utilities

This module provides utilities to enhance Allure reports with:
- Environment information
- Executor details (CI/CD)
- Custom categories
- Report metadata
"""

import json
import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path


class AllureEnhancements:
    """Utilities to enhance Allure reports with metadata and information"""

    @staticmethod
    def write_environment_info(
        output_dir: str = "allure-results", additional_info: dict[str, str] | None = None
    ):
        """
        Write environment.properties file for Allure report.

        Args:
            output_dir: Directory where allure-results are stored
            additional_info: Additional key-value pairs to include
        """
        env_file = Path(output_dir) / "environment.properties"

        # System information
        env_info = {
            "OS": platform.system(),
            "OS Version": platform.version(),
            "Python Version": platform.python_version(),
            "Platform": platform.platform(),
            "Machine": platform.machine(),
            "Processor": platform.processor(),
            "Report Generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # Git information (if available)
        try:
            git_branch = (
                subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL
                )
                .decode()
                .strip()
            )
            git_commit = (
                subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
                .decode()
                .strip()[:8]
            )
            env_info["Git Branch"] = git_branch
            env_info["Git Commit"] = git_commit
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Appium information
        try:
            appium_version = (
                subprocess.check_output(["appium", "--version"], stderr=subprocess.DEVNULL)
                .decode()
                .strip()
            )
            env_info["Appium Version"] = appium_version
        except (subprocess.CalledProcessError, FileNotFoundError):
            env_info["Appium Version"] = "Not installed"

        # ADB information (Android)
        try:
            adb_version = (
                subprocess.check_output(["adb", "version"], stderr=subprocess.DEVNULL)
                .decode()
                .strip()
                .split()[0]
            )
            env_info["ADB Version"] = adb_version
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Android App Version Information
        # Get app package from environment or config
        app_package = os.getenv("ANDROID_APP_PACKAGE", "com.example.app")
        device_id = os.getenv("ANDROID_SERIAL", os.getenv("DEVICE_UDID"))

        if device_id and app_package:
            try:
                # Get app version info from device using dumpsys
                adb_cmd = ["adb", "-s", device_id, "shell", "dumpsys", "package", app_package]
                dumpsys_output = subprocess.check_output(
                    adb_cmd, stderr=subprocess.DEVNULL
                ).decode()

                # Parse version information
                for line in dumpsys_output.split("\n"):
                    line = line.strip()
                    if line.startswith("versionName="):
                        version_name = line.split("=")[1].strip()
                        env_info["App Version Name"] = version_name
                    elif line.startswith("versionCode="):
                        # Extract versionCode, minSdk, targetSdk from same line
                        # Format: versionCode=64 minSdk=24 targetSdk=35
                        parts = line.split()
                        for part in parts:
                            if "versionCode=" in part:
                                env_info["App Version Code"] = part.split("=")[1]
                            elif "minSdk=" in part:
                                env_info["App Min SDK"] = part.split("=")[1]
                            elif "targetSdk=" in part:
                                env_info["App Target SDK"] = part.split("=")[1]

                # Add app package name
                env_info["App Package"] = app_package
            except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
                # If we can't get app info, continue without it
                pass

        # Environment variables
        env_vars = {
            "RUN_MODE": os.getenv("RUN_MODE", "local"),
            "PLATFORM": os.getenv("PLATFORM", "not set"),
            "DEVICE_PROVIDER": os.getenv("DEVICE_PROVIDER", "local"),
            "Retry Policy": "1 retry with 10s delay",
        }
        for key, value in env_vars.items():
            if value != "not set":
                env_info[key] = value

        # Device information from config
        try:
            from src.data.parsers.config_manager import ConfigManager

            config_mgr = ConfigManager()
            run_mode = os.getenv("RUN_MODE", "local")
            profile_key = "lambdatest" if run_mode == "lambdatest" else "local"
            devices_config = config_mgr.get(f"profiles.{profile_key}.devices.android", {})

            if devices_config:
                for key in sorted(devices_config.keys()):
                    if key.startswith("device_") and isinstance(devices_config[key], dict):
                        dev = devices_config[key]
                        idx = key.replace("device_", "")
                        if dev.get("name"):
                            env_info[f"Device {idx}"] = (
                                f"{dev['name']} (Android {dev.get('platform_version', 'N/A')})"
                            )

                # For local profile with primary_device_id
                if "primary_device_id" in devices_config and "Device 1" not in env_info:
                    env_info["Device"] = f"Local ({devices_config['primary_device_id']})"
        except Exception:
            pass

        # OEM family per device (set by Configure Devices step in CI)
        env_info["Device.1.OEMFamily"] = os.getenv("DEVICE1_OEM_FAMILY", "unknown_android")
        env_info["Device.2.OEMFamily"] = os.getenv("DEVICE2_OEM_FAMILY", "unknown_android")

        # Add additional info
        if additional_info:
            env_info.update(additional_info)

        # Write to file
        env_file.parent.mkdir(parents=True, exist_ok=True)
        with open(env_file, "w") as f:
            for key, value in env_info.items():
                f.write(f"{key}={value}\n")

        print(f"✅ Environment info written to {env_file}")

    @staticmethod
    def write_executor_info(
        output_dir: str = "allure-results",
        build_name: str | None = None,
        build_url: str | None = None,
        report_url: str | None = None,
        executor_name: str | None = None,
    ):
        """
        Write executor.json file for Allure report (CI/CD information).

        Args:
            output_dir: Directory where allure-results are stored
            build_name: Build name/number
            build_url: URL to build (CI/CD)
            report_url: URL to this report
            executor_name: Name of executor (e.g., "GitHub Actions", "Jenkins")
        """
        executor_file = Path(output_dir) / "executor.json"

        # Detect CI/CD environment
        ci_info = AllureEnhancements._detect_ci_environment()

        executor_data = {
            "name": executor_name or ci_info.get("name", "Local Execution"),
            "type": ci_info.get("type", "local"),
            "buildName": build_name
            or ci_info.get("build_name", f"Build {datetime.now().strftime('%Y%m%d-%H%M%S')}"),
            "buildUrl": build_url or ci_info.get("build_url"),
            "reportUrl": report_url or ci_info.get("report_url"),
            "reportName": "the app Mobile Test Automation Report",
        }

        # Remove None values
        executor_data = {k: v for k, v in executor_data.items() if v is not None}

        executor_file.parent.mkdir(parents=True, exist_ok=True)
        with open(executor_file, "w") as f:
            json.dump(executor_data, f, indent=2)

        print(f"✅ Executor info written to {executor_file}")

    @staticmethod
    def _detect_ci_environment() -> dict[str, str]:
        """Detect CI/CD environment from environment variables."""
        ci_info = {}

        # GitHub Actions
        if os.getenv("GITHUB_ACTIONS"):
            ci_info = {
                "name": "GitHub Actions",
                "type": "github",
                "build_name": f"{os.getenv('GITHUB_WORKFLOW', 'Unknown')} #{os.getenv('GITHUB_RUN_NUMBER', 'N/A')}",
                "build_url": f"{os.getenv('GITHUB_SERVER_URL', '')}/{os.getenv('GITHUB_REPOSITORY', '')}/actions/runs/{os.getenv('GITHUB_RUN_ID', '')}",
            }

        # Jenkins
        elif os.getenv("JENKINS_URL"):
            ci_info = {
                "name": "Jenkins",
                "type": "jenkins",
                "build_name": f"{os.getenv('JOB_NAME', 'Unknown')} #{os.getenv('BUILD_NUMBER', 'N/A')}",
                "build_url": os.getenv("BUILD_URL"),
            }

        # GitLab CI
        elif os.getenv("CI_JOB_URL"):
            ci_info = {
                "name": "GitLab CI",
                "type": "gitlab",
                "build_name": f"{os.getenv('CI_JOB_NAME', 'Unknown')} #{os.getenv('CI_JOB_ID', 'N/A')}",
                "build_url": os.getenv("CI_JOB_URL"),
            }

        # CircleCI
        elif os.getenv("CIRCLECI"):
            ci_info = {
                "name": "CircleCI",
                "type": "circleci",
                "build_name": f"{os.getenv('CIRCLE_JOB', 'Unknown')} #{os.getenv('CIRCLE_BUILD_NUM', 'N/A')}",
                "build_url": os.getenv("CIRCLE_BUILD_URL"),
            }

        return ci_info

    @staticmethod
    def write_categories(output_dir: str = "allure-results", custom_categories: list | None = None):
        """
        Write categories.json for custom test result categorization.

        Args:
            output_dir: Directory where allure-results are stored
            custom_categories: List of custom category definitions
        """
        categories_file = Path(output_dir) / "categories.json"

        # Default categories (order matters — first match wins in Allure)
        default_categories = [
            {
                "name": "Infrastructure Failures",
                "matchedStatuses": ["failed", "broken"],
                "messageRegex": ".*timed out from.*queue.*|.*lambda.*queue.*|.*device not available.*|.*session not created.*|.*Could not start a new session.*|.*unreachable.*",
                "traceRegex": ".*timed out from.*queue.*|.*lambda.*queue.*|.*device not available.*|.*session not created.*|.*Could not start a new session.*|.*unreachable.*",
            },
            {
                "name": "Product Defects",
                "matchedStatuses": ["failed"],
            },
            {
                "name": "Test Defects",
                "matchedStatuses": ["broken"],
            },
            {
                "name": "Skipped Tests",
                "matchedStatuses": ["skipped"],
            },
        ]

        categories = custom_categories or default_categories

        categories_file.parent.mkdir(parents=True, exist_ok=True)
        with open(categories_file, "w") as f:
            json.dump(categories, f, indent=2)

        print(f"✅ Categories written to {categories_file}")


def setup_allure_metadata(
    output_dir: str = "allure-results",
    build_name: str | None = None,
    additional_env_info: dict[str, str] | None = None,
):
    """
    Convenience function to set up all Allure metadata at once.

    Args:
        output_dir: Directory where allure-results are stored
        build_name: Build name/number
        additional_env_info: Additional environment information
    """
    AllureEnhancements.write_environment_info(output_dir, additional_env_info)
    AllureEnhancements.write_executor_info(output_dir, build_name=build_name)
    AllureEnhancements.write_categories(output_dir)
    print("✨ Allure metadata setup complete!")
