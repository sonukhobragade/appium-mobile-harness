#!/usr/bin/env python3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.appium_utils.quick_inspector import QuickElementFinder
from src.appium_utils.runner import TestRunner

print("\n" + "=" * 80, flush=True)
print("🔍 APPIUM QUICK INSPECTOR", flush=True)
print("=" * 80 + "\n", flush=True)

try:
    print("📱 Connecting to device...", flush=True)
    runner = TestRunner()
    success, message = runner.connect_from_config()

    if not success:
        print(f"❌ {message}", flush=True)
        sys.exit(1)

    print(f"✅ {message}", flush=True)

    print("\n⏳ Waiting 2 seconds...", flush=True)
    time.sleep(2)

    print("\n🔍 Inspecting screen...\n", flush=True)
    finder = QuickElementFinder(runner.driver)
    finder.inspect_screen(suggest_assertions=True)

    print("\n📸 Saving screenshot...", flush=True)
    finder.save_screenshot("current_screen.png")
    print("✅ Done!", flush=True)

    runner.disconnect()

except Exception as e:
    print(f"\n❌ Error: {e}", flush=True)
    import traceback

    traceback.print_exc()
