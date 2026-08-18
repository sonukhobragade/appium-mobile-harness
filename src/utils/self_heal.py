"""
Self-healing element finder using baseline JSON + Claude Haiku API.

Flow:
  1. Primary locator fails (TimeoutException)
  2. Try baseline from locator_baseline.json (fast, ~0.1ms)
  3. If baseline also fails, call Claude Haiku with compact element inventory (~$0.003/call)
  4. If healed, update baseline for next time
  5. Next run: baseline works directly, no Claude call needed

Cost: ~$14/year for 148 tests. Basically free.
Toggle: SELF_HEAL=true env var (off by default).
"""

import json
import logging
import os
import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.utils.locator_store import get_baseline, increment_heal_count

logger = logging.getLogger(__name__)

# Cooldown: don't call Claude API more than once per element per 60s
_claude_call_times: dict[str, float] = {}

# Per-session healing stats (reset between tests via conftest fixture)
_heal_stats: dict[str, int] = {
    "baseline_heals": 0,
    "claude_heals": 0,
    "heal_failures": 0,
}


def get_heal_stats() -> dict:
    """Get healing statistics for Allure reporting."""
    return dict(_heal_stats)


def reset_heal_stats() -> None:
    """Reset stats between tests."""
    _heal_stats["baseline_heals"] = 0
    _heal_stats["claude_heals"] = 0
    _heal_stats["heal_failures"] = 0


def try_heal_element(
    driver,
    element_key: str,
    original_by,
    original_locator: str,
    timeout: int = 5,
) -> WebElement | None:
    """
    Try to heal a broken locator. Returns WebElement or None.

    Flow:
    1. Try baseline (last known working locator from JSON) — fast, no API call
    2. Try Claude Haiku API with compact element inventory — ~$0.003/call
    3. If healed, update baseline for next time
    """
    logger.info(f"[HEAL] Attempting self-heal for: {element_key}")

    # Detect if the original locator was text-based
    _is_text_locator = any(
        kw in original_locator for kw in ("text(", "textContains(", "textMatches(")
    )

    # Step 1: Try baseline locator (fast, ~0.1ms)
    baseline = get_baseline(element_key)
    if baseline:
        b = baseline["by"]
        v = baseline["value"]
        # Only try if different from what just failed
        if b != str(original_by) or v != original_locator:
            try:
                element = WebDriverWait(driver, timeout).until(
                    EC.visibility_of_element_located((b, v))
                )
                # FIX: Validate baseline element has useful text for text-based locators
                if _is_text_locator:
                    el_text = (element.text or "").strip()
                    el_class = element.get_attribute("className") or ""
                    if not el_text and "ViewGroup" in el_class:
                        logger.warning(
                            f"[HEAL] Baseline rejected for '{element_key}': "
                            f"ViewGroup with empty .text (poisoned entry)"
                        )
                        # Remove poisoned baseline entry
                        # Overwrite with empty to invalidate (will be cleaned on next heal)
                        raise Exception("Poisoned baseline — ViewGroup container")

                logger.info(f"[HEAL] Baseline healed '{element_key}': {b}={v}")
                _heal_stats["baseline_heals"] += 1
                increment_heal_count(element_key)
                return element
            except Exception:
                logger.debug(f"[HEAL] Baseline also failed for '{element_key}'")

    # Step 2: Try Claude Haiku API (with 60s cooldown)
    now = time.time()
    last_call = _claude_call_times.get(element_key, 0)
    if now - last_call < 60:
        logger.debug(
            f"[HEAL] Claude cooldown active for '{element_key}' (called {now - last_call:.0f}s ago)"
        )
        _heal_stats["heal_failures"] += 1
        return None

    healed = _claude_heal(driver, element_key, str(original_by), original_locator, timeout)
    _claude_call_times[element_key] = now

    if healed:
        _heal_stats["claude_heals"] += 1
    else:
        _heal_stats["heal_failures"] += 1

    return healed


def _clean_attr(value: str | None) -> str:
    """Clean React Native attribute — 'null' string → empty string."""
    if not value or value in ("null", "undefined"):
        return ""
    return value.strip()


def _build_compact_inventory(driver) -> str:
    """
    Build compact element inventory for Claude (~2-3K tokens).

    Instead of driver.page_source (180K+ for React Native), we scan specific
    element types and extract only the 3 key attributes per element.
    """
    inventory = []

    element_types = [
        "android.widget.TextView",
        "android.widget.Button",
        "android.widget.EditText",
        "android.widget.ImageButton",
        "android.widget.ImageView",
    ]

    for cls in element_types:
        try:
            elements = driver.find_elements(AppiumBy.CLASS_NAME, cls)
            for el in elements[:15]:  # Max 15 per type
                try:
                    entry = {
                        "class": cls.split(".")[-1],
                        "text": _clean_attr(el.text),
                        "rid": _clean_attr(el.get_attribute("resource-id")),
                        "desc": _clean_attr(el.get_attribute("content-desc")),
                    }
                    # Only include elements with at least one identifier
                    if any([entry["text"], entry["rid"], entry["desc"]]):
                        inventory.append(entry)
                except StaleElementReferenceException:
                    continue
        except Exception:
            continue

    # Also scan ViewGroups with identifiers (React Native buttons/containers)
    # React Native uses ViewGroup for most interactive elements — scan more
    # FIX: Skip ViewGroups with combined accessibility labels (container elements)
    # These have comma-separated desc like "Login Reward, 1:44pm, 15 Feb 26, •, You, +100"
    # Claude heals to them but .text is empty → wrong element
    try:
        vgs = driver.find_elements(AppiumBy.CLASS_NAME, "android.view.ViewGroup")
        for el in vgs[:50]:
            try:
                desc = _clean_attr(el.get_attribute("content-desc"))
                rid = _clean_attr(el.get_attribute("resource-id"))
                if desc or rid:
                    # Skip container elements with combined accessibility labels
                    if desc and desc.count(",") >= 3:
                        logger.debug(f"[HEAL] Skipping container ViewGroup: desc='{desc[:60]}...'")
                        continue
                    inventory.append({"class": "ViewGroup", "text": "", "rid": rid, "desc": desc})
            except StaleElementReferenceException:
                continue
    except Exception:
        pass

    return json.dumps(inventory, indent=2)


def _add_inventory_fallbacks(
    strategies: list,
    inventory_items: list[dict],
    healed_by: str,
    healed_value: str,
    appium_by,
) -> None:
    """
    Look up Claude's suggestion in the inventory and add cross-attribute fallbacks.

    Example: Claude suggests rid='login-get-otp-button', but the inventory shows
    that element also has desc='GET OTP'. We add text("GET OTP") and
    description("GET OTP") as fallback strategies.
    """
    # Find matching inventory items by any attribute
    for item in inventory_items:
        rid = item.get("rid", "")
        desc = item.get("desc", "")
        text = item.get("text", "")

        # Check if Claude's value matches any attribute of this inventory item
        matched = False
        if healed_value == rid or healed_value == desc or healed_value == text:
            matched = True
        elif healed_value in desc or healed_value in text:
            matched = True

        if not matched:
            continue

        # Found matching inventory item — add all its attributes as strategies
        logger.info(
            f"[HEAL] Inventory match: class={item.get('class')}, "
            f"text='{text}', rid='{rid}', desc='{desc}'"
        )

        if desc and desc != healed_value:
            strategies.append(
                (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().description("{desc}")')
            )
        if text and text != healed_value:
            strategies.append((AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{text}")'))
        if rid and rid != healed_value:
            strategies.append((AppiumBy.ID, rid))
            strategies.append(
                (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().resourceId("{rid}")')
            )
        # Only use first matching inventory item
        break


def _claude_heal(
    driver,
    element_key: str,
    original_by: str,
    original_value: str,
    timeout: int = 5,
) -> WebElement | None:
    """Ask Claude Haiku to find element from compact inventory. Returns WebElement or None."""
    try:
        import anthropic
    except ImportError:
        logger.warning("[HEAL] anthropic package not installed — skipping Claude heal")
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("[HEAL] ANTHROPIC_API_KEY not set — skipping Claude heal")
        return None

    logger.info(f"[HEAL] Calling Claude Haiku for '{element_key}'...")

    try:
        inventory = _build_compact_inventory(driver)
        logger.info(
            f"[HEAL] Inventory size: {len(inventory)} chars, ~{inventory.count('{')} elements"
        )
        logger.debug(f"[HEAL] Inventory:\n{inventory[:3000]}")

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"You are a mobile test automation expert.\n"
                        f"The element '{element_key}' (located by {original_by}='{original_value}') "
                        f"was not found on the current screen.\n\n"
                        f"The original locator is WRONG/OUTDATED. Find the CLOSEST MATCHING element "
                        f"from the inventory below. The element may have different text, a different "
                        f"resource-id, or different content-desc than what was expected.\n\n"
                        f"Look for elements with SIMILAR PURPOSE (e.g., if looking for 'SUBMIT OTP', "
                        f"the correct element might be 'GET OTP' or 'Send OTP').\n\n"
                        f"Return a JSON locator for the best match:\n"
                        f'{{"by": "<strategy>", "value": "<locator>"}}\n\n'
                        f"Valid 'by' values (in order of preference):\n"
                        f"- 'id' for resource-id (fastest)\n"
                        f"- '-android uiautomator' for UiSelector text/desc (fast)\n"
                        f"- 'accessibility id' for content-desc\n"
                        f"- 'xpath' for XPath (slow, AVOID if possible)\n\n"
                        f"IMPORTANT: Do NOT return the original failing locator. "
                        f"Find a DIFFERENT element that serves the same purpose.\n"
                        f"Prefer '-android uiautomator' with UiSelector over 'xpath'.\n"
                        f"Return ONLY the JSON object, nothing else.\n\n"
                        f"Element inventory:\n{inventory[:6000]}"
                    ),
                }
            ],
        )

        result_text = response.content[0].text.strip()
        logger.info(f"[HEAL] Claude raw response: {result_text[:500]}")

        # Extract JSON from response (handle markdown code blocks)
        if "```" in result_text:
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        result = json.loads(result_text)
        healed_by = result["by"]
        healed_value = result["value"]
        logger.info(f"[HEAL] Claude suggested: by='{healed_by}', value='{healed_value}'")

        # Map string strategy names to Appium constants
        by_map = {
            "id": AppiumBy.ID,
            "accessibility id": AppiumBy.ACCESSIBILITY_ID,
            "-android uiautomator": AppiumBy.ANDROID_UIAUTOMATOR,
            "xpath": AppiumBy.XPATH,
            "class name": AppiumBy.CLASS_NAME,
        }
        appium_by = by_map.get(healed_by, healed_by)
        logger.info(f"[HEAL] Mapped strategy: '{healed_by}' → '{appium_by}'")

        # Try healed locator with MULTIPLE strategies (React Native elements
        # may use content-desc OR resource-id — Claude might suggest the wrong one)
        strategies_to_try = [(appium_by, healed_value)]

        # Inventory-aware fallback: look up the element in the inventory
        # to get ALL its attributes (rid, desc, text) for cross-strategy tries
        import re

        inventory_items = json.loads(inventory)
        _add_inventory_fallbacks(
            strategies_to_try, inventory_items, healed_by, healed_value, appium_by
        )

        # Strategy-specific fallbacks (when inventory lookup doesn't help)
        if appium_by == AppiumBy.XPATH:
            text_match = re.search(r"@text=['\"]([^'\"]+)['\"]", healed_value)
            desc_match = re.search(r"@content-desc=['\"]([^'\"]+)['\"]", healed_value)
            rid_match = re.search(r"@resource-id=['\"]([^'\"]+)['\"]", healed_value)
            for match, strategies in [
                (
                    text_match,
                    [
                        lambda v: (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{v}")'),
                        lambda v: (
                            AppiumBy.ANDROID_UIAUTOMATOR,
                            f'new UiSelector().textContains("{v}")',
                        ),
                        lambda v: (
                            AppiumBy.ANDROID_UIAUTOMATOR,
                            f'new UiSelector().description("{v}")',
                        ),
                    ],
                ),
                (
                    desc_match,
                    [
                        lambda v: (
                            AppiumBy.ANDROID_UIAUTOMATOR,
                            f'new UiSelector().description("{v}")',
                        ),
                        lambda v: (AppiumBy.ID, v),
                    ],
                ),
                (
                    rid_match,
                    [
                        lambda v: (AppiumBy.ID, v),
                        lambda v: (
                            AppiumBy.ANDROID_UIAUTOMATOR,
                            f'new UiSelector().resourceId("{v}")',
                        ),
                    ],
                ),
            ]:
                if match:
                    val = match.group(1)
                    strategies_to_try.extend([s(val) for s in strategies])
        elif appium_by == AppiumBy.ANDROID_UIAUTOMATOR:
            match = re.search(r'(?:text|description|resourceId)\("([^"]+)"\)', healed_value)
            if match:
                raw_val = match.group(1)
                strategies_to_try.extend(
                    [
                        (AppiumBy.ID, raw_val),
                        (
                            AppiumBy.ANDROID_UIAUTOMATOR,
                            f'new UiSelector().description("{raw_val}")',
                        ),
                        (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{raw_val}")'),
                        (
                            AppiumBy.ANDROID_UIAUTOMATOR,
                            f'new UiSelector().textContains("{raw_val}")',
                        ),
                    ]
                )

        # Detect if the original locator was text-based (text/textContains/textMatches)
        _is_text_locator = any(
            kw in original_value for kw in ("text(", "textContains(", "textMatches(")
        )

        element = None
        winning_by = None
        winning_value = None

        for try_by, try_value in strategies_to_try:
            try:
                logger.info(f"[HEAL] Trying: {try_by}='{try_value[:80]}'")
                candidate = WebDriverWait(driver, timeout).until(
                    EC.visibility_of_element_located((try_by, try_value))
                )

                # FIX: Reject container elements with empty .text for text-based locators
                # ViewGroup containers have combined accessibility labels but .text is ""
                if _is_text_locator:
                    candidate_text = (candidate.text or "").strip()
                    candidate_class = candidate.get_attribute("className") or ""
                    if not candidate_text and "ViewGroup" in candidate_class:
                        logger.warning(
                            f"[HEAL] Rejected: {try_by}='{try_value[:80]}' — "
                            f"ViewGroup with empty .text (container element)"
                        )
                        continue

                element = candidate
                winning_by = str(try_by)
                winning_value = try_value
                logger.info(f"[HEAL] ✓ Found with: {try_by}='{try_value[:80]}'")
                break
            except Exception:
                logger.debug(f"[HEAL] Strategy failed: {try_by}")
                continue

        if element is None:
            raise TimeoutException(
                f"Claude suggested '{healed_value}' but none of "
                f"{len(strategies_to_try)} strategies found it"
            )

        logger.info(f"[HEAL] Claude healed '{element_key}': {winning_by}={winning_value[:80]}")

        # NOTE: Do NOT save to baseline — Claude's guess may be wrong.
        # Heal is diagnostic only: helps current run, logs what to fix.
        # Developer must manually update the page object locator.
        logger.warning(
            f"[HEAL] ACTION REQUIRED: Update page object locator for '{element_key}'\n"
            f"  Old: {original_by}='{original_value}'\n"
            f"  Fix: {winning_by}='{winning_value}'"
        )

        return element

    except Exception as e:
        logger.warning(f"[HEAL] Claude could not heal '{element_key}': {e}")
        return None
