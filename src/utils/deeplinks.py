"""Centralized deeplink helpers.

Use `mobile: deepLink` Appium command — fires intent against the running
session, does NOT relaunch the app. Preserves login state, in-memory user
profile, OTP-verified flag, etc.

Add new app routes here as the team uncovers them. One source of truth.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

APP_PACKAGE = "com.example.app"
SCHEME = "example"


def _fire(driver, url: str) -> None:
    """Fire deeplink intent through current Appium session (no relaunch)."""
    logger.info("DeepLink: %s", url)
    driver.execute_script(
        "mobile: deepLink",
        {"url": url, "package": APP_PACKAGE},
    )


class DeepLinks:
    """Static helpers for in-app navigation via deeplink intents.

    All methods preserve current Appium session — no app relaunch, no login loss.
    Use AFTER login + onboarding so user lands on the destination screen with
    full session state intact.
    """

    @staticmethod
    def go_to_report_template(driver, template_id: int | str) -> None:
        """Navigate to the Report Detail screen for a given template id.

        Bypasses Reports tab → section scroll → card tap. Reliable on cloud
        devices where RN FlatList virtualisation + section ordering can hide
        cards from UiAutomator scroll.
        """
        _fire(driver, f"{SCHEME}://reports/template/{template_id}")

    @staticmethod
    def go_to_reports(driver) -> None:
        """Navigate to the Reports tab root (profile switcher visible at top)."""
        _fire(driver, f"{SCHEME}://reports")

    @staticmethod
    def go_to_profile_edit(driver) -> None:
        """Navigate to the Profile Edit form (existing/complete user).

        Bypasses the post-OTP chat landing → Home → drawer → Edit Profile
        chain (flaky: coachmark overlay captures the a11y tree). The route is
        not in DEEPLINK_ONBOARDING_ROUTES, so it falls to DEFAULT_DEEPLINK_CONFIG
        (requiresOnboarding=false, requiresAuth=true) — an authed + onboarded
        user lands directly on /profile/profile-edit with session intact.
        FE: app/(screens)/profile/profile-edit.tsx + config/deeplink-config.ts.
        """
        _fire(driver, f"{SCHEME}://profile/profile-edit")

    @staticmethod
    def go_to_downloaded_reports(driver) -> None:
        """Navigate to the Downloaded Reports (history) screen.

        Bypasses Reports tab → "View your downloaded reports" link tap which
        is hidden behind a Fabric-virtualised scroll on Reports Home.
        """
        _fire(driver, f"{SCHEME}://reports/history")
