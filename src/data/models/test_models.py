"""
test_models.py — the records a run produces and the storage layer persists.

These are plain dataclasses rather than an ORM. A test run is written once and
read back for reporting, so there is nothing to gain from mapping objects to
rows lazily, and a dataclass keeps the shape obvious to anyone reading a
fixture.

``status`` and ``platform`` are enums with string values because they cross a
process boundary into PostgreSQL. Storing ``status.value`` means the column
holds ``"passed"``, which is greppable in a database console; storing an int
means the next person has to find this file to read their own data.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TestStatus(str, Enum):
    """Outcome of a test or suite.

    Inherits from ``str`` so comparisons against a plain string work and JSON
    serialisation needs no encoder.
    """

    # These names begin with "Test", so pytest tries to collect them as test
    # classes and warns on every run. They are records, not tests.
    __test__ = False

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"
    RUNNING = "running"
    BLOCKED = "blocked"


class Platform(str, Enum):
    """Target platform for a run."""

    ANDROID = "android"
    IOS = "ios"
    WEB = "web"


@dataclass
class Screenshot:
    """An image captured during a test.

    ``data`` holds a base64 string when the image is stored inline; ``path``
    holds a location when it is written to disk or an artifact store instead.
    Both are optional because which one is used depends on how the run was
    configured, and a failure screenshot is worth keeping even if only one
    survived.

    ``data`` is str rather than bytes on purpose: the storage layer writes it to
    a TEXT column, and psycopg2 adapts bytes to bytea, which PostgreSQL then
    refuses for that column and fails the whole transaction. Use
    ``Screenshot.from_bytes`` rather than assigning raw bytes.
    """

    filename: str
    path: str | None = None
    data: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_bytes(cls, filename: str, raw: bytes, **kwargs: Any) -> Screenshot:
        """Build one from raw image bytes, encoding for text storage."""
        return cls(filename=filename, data=base64.b64encode(raw).decode("ascii"), **kwargs)

    def to_bytes(self) -> bytes | None:
        """Decode the inline payload back to image bytes.

        ``None`` means no inline payload. An empty payload is a real value and
        round-trips to ``b""``, so the check is against None rather than
        falsiness. Decoding validates, because b64decode otherwise discards
        characters it does not recognise and hands back a silently truncated
        image.
        """
        if self.data is None:
            return None
        return base64.b64decode(self.data, validate=True)


@dataclass
class TestResult:
    """One executed test."""

    # These names begin with "Test", so pytest tries to collect them as test
    # classes and warns on every run. They are records, not tests.
    __test__ = False

    test_name: str
    platform: Platform
    status: TestStatus
    suite_id: str | None = None
    device_id: str | None = None
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    duration: float | None = None
    error_message: str | None = None
    stack_trace: str | None = None
    retry_count: int = 0
    test_data: dict[str, Any] | None = None
    logs: list[dict[str, Any]] = field(default_factory=list)
    screenshots: list[Screenshot] = field(default_factory=list)

    def mark_finished(self, status: TestStatus) -> None:
        """Close out a result, computing duration from the timestamps.

        Duration is derived rather than passed in so it cannot disagree with
        start_time and end_time, which is the usual way these three drift apart.
        """
        self.status = status
        self.end_time = datetime.now()
        self.duration = (self.end_time - self.start_time).total_seconds()


@dataclass
class TestSuite:
    """A group of tests executed together against one device."""

    # These names begin with "Test", so pytest tries to collect them as test
    # classes and warns on every run. They are records, not tests.
    __test__ = False

    suite_name: str
    platform: Platform
    status: TestStatus = TestStatus.RUNNING
    description: str | None = None
    device_id: str | None = None
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    config: dict[str, Any] = field(default_factory=dict)
    results: list[TestResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status is TestStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status is TestStatus.FAILED)

    def mark_finished(self) -> None:
        """A suite fails if any test in it failed or errored."""
        self.end_time = datetime.now()
        bad = {TestStatus.FAILED, TestStatus.ERROR}
        self.status = (
            TestStatus.FAILED
            if any(r.status in bad for r in self.results)
            else TestStatus.PASSED
        )
