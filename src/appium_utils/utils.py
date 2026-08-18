"""Shared helpers for ad-hoc Appium utilities."""

from __future__ import annotations

from typing import Any


def detect_platform(driver: Any, default: str = "android") -> str:
    """Return 'android' or 'ios' based on driver capabilities.

    Falls back to ``default`` when platform cannot be determined.
    """
    try:
        capabilities = getattr(driver, "capabilities", {}) or {}
        platform = str(capabilities.get("platformName", "")).lower()
        if "ios" in platform:
            return "ios"
        if "android" in platform:
            return "android"
    except Exception:
        # Detection is best-effort; swallow errors but avoid bare except.
        pass
    return default


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp ``value`` into the inclusive [minimum, maximum] range."""
    return max(minimum, min(maximum, value))
