"""
Reporting module for test automation framework.

This module provides utilities for enhanced test reporting with Allure.
"""

from src.reporting.allure_decorators import allure_step_class
from src.reporting.screen_timing import (
    ScreenTimingResult,
    ScreenTimingTracker,
    end_timing_session,
    get_timing_summary,
    get_timing_tracker,
    screen_timing_class,
    start_timing_session,
    track_screen_timing,
)

__all__ = [
    # Allure step decorators
    "allure_step_class",
    # Screen timing
    "screen_timing_class",
    "track_screen_timing",
    "ScreenTimingTracker",
    "ScreenTimingResult",
    "get_timing_tracker",
    "start_timing_session",
    "end_timing_session",
    "get_timing_summary",
]
