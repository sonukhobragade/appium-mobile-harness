"""Appium helper utilities for ad-hoc inspection, recording, and dashboarding."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repository root is on sys.path so "src" package imports resolve even when
# utilities are executed directly via `python src/appium_utils/<tool>.py`.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from .inspector import MobileInspector  # noqa: E402
from .page_object_generator import PageObjectGenerator  # noqa: E402
from .quick_test_generator import QuickTestGenerator  # noqa: E402
from .recorder import ActionRecorder  # noqa: E402
from .runner import TestRunner  # noqa: E402
from .smart_checkpoint_recorder import SmartCheckpointRecorder  # noqa: E402
from .utils import clamp, detect_platform  # noqa: E402

__all__ = [
    "ActionRecorder",
    "MobileInspector",
    "PageObjectGenerator",
    "QuickTestGenerator",
    "SmartCheckpointRecorder",
    "TestRunner",
    "detect_platform",
    "clamp",
]
