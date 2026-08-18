"""
config_manager.py — layered configuration for the test run.

Resolution order, highest priority first:

    1. environment variables, for anything CI needs to override per job
    2. the selected profile block, ``profiles.<name>.*``
    3. the top-level defaults in the config file

So ``config.yaml`` holds what is true everywhere, a profile holds what changes
per execution target (local emulator, a cloud device grid, a nightly matrix),
and the environment wins over both because a CI job cannot edit a file.

Keys are addressed with dotted paths::

    ConfigManager().get("devices.android.primary_device_id", "emulator-5554")
    ConfigManager(profile="lambdatest").get("provider.hub_url")

A missing key returns the default rather than raising. Configuration that is
merely absent should not crash a suite that has a sensible fallback; the places
where a missing value genuinely cannot be defaulted raise at the point of use,
where the error message can say what the value was for.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# Searched in order; the first that exists wins. CONFIG_FILE overrides all.
# settings/config.yaml is first because that is pytest's --test-config default;
# leaving it out meant the session fixture and every bare ConfigManager() in
# conftest.py could read different files in the same run.
_DEFAULT_CONFIG_NAMES = (
    "settings/config.yaml",
    "config.yaml",
    "config.yml",
    "config/config.yaml",
)

# Dotted key -> (environment variable, type). The type is explicit per key
# because blanket coercion corrupts identifiers: ANDROID_SERIAL=001234 parses
# as int 1234 and then addresses a device that does not exist.
_ENV_OVERRIDES = {
    "provider.name": ("DEVICE_PROVIDER", str),
    "provider.hub_url": ("APPIUM_SERVER_URL", str),
    "devices.android.primary_device_id": ("ANDROID_SERIAL", str),
    "orchestration.device_count": ("DEVICE_COUNT", int),
    "orchestration.enable_multi_device": ("ENABLE_MULTI_DEVICE", bool),
    "reporting.slack.webhook_url": ("SLACK_WEBHOOK_URL", str),
    "reporting.slack.enabled": ("SLACK_ENABLED", bool),
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _coerce(raw: str, kind: type) -> Any:
    """Convert an environment string to the type this specific key expects."""
    if kind is bool:
        low = raw.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"expected a boolean, got {raw!r}")
    if kind is int:
        return int(raw)
    if kind is float:
        return float(raw)
    return raw


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override onto base recursively, without mutating either."""
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def _project_root() -> Path:
    # src/data/parsers/config_manager.py -> repo root
    return Path(__file__).resolve().parents[3]


class ConfigManager:
    """Reads the config file once per (path, profile) and answers dotted lookups."""

    _cache: dict[tuple[str, str | None], dict] = {}

    def __init__(self, profile: str | None = None, config_path: str | Path | None = None):
        # RUN_MODE is what conftest.py actually sets to choose an execution
        # target. Honouring only TEST_PROFILE meant RUN_MODE=lambdatest silently
        # ran against the local defaults instead.
        run_mode = os.getenv("RUN_MODE")
        if run_mode in ("local", ""):
            run_mode = None
        self.profile = profile or os.getenv("TEST_PROFILE") or run_mode or None
        self._explicit = bool(config_path or os.getenv("CONFIG_FILE"))
        self.config_path = self._resolve_path(config_path)
        self._data = self._load()

    # ── construction ──────────────────────────────────────────────────────

    @classmethod
    def from_cli_args(cls, options: Any) -> ConfigManager:
        """Build from a pytest ``Namespace``.

        Accepts anything attribute-addressable, so it works with pytest's
        ``pytestconfig.option`` and with a plain argparse namespace in tests.
        """
        profile = getattr(options, "profile", None)
        # pytest stores --test-config as options.test_config. The other names are
        # accepted so a plain argparse namespace works in tests.
        config_path = (
            getattr(options, "test_config", None)
            or getattr(options, "config", None)
            or getattr(options, "config_file", None)
        )
        return cls(profile=profile, config_path=config_path)

    @staticmethod
    def _resolve_path(config_path: str | Path | None) -> Path | None:
        if config_path:
            return Path(config_path)
        env_path = os.getenv("CONFIG_FILE")
        if env_path:
            return Path(env_path)
        root = _project_root()
        for name in _DEFAULT_CONFIG_NAMES:
            candidate = root / name
            if candidate.exists():
                return candidate
        return None

    def _load(self) -> dict:
        # Checked before the cache: a file that existed on a previous load and
        # has since been removed must still raise, not return stale content.
        if self._explicit and self.config_path and not self.config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {self.config_path}. It was requested "
                f"explicitly via --test-config or CONFIG_FILE."
            )

        key = (str(self.config_path), self.profile)
        if key in self._cache:
            return self._cache[key]

        data: dict = {}
        if self.config_path and self.config_path.exists():
            with open(self.config_path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                raise ValueError(
                    f"{self.config_path} must contain a mapping at the top level, "
                    f"got {type(data).__name__}."
                )

        self._cache[key] = data
        return data

    @classmethod
    def clear_cache(cls) -> None:
        """Drop cached files. Tests that write a config on the fly need this."""
        cls._cache.clear()

    # ── lookup ────────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Resolve a dotted key against env, then profile, then defaults."""
        override = _ENV_OVERRIDES.get(key)
        if override:
            env_var, kind = override
            raw = os.getenv(env_var)
            if raw not in (None, ""):
                return _coerce(raw, kind)

        found_default, base = self._dig(self._data, key)

        if self.profile:
            found_profile, override = self._dig(
                self._data, f"profiles.{self.profile}.{key}"
            )
            if found_profile:
                # Callers fetch whole subtrees (`get("devices", {})`) and then
                # index into them. Returning the profile subtree wholesale would
                # drop every default the profile did not restate, so mappings
                # merge instead of replace.
                if isinstance(override, dict) and isinstance(base, dict):
                    return _deep_merge(base, override)
                return override

        return base if found_default else default

    @staticmethod
    def _dig(data: Any, dotted: str) -> tuple[bool, Any]:
        current = data
        for part in dotted.split("."):
            if not isinstance(current, dict) or part not in current:
                return False, None
            current = current[part]
        return True, current

    def __repr__(self) -> str:
        return f"ConfigManager(profile={self.profile!r}, path={str(self.config_path)!r})"
