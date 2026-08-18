"""
Locator baseline store for self-healing — just a JSON file.

Stores last-known-working locators so when primary locators break,
the framework can try the baseline before calling Claude API.

File: locator_baseline.json (project root, git-committed)
"""

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

# Baseline file at project root (alongside conftest.py)
BASELINE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "locator_baseline.json",
)


def _load() -> dict:
    """Load baseline from JSON file."""
    if os.path.exists(BASELINE_FILE):
        try:
            with open(BASELINE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[HEAL] Could not load baseline: {e}")
    return {}


def save_baseline(element_key: str, by: str, value: str) -> None:
    """Save/update a working locator for an element."""
    baseline = _load()
    baseline[element_key] = {
        "by": by,
        "value": value,
        "updated_at": datetime.now().isoformat(),
        "healed_count": baseline.get(element_key, {}).get("healed_count", 0),
    }
    try:
        with open(BASELINE_FILE, "w", encoding="utf-8") as f:
            json.dump(baseline, f, indent=2, sort_keys=True)
    except OSError as e:
        logger.warning(f"[HEAL] Could not save baseline: {e}")


def get_baseline(element_key: str) -> dict | None:
    """Get last known working locator for an element. Returns None if not found."""
    return _load().get(element_key)


def increment_heal_count(element_key: str) -> None:
    """Increment the healed_count for a baseline entry."""
    baseline = _load()
    if element_key in baseline:
        baseline[element_key]["healed_count"] = baseline[element_key].get("healed_count", 0) + 1
        baseline[element_key]["updated_at"] = datetime.now().isoformat()
        try:
            with open(BASELINE_FILE, "w", encoding="utf-8") as f:
                json.dump(baseline, f, indent=2, sort_keys=True)
        except OSError:
            pass


def get_all_baselines() -> dict:
    """Get all baseline entries (for reporting)."""
    return _load()
