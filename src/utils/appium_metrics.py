"""
Appium Metrics Collection Utility

Tracks Appium-specific metrics:
- Command execution times
- Session information
- Element interaction metrics
- Network request tracking
- Device resource usage
"""

import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

import allure

logger = logging.getLogger(__name__)


@dataclass
class CommandMetrics:
    """Metrics for a single WebDriver command"""

    command_name: str
    execution_time: float
    success: bool
    error_message: str = ""
    timestamp: float = field(default_factory=time.time)
    args: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "command": self.command_name,
            "execution_time_seconds": round(self.execution_time, 3),
            "success": self.success,
            "error": self.error_message,
            "timestamp": self.timestamp,
        }


@dataclass
class ElementInteractionMetrics:
    """Metrics for element interactions"""

    element_locator: str
    interaction_type: str  # click, send_keys, find, etc.
    execution_time: float
    success: bool
    retry_count: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "locator": self.element_locator,
            "interaction": self.interaction_type,
            "execution_time_seconds": round(self.execution_time, 3),
            "success": self.success,
            "retry_count": self.retry_count,
            "timestamp": self.timestamp,
        }


@dataclass
class SessionMetrics:
    """Complete session metrics"""

    session_id: str | None = None
    platform: str | None = None
    device_id: str | None = None
    session_start_time: float = 0.0
    session_end_time: float = 0.0
    commands: list[CommandMetrics] = field(default_factory=list)
    element_interactions: list[ElementInteractionMetrics] = field(default_factory=list)
    slow_commands: list[CommandMetrics] = field(default_factory=list)  # Commands > 1s
    failed_commands: list[CommandMetrics] = field(default_factory=list)

    @property
    def session_duration(self) -> float:
        """Total session duration in seconds"""
        if self.session_end_time > 0:
            return self.session_end_time - self.session_start_time
        return time.time() - self.session_start_time

    @property
    def total_commands(self) -> int:
        """Total number of commands executed"""
        return len(self.commands)

    @property
    def average_command_time(self) -> float:
        """Average command execution time"""
        if not self.commands:
            return 0.0
        return sum(cmd.execution_time for cmd in self.commands) / len(self.commands)

    @property
    def slow_command_count(self) -> int:
        """Number of slow commands (>1s)"""
        return len(self.slow_commands)

    def to_dict(self) -> dict:
        """Convert to dictionary for reporting"""
        return {
            "session_id": self.session_id,
            "platform": self.platform,
            "device_id": self.device_id,
            "session_duration_seconds": round(self.session_duration, 3),
            "total_commands": self.total_commands,
            "average_command_time_seconds": round(self.average_command_time, 3),
            "slow_command_count": self.slow_command_count,
            "failed_command_count": len(self.failed_commands),
            "commands": [cmd.to_dict() for cmd in self.commands],
            "element_interactions": [inter.to_dict() for inter in self.element_interactions],
            "slow_commands": [cmd.to_dict() for cmd in self.slow_commands],
        }

    def attach_to_allure(self):
        """Attach metrics to Allure report"""
        import json

        metrics_dict = self.to_dict()

        # Attach as JSON
        allure.attach(
            json.dumps(metrics_dict, indent=2),
            name="📊 Appium Session Metrics",
            attachment_type=allure.attachment_type.JSON,
        )

        # Attach summary
        summary = self._generate_summary()
        allure.attach(
            summary,
            name="⏱️ Session Summary",
            attachment_type=allure.attachment_type.TEXT,
        )

    def _generate_summary(self) -> str:
        """Generate human-readable summary"""
        lines = [
            "Appium Session Metrics Summary",
            "=" * 60,
            f"Session ID: {self.session_id or 'N/A'}",
            f"Platform: {self.platform or 'N/A'}",
            f"Device ID: {self.device_id or 'N/A'}",
            f"Session Duration: {self.session_duration:.3f}s",
            "",
            "Command Statistics:",
            f"  Total Commands: {self.total_commands}",
            f"  Average Time: {self.average_command_time:.3f}s",
            f"  Slow Commands (>1s): {self.slow_command_count}",
            f"  Failed Commands: {len(self.failed_commands)}",
        ]

        if self.slow_commands:
            lines.append("")
            lines.append("Slow Commands:")
            for cmd in self.slow_commands[:10]:  # Top 10
                lines.append(f"  - {cmd.command_name}: {cmd.execution_time:.3f}s")

        return "\n".join(lines)


class AppiumMetricsCollector:
    """
    Collects Appium-specific metrics during test execution.

    Usage:
        collector = AppiumMetricsCollector(device_id="...", platform="android")
        collector.start_session(session_id="...")

        # Metrics are automatically collected via decorators
        # or manually:
        collector.record_command("find_element", 0.5, success=True)

        # At end of test:
        metrics = collector.finalize()
        metrics.attach_to_allure()
    """

    def __init__(self, device_id: str | None = None, platform: str | None = None):
        """
        Initialize metrics collector

        Args:
            device_id: Device identifier
            platform: Platform name (android/ios)
        """
        self.device_id = device_id
        self.platform = platform
        self.metrics = SessionMetrics(device_id=device_id, platform=platform)
        self.metrics.session_start_time = time.time()

        logger.info(f"📊 Appium Metrics Collector initialized for {platform} device: {device_id}")

    def start_session(self, session_id: str | None = None):
        """Mark session start"""
        self.metrics.session_id = session_id
        self.metrics.session_start_time = time.time()
        logger.info(f"📊 Session started: {session_id}")

    def record_command(
        self,
        command_name: str,
        execution_time: float,
        success: bool = True,
        error_message: str = "",
        **kwargs,
    ):
        """
        Record a WebDriver command execution

        Args:
            command_name: Name of the command (e.g., "find_element")
            execution_time: Execution time in seconds
            success: Whether command succeeded
            error_message: Error message if failed
            **kwargs: Additional command metadata
        """
        cmd_metrics = CommandMetrics(
            command_name=command_name,
            execution_time=execution_time,
            success=success,
            error_message=error_message,
            args=kwargs,
        )

        self.metrics.commands.append(cmd_metrics)

        # Track slow commands (>1s)
        if execution_time > 1.0:
            self.metrics.slow_commands.append(cmd_metrics)
            logger.warning(f"⚠️ Slow command detected: {command_name} took {execution_time:.3f}s")

        # Track failed commands
        if not success:
            self.metrics.failed_commands.append(cmd_metrics)
            logger.error(f"❌ Command failed: {command_name} - {error_message}")

    def record_element_interaction(
        self,
        locator: str,
        interaction_type: str,
        execution_time: float,
        success: bool = True,
        retry_count: int = 0,
    ):
        """
        Record an element interaction

        Args:
            locator: Element locator
            interaction_type: Type of interaction (click, send_keys, find, etc.)
            execution_time: Execution time in seconds
            success: Whether interaction succeeded
            retry_count: Number of retries
        """
        interaction = ElementInteractionMetrics(
            element_locator=locator,
            interaction_type=interaction_type,
            execution_time=execution_time,
            success=success,
            retry_count=retry_count,
        )

        self.metrics.element_interactions.append(interaction)

    def get_device_resources(self) -> dict[str, Any]:
        """
        Get current device resource usage

        Returns:
            Dictionary with memory, CPU, battery info
        """
        if not self.device_id or self.platform != "android":
            return {}

        try:
            resources = {}

            # Memory usage
            mem_cmd = f"adb -s {self.device_id} shell dumpsys meminfo {self.app_package}"
            result = subprocess.run(mem_cmd, shell=True, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                # Parse memory info (simplified - can be enhanced)
                resources["memory_dump"] = result.stdout[:500]  # First 500 chars

            # Battery level
            battery_cmd = f"adb -s {self.device_id} shell dumpsys battery | grep level"
            result = subprocess.run(
                battery_cmd, shell=True, capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                try:
                    level = int(result.stdout.split(":")[1].strip())
                    resources["battery_level"] = level
                except (ValueError, IndexError):
                    pass

            return resources

        except Exception as e:
            logger.warning(f"Failed to get device resources: {e}")
            return {}

    def finalize(self) -> SessionMetrics:
        """Finalize metrics collection"""
        self.metrics.session_end_time = time.time()

        logger.info("=" * 80)
        logger.info("📊 APPIUM SESSION METRICS SUMMARY")
        logger.info("=" * 80)
        logger.info(self.metrics._generate_summary())
        logger.info("=" * 80)

        return self.metrics


def time_appium_command(
    command_name: str | None = None,
    slow_threshold: float = 1.0,
    record_metrics: AppiumMetricsCollector | None = None,
):
    """
    Decorator to time Appium/WebDriver commands

    Usage:
        @time_appium_command("find_element", record_metrics=collector)
        def find_element(self, locator, by, timeout):
            # ... implementation
    """

    def decorator(func: Callable) -> Callable:
        cmd_name = command_name or func.__name__

        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            error_msg = ""

            try:
                result = func(*args, **kwargs)
                execution_time = time.time() - start_time

                # Record metrics if collector provided
                if record_metrics:
                    record_metrics.record_command(
                        command_name=cmd_name,
                        execution_time=execution_time,
                        success=True,
                        **kwargs,
                    )

                # Log slow commands
                if execution_time > slow_threshold:
                    logger.warning(
                        f"⚠️ Slow {cmd_name}: {execution_time:.3f}s "
                        f"(args: {kwargs.get('locator', 'N/A')})"
                    )

                return result

            except Exception as e:
                execution_time = time.time() - start_time
                error_msg = str(e)

                # Record failed command
                if record_metrics:
                    record_metrics.record_command(
                        command_name=cmd_name,
                        execution_time=execution_time,
                        success=False,
                        error_message=error_msg,
                        **kwargs,
                    )

                logger.error(f"❌ {cmd_name} failed after {execution_time:.3f}s: {error_msg}")
                raise

        return wrapper

    return decorator


# Convenience function for quick integration
def create_metrics_collector(
    device_id: str | None = None, platform: str | None = None
) -> AppiumMetricsCollector:
    """
    Create a metrics collector instance

    Args:
        device_id: Device identifier
        platform: Platform name

    Returns:
        AppiumMetricsCollector instance
    """
    return AppiumMetricsCollector(device_id=device_id, platform=platform)
