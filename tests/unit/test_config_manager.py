"""
Regression tests for ConfigManager and the run-record models.

Every case here corresponds to a defect found in review rather than to a
requirement invented afterwards. That is deliberate: a test that encodes a real
failure is worth more than one that restates the implementation.
"""

from __future__ import annotations

import argparse
import base64
import pathlib
import textwrap

import pytest

from src.data.models.test_models import Platform, Screenshot, TestResult, TestStatus, TestSuite
from src.data.parsers.config_manager import ConfigManager

pytestmark = pytest.mark.unit


CONFIG = textwrap.dedent(
    """
    provider:
      name: local
      hub_url: "http://127.0.0.1:4723"
    devices:
      android:
        primary_device_id: emulator-5554
        secondary_device_id: emulator-5556
    orchestration:
      enable_multi_device: false
      device_count: 1
    profiles:
      lambdatest:
        provider:
          name: cloud
    """
)


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG)
    return path


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """The cache is class-level, so it leaks across tests unless cleared."""
    for var in ("RUN_MODE", "TEST_PROFILE", "CONFIG_FILE", "ANDROID_SERIAL",
                "DEVICE_COUNT", "DEVICE_PROVIDER", "SLACK_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    ConfigManager.clear_cache()
    yield
    ConfigManager.clear_cache()


class TestLookup:
    def test_reads_defaults(self, config_file):
        cfg = ConfigManager(config_path=config_file)
        assert cfg.get("provider.name") == "local"
        assert cfg.get("devices.android.primary_device_id") == "emulator-5554"

    def test_missing_key_returns_default(self, config_file):
        assert ConfigManager(config_path=config_file).get("nope.nope", "fb") == "fb"


class TestProfileSelection:
    def test_run_mode_selects_profile(self, config_file, monkeypatch):
        """conftest sets RUN_MODE, not TEST_PROFILE. Honouring only the latter
        meant a cloud run silently executed against the local defaults."""
        monkeypatch.setenv("RUN_MODE", "lambdatest")
        cfg = ConfigManager(config_path=config_file)
        assert cfg.profile == "lambdatest"
        assert cfg.get("provider.name") == "cloud"

    def test_run_mode_local_means_no_profile(self, config_file, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "local")
        assert ConfigManager(config_path=config_file).profile is None

    def test_explicit_profile_beats_env(self, config_file, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "local")
        assert ConfigManager(profile="lambdatest", config_path=config_file).profile == "lambdatest"


class TestProfileMerge:
    def test_mapping_lookup_merges_over_defaults(self, config_file, monkeypatch):
        """Callers fetch a subtree and index into it. Returning the profile's
        subtree wholesale dropped every default it did not restate."""
        monkeypatch.setenv("RUN_MODE", "lambdatest")
        provider = ConfigManager(config_path=config_file).get("provider", {})
        assert provider["name"] == "cloud"          # overridden
        assert provider["hub_url"] == "http://127.0.0.1:4723"   # preserved

    def test_untouched_subtree_survives(self, config_file, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "lambdatest")
        devices = ConfigManager(config_path=config_file).get("devices", {})
        assert devices["android"]["secondary_device_id"] == "emulator-5556"


class TestEnvOverrides:
    def test_string_identifier_is_not_coerced(self, config_file, monkeypatch):
        """A zero-prefixed serial parsed as an int addresses the wrong device."""
        monkeypatch.setenv("ANDROID_SERIAL", "001234")
        got = ConfigManager(config_path=config_file).get("devices.android.primary_device_id")
        assert got == "001234"
        assert isinstance(got, str)

    def test_numeric_key_is_coerced(self, config_file, monkeypatch):
        monkeypatch.setenv("DEVICE_COUNT", "4")
        got = ConfigManager(config_path=config_file).get("orchestration.device_count")
        assert got == 4 and isinstance(got, int)

    def test_boolean_key_is_coerced(self, config_file, monkeypatch):
        monkeypatch.setenv("SLACK_ENABLED", "true")
        assert ConfigManager(config_path=config_file).get("reporting.slack.enabled") is True

    def test_env_beats_profile(self, config_file, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "lambdatest")
        monkeypatch.setenv("DEVICE_PROVIDER", "browserstack")
        assert ConfigManager(config_path=config_file).get("provider.name") == "browserstack"


class TestMissingFiles:
    def test_explicit_missing_path_raises(self, tmp_path):
        """Silently loading {} let a typo disable database writes for a whole run."""
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            ConfigManager(config_path=tmp_path / "absent.yaml")

    def test_removed_file_raises_despite_cache(self, config_file):
        """Checked before the cache, so a deleted file cannot return stale data."""
        assert ConfigManager(config_path=config_file).get("provider.name") == "local"
        config_file.unlink()
        with pytest.raises(FileNotFoundError):
            ConfigManager(config_path=config_file)

    def test_discovery_degrades_quietly(self, monkeypatch, tmp_path):
        """Nothing named and nothing found is not an error."""
        monkeypatch.chdir(tmp_path)
        assert ConfigManager().get("anything", "fallback") == "fallback"


class TestFromCliArgs:
    def test_reads_pytest_test_config(self, config_file):
        ns = argparse.Namespace(test_config=str(config_file), profile=None)
        assert ConfigManager.from_cli_args(ns).get("provider.name") == "local"

    def test_unset_cli_default_does_not_raise(self, tmp_path):
        """An unsupplied option must fall back to discovery, not raise."""
        ns = argparse.Namespace(test_config=None, profile=None)
        cfg = ConfigManager.from_cli_args(ns)
        assert cfg.get("provider.name", "fallback") in {"fallback", "local"}

    def test_pytest_option_default_is_none(self):
        """Guards the actual regression rather than a hand-built namespace.

        --test-config previously defaulted to "settings/config.yaml". Because
        pytest always supplies a default, ConfigManager saw an explicitly named
        path that does not exist in this repo and raised on every run. A test
        that builds its own Namespace cannot catch that coming back, so read
        the real conftest and assert the default.

        Parsed rather than imported: importing conftest pulls in appium, allure
        and the whole driver stack, which a unit test should not require.
        """
        import ast

        root = pathlib.Path(__file__).resolve().parents[2]
        tree = ast.parse((root / "conftest.py").read_text(encoding="utf-8"))

        defaults = [
            kw.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and any(
                isinstance(a, ast.Constant) and a.value == "--test-config"
                for a in node.args
            )
            for kw in node.keywords
            if kw.arg == "default"
        ]

        assert defaults, "--test-config option not found in conftest.py"
        for default in defaults:
            assert isinstance(default, ast.Constant) and default.value is None, (
                "--test-config must default to None. A concrete default makes "
                "every run look like an explicitly requested config path."
            )


class TestScreenshot:
    def test_round_trips(self):
        raw = b"\x89PNG\r\n\x1a\n"
        shot = Screenshot.from_bytes("f.png", raw)
        assert isinstance(shot.data, str)   # a TEXT column rejects bytea
        assert shot.to_bytes() == raw

    def test_empty_payload_is_not_absent(self):
        """b"" is a real payload; conflating it with None loses the distinction."""
        assert Screenshot.from_bytes("f.png", b"").to_bytes() == b""

    def test_absent_payload_is_none(self):
        assert Screenshot(filename="f.png", path="/tmp/f.png").to_bytes() is None

    def test_corrupt_payload_raises(self):
        """Without validation b64decode discards junk and returns a truncated image."""
        with pytest.raises(base64.binascii.Error):
            Screenshot(filename="f.png", data="not valid base64!!").to_bytes()


class TestRunRecords:
    def test_result_duration_is_derived(self):
        result = TestResult(test_name="t", platform=Platform.ANDROID, status=TestStatus.RUNNING)
        result.mark_finished(TestStatus.PASSED)
        assert result.status is TestStatus.PASSED
        assert result.end_time is not None and result.duration >= 0

    def test_suite_fails_if_any_test_failed(self):
        suite = TestSuite(suite_name="s", platform=Platform.ANDROID)
        suite.results = [
            TestResult(test_name="a", platform=Platform.ANDROID, status=TestStatus.PASSED),
            TestResult(test_name="b", platform=Platform.ANDROID, status=TestStatus.FAILED),
        ]
        suite.mark_finished()
        assert suite.status is TestStatus.FAILED
        assert (suite.passed, suite.failed) == (1, 1)

    def test_enum_values_are_readable_in_the_database(self):
        assert TestStatus.PASSED.value == "passed"
        assert Platform.ANDROID.value == "android"
