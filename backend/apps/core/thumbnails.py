"""Small square WebP thumbnails for uploaded photos.

Avatars and ID-card previews are shown at 40-80 px, but the uploaded originals are up to 2 MB. On slow
mobile networks that made the profile and applicant pages crawl. ``thumbnail_url`` returns the URL of a
small derived copy instead, and the original file is never touched or replaced.

* Created lazily on first use and cached under ``MEDIA_ROOT/thumbs/<size>/``, so photos uploaded before
  this feature work too. The cache key includes the source name and mtime, so a re-upload gets a new thumb.
* No model fields, migrations or DB writes are involved (verified-document and audit rows are untouched).
* Never raises: any problem (missing file, corrupt image, read-only disk) falls back to the original URL.
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

THUMB_QUALITY = 80


def _build(src_path: str, dest: Path, size: int) -> None:
    from PIL import Image, ImageOps

    with Image.open(src_path) as im:
        im.draft("RGB", (size * 2, size * 2))  # JPEG: decode at reduced scale, much faster
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        thumb = ImageOps.fit(im, (size, size), Image.LANCZOS, centering=(0.5, 0.4))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{uuid.uuid4().hex}.tmp")
    try:
        thumb.save(tmp, "WEBP", quality=THUMB_QUALITY, method=4)
        os.replace(tmp, dest)  # atomic: concurrent requests never see a half-written file
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def thumbnail_url(fieldfile, size: int = 96) -> str:
    """URL of a ``size`` x ``size`` thumbnail of ``fieldfile`` (an ImageField value), else the original URL."""
    if not fieldfile:
        return ""
    try:
        name = fieldfile.name
        src_path = fieldfile.storage.path(name)
        mtime = os.stat(src_path).st_mtime_ns
        key = hashlib.sha256(f"{name}|{mtime}|{size}".encode()).hexdigest()[:24]
        rel = f"thumbs/{size}/{key}.webp"
        dest = Path(settings.MEDIA_ROOT) / rel
        if not dest.exists():
            _build(src_path, dest, size)
        return default_storage.url(rel)
    except Exception:  # noqa: BLE001 - a thumbnail must never break a page
        logger.warning("thumbnail failed for %r; using the original", getattr(fieldfile, "name", fieldfile), exc_info=True)
        try:
            return fieldfile.url
        except Exception:  # noqa: BLE001
            return ""
