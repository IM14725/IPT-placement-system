import io

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from PIL import Image

from apps.core.thumbnails import thumbnail_url


class _Field:
    """Minimal stand-in for an ImageFieldFile."""

    def __init__(self, storage, name):
        self.storage, self.name = storage, name

    def __bool__(self):
        return bool(self.name)

    @property
    def url(self):
        return self.storage.url(self.name)


def _jpeg(size=(1200, 800), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def media(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.MEDIA_URL = "/media/"
    from django.core.files.storage import default_storage
    from django.utils.functional import empty

    storage = FileSystemStorage(location=str(tmp_path), base_url="/media/")
    # thumbnail_url builds URLs through default_storage; point it at the temp dir too
    default_storage._wrapped = storage
    yield storage
    default_storage._wrapped = empty  # re-resolve from settings for later tests


def test_thumbnail_is_small_square_webp_and_original_untouched(media, tmp_path):
    name = media.save("profile_photos/me.jpg", ContentFile(_jpeg()))
    field = _Field(media, name)
    before = (tmp_path / name).read_bytes()

    url = thumbnail_url(field, 96)

    assert url.startswith("/media/thumbs/96/") and url.endswith(".webp")
    thumb_path = tmp_path / url.removeprefix("/media/")
    with Image.open(thumb_path) as im:
        assert im.size == (96, 96)
        assert im.format == "WEBP"
    assert thumb_path.stat().st_size < len(before)
    assert (tmp_path / name).read_bytes() == before  # original never modified


def test_thumbnail_is_cached(media, tmp_path):
    name = media.save("profile_photos/me.jpg", ContentFile(_jpeg()))
    field = _Field(media, name)
    first = thumbnail_url(field, 96)
    thumb_path = tmp_path / first.removeprefix("/media/")
    mtime = thumb_path.stat().st_mtime_ns
    assert thumbnail_url(field, 96) == first
    assert thumb_path.stat().st_mtime_ns == mtime  # not regenerated


def test_missing_or_corrupt_file_falls_back_to_original_url(media):
    assert thumbnail_url(None, 96) == ""
    missing = _Field(media, "profile_photos/gone.jpg")
    assert thumbnail_url(missing, 96) == "/media/profile_photos/gone.jpg"
    name = media.save("profile_photos/bad.jpg", ContentFile(b"not an image"))
    assert thumbnail_url(_Field(media, name), 96) == f"/media/{name}"
