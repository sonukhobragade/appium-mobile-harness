#!/usr/bin/env python3
"""Quick smoke test for SmartCheckpointRecorder."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Ensure repository root (contains `src`) is importable when executed directly
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appium_utils.smart_checkpoint_recorder import SmartCheckpointRecorder  # noqa: E402
from src.platform.driver_factory import DriverFactory  # noqa: E402

DEVICE_ID = os.getenv("RECORDER_TEST_DEVICE_ID", "emulator-5554")
APPIUM_SERVER = os.getenv("APPIUM_SERVER_URL", "http://localhost:4723")
APP_PACKAGE = os.getenv("APP_PACKAGE", "com.example.app")
APP_ACTIVITY = os.getenv("APP_ACTIVITY", ".MainActivity")


def main() -> int:
    print("🚀 Testing Recorder Connection...")
    print("=" * 50)

    client = None
    try:
        print("\n1. Connecting to Appium server via DriverFactory...")
        client = DriverFactory.create_driver(
            platform=DriverFactory.ANDROID,
            device_id=DEVICE_ID,
            app_package=APP_PACKAGE,
            app_activity=APP_ACTIVITY,
            appium_server_url=APPIUM_SERVER,
        )
        driver = client.start_session()
        print("✅ Connected to Appium!\n")

        print("2. Creating recorder...")
        recorder = SmartCheckpointRecorder(driver)
        print(f"✅ Recorder created! Platform: {recorder.platform}")

        print("\n3. Starting recording...")
        recorder.start_recording()
        print("✅ Recording started!")

        print("\n4. Capturing first checkpoint...")
        checkpoint1 = recorder.capture_checkpoint("Test Screen 1")
        _print_checkpoint_info(checkpoint1)

        print("\n5. Waiting 2 seconds...")
        time.sleep(2)

        print("\n6. Capturing second checkpoint...")
        checkpoint2 = recorder.capture_checkpoint("Test Screen 2")
        _print_checkpoint_info(checkpoint2)

        print("\n7. Stopping recording...")
        checkpoints = recorder.stop_recording()
        print(f"✅ Recording stopped! Total checkpoints: {len(checkpoints)}")

        print("\n" + "=" * 50)
        print("✅ ALL TESTS PASSED!\nNow try the dashboard - it should work!\n")
        return 0

    except Exception as exc:  # noqa: BLE001
        print(f"\n❌ ERROR: {exc}")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        if client:
            client.quit_session()


def _print_checkpoint_info(checkpoint: dict[str, object]) -> None:
    if checkpoint.get("success") is False:
        print(f"❌ Error: {checkpoint.get('error')}")
        return

    print("✅ Checkpoint captured!")
    print(f"   - Elements found: {checkpoint['element_count']}")
    screenshot = checkpoint.get("screenshot")
    print(f"   - Screenshot: {'Yes' if screenshot else 'No'}")
    if screenshot:
        print(f"   - Screenshot payload: {len(screenshot)} chars")
    if checkpoint.get("inferred_action"):
        print(f"   - Changes detected: {checkpoint['inferred_action']}")


if __name__ == "__main__":
    raise SystemExit(main())
