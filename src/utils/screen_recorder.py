"""Chunked ADB screen recorder for local devices/emulators.

Why chunks rather than one long `screenrecord`:
    `adb shell screenrecord` writes an mp4 whose moov atom is only finalised
    when the process exits on its own (via --time-limit). Killing it early --
    `proc.terminate()`, or SIGINT to the on-device pid -- leaves a truncated
    file that reports no duration and will not play. Streaming raw H.264 out
    of `adb exec-out` avoids the container problem but screenrecord buffers
    stdout and drops nearly every frame, so that path is unusable too.

    So: record a sequence of short chunks, each of which exits naturally and
    is therefore always valid, then concatenate them with ffmpeg. Stopping
    costs at most one chunk length of waiting.

Local devices only. Cloud providers (LambdaTest) record server-side.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEVICE_DIR = "/sdcard/qa_rec"
DEFAULT_CHUNK_SECONDS = 10
DEFAULT_SIZE = "720x1280"
DEFAULT_BITRATE = "4000000"
# screenrecord refuses time limits above 180s
MAX_CHUNK_SECONDS = 180


def _safe_name(name: str) -> str:
    """Filesystem-safe slug for a campaign name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")[:120]


class ScreenRecorder:
    """Record a device screen as a series of naturally-terminating chunks."""

    def __init__(
        self,
        device_id: str,
        output_dir: str = "logs/videos",
        chunk_seconds: int = DEFAULT_CHUNK_SECONDS,
        size: str = DEFAULT_SIZE,
        bitrate: str = DEFAULT_BITRATE,
    ):
        self.device_id = device_id
        self.output_dir = Path(output_dir)
        self.chunk_seconds = min(chunk_seconds, MAX_CHUNK_SECONDS)
        self.size = size
        self.bitrate = bitrate
        self._proc: subprocess.Popen | None = None
        self._chunk_index = 0
        self._started = False

    # ── adb plumbing ──

    def _adb(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        cmd = ["adb", "-s", self.device_id, *args]
        return subprocess.run(cmd, capture_output=True, text=True, **kwargs)

    def _device_chunk_path(self, index: int) -> str:
        return f"{DEVICE_DIR}/chunk_{index:04d}.mp4"

    def _spawn_chunk(self) -> None:
        path = self._device_chunk_path(self._chunk_index)
        self._proc = subprocess.Popen(
            [
                "adb",
                "-s",
                self.device_id,
                "shell",
                "screenrecord",
                "--size",
                self.size,
                "--bit-rate",
                self.bitrate,
                "--time-limit",
                str(self.chunk_seconds),
                path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # ── lifecycle ──

    def start(self) -> bool:
        if shutil.which("adb") is None:
            logger.warning("adb not on PATH — screen recording disabled")
            return False
        self._adb("shell", "rm", "-rf", DEVICE_DIR, timeout=10)
        self._adb("shell", "mkdir", "-p", DEVICE_DIR, timeout=10)
        self._chunk_index = 0
        self._spawn_chunk()
        self._started = True
        logger.info("🎥 Recording started (%ds chunks) on %s", self.chunk_seconds, self.device_id)
        return True

    def poll(self) -> None:
        """Start the next chunk if the current one has finished.

        Call periodically. Not required for correctness of the final video --
        stop() rolls over any gap -- but keeps chunks contiguous.
        """
        if self._started and self._proc and self._proc.poll() is not None:
            self._chunk_index += 1
            self._spawn_chunk()

    def stop(self, name: str) -> str | None:
        """Finish the in-flight chunk, pull everything, concat to one mp4.

        Waits up to one chunk length for the current chunk to exit naturally --
        killing it would corrupt that chunk.

        Returns the saved path, or None if nothing usable was recorded.
        """
        if not self._started:
            return None
        self._started = False

        if self._proc and self._proc.poll() is None:
            try:
                self._proc.wait(timeout=self.chunk_seconds + 5)
            except subprocess.TimeoutExpired:
                # Chunk overran its own limit: kill it and drop it. Every earlier
                # chunk is still valid, so we lose the tail, not the recording.
                self._proc.kill()
                self._adb("shell", "rm", "-f", self._device_chunk_path(self._chunk_index))
                logger.warning("Final chunk overran and was discarded")
        self._proc = None
        time.sleep(0.5)  # let the last chunk's moov land on disk

        self.output_dir.mkdir(parents=True, exist_ok=True)
        staging = self.output_dir / f".{_safe_name(name)}_chunks"
        staging.mkdir(parents=True, exist_ok=True)

        pulled = self._pull_chunks(staging)
        if not pulled:
            logger.warning("No video chunks recovered from device")
            shutil.rmtree(staging, ignore_errors=True)
            return None

        out_path = self.output_dir / f"{_safe_name(name)}.mp4"
        ok = self._concat(pulled, out_path)

        shutil.rmtree(staging, ignore_errors=True)
        self._adb("shell", "rm", "-rf", DEVICE_DIR, timeout=10)

        if not ok:
            return None
        size_kb = out_path.stat().st_size // 1024
        logger.info("🎥 Recording saved: %s (%d KB)", out_path, size_kb)
        return str(out_path)

    # ── helpers ──

    def _pull_chunks(self, staging: Path) -> list[Path]:
        listing = self._adb("shell", "ls", DEVICE_DIR, timeout=15).stdout.split()
        chunks = sorted(n.strip() for n in listing if n.strip().endswith(".mp4"))
        pulled: list[Path] = []
        for chunk in chunks:
            local = staging / chunk
            self._adb("pull", f"{DEVICE_DIR}/{chunk}", str(local), timeout=60)
            # A valid chunk always has a readable duration; a truncated one does not.
            if local.exists() and local.stat().st_size > 1024 and _has_duration(local):
                pulled.append(local)
            else:
                logger.warning("Discarding unusable chunk %s", chunk)
        return pulled

    def _concat(self, chunks: list[Path], out_path: Path) -> bool:
        if shutil.which("ffmpeg") is None:
            # No ffmpeg: keep the first chunk rather than nothing at all.
            logger.warning("ffmpeg not found — saving first chunk only")
            shutil.copy(chunks[0], out_path)
            return True

        if len(chunks) == 1:
            shutil.copy(chunks[0], out_path)
            return True

        list_file = chunks[0].parent / "concat.txt"
        list_file.write_text("".join(f"file '{c.resolve()}'\n" for c in chunks))
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning("ffmpeg concat failed: %s", result.stderr[:200])
            return False
        return True


def _has_duration(path: Path) -> bool:
    """True when ffprobe reports a real duration (i.e. the mp4 was finalised)."""
    if shutil.which("ffprobe") is None:
        return True  # can't verify; assume usable
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    value = result.stdout.strip()
    try:
        return float(value) > 0
    except ValueError:
        return False
