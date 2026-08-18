"""Allure attachment helpers."""

from __future__ import annotations

import logging
from pathlib import Path

import allure

logger = logging.getLogger(__name__)


def attach_as_jpeg(path: str | Path | None, name: str, quality: int = 80) -> None:
    """Attach an image file to Allure as JPEG.

    PNG screenshots are large (often ~500KB–1MB each) and slow the Allure
    report UI when many are attached. Converting to JPEG at quality 80
    shrinks them by roughly 5–10× with no perceptible loss for screenshots.

    Silently no-ops if `path` is empty, missing, or Pillow is unavailable
    (tests should never fail because of a reporting helper).

    Args:
        path: Path to a PNG/JPEG file on disk (or None — no-op)
        name: Display name in the Allure report
        quality: JPEG quality 1–95. 80 is a good balance for screenshots.
    """
    if not path:
        return
    src = Path(str(path))
    if not src.exists():
        logger.warning("attach_as_jpeg: file not found: %s", src)
        return

    try:
        from PIL import Image  # local import — optional dep at runtime
    except ImportError:
        # Fallback: attach the original file without conversion.
        logger.warning("Pillow not installed — attaching original file")
        allure.attach.file(str(src), name=name, attachment_type=allure.attachment_type.PNG)
        return

    # Write JPEG next to the source PNG, then use allure.attach.file (the
    # file-path based API is the one Allure reliably surfaces in the report).
    try:
        jpeg_path = src.with_suffix(".jpg")
        with Image.open(src) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(jpeg_path, format="JPEG", quality=quality, optimize=True)
        allure.attach.file(
            str(jpeg_path),
            name=name,
            attachment_type=allure.attachment_type.JPG,
            extension="jpg",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("attach_as_jpeg failed for %s: %s — falling back to PNG", src, exc)
        try:
            allure.attach.file(str(src), name=name, attachment_type=allure.attachment_type.PNG)
        except Exception:  # noqa: BLE001
            pass
