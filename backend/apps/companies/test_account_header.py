"""The "Your account" header at the top of the student and organization profile pages, and the organization logo."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.students.test_dashboard_features import _client, _reset_redis_state, _student, org, places  # noqa: F401


def _png(size=(40, 40), color=(30, 120, 200)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _header(body):
    start = body.index("data-account-header")
    return body[start : body.index("</section>", start)]


@pytest.mark.django_db
def test_student_profile_starts_with_the_account_header(places):
    student = _student(places)
    user = student.user
    user.phone, user.email_verified, user.phone_verified = "255712345678", True, False
    user.save()
    body = _client(user).get(reverse("student-profile")).content.decode()
    header = _header(body)
    # It sits above the form and the tabs, so users see their account before anything else.
    assert body.index("data-account-header") < body.index('data-panel="profile"')
    assert user.email in header and "255712345678" in header
    assert header.count("Verified") == 1 and header.count("Not verified") == 1  # email yes, phone no
    assert "Joined" in header and "Student account" in header
    assert 'href="#id_profile_photo"' in header  # the photo is changed in the profile form


@pytest.mark.django_db
def test_organization_profile_starts_with_the_account_header(org):
    user = org.user
    user.phone, user.email_verified, user.phone_verified = "", False, False
    user.save()
    body = _client(user).get(reverse("organization-profile")).content.decode()
    header = _header(body)
    assert "{#" not in body and "#}" not in body  # a multi-line {# #} comment once leaked onto the page
    assert body.index("data-account-header") < body.index('data-panel="profile"')
    assert org.name in header and user.email in header
    assert "Signed in as" in header and "No phone number added" in header
    assert "Not verified" in header  # the email
    assert "Organization pending verification" in header or "Organization verified" in header
    assert reverse("organization-upload-logo") in header  # a manager can set the logo


@pytest.mark.django_db
def test_organization_logo_upload_replace_and_remove(org, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    c = _client(org.user)
    url = reverse("organization-upload-logo")

    resp = c.post(url, {"logo": SimpleUploadedFile("logo.png", _png(), content_type="image/png")})
    assert resp.status_code == 302
    org.refresh_from_db()
    assert org.logo and org.logo.name.startswith("org_logos/")
    assert 'alt="Organization logo"' in _header(c.get(reverse("organization-profile")).content.decode())

    first = org.logo.name
    c.post(url, {"logo": SimpleUploadedFile("second.png", _png(color=(200, 30, 30)), content_type="image/png")})
    org.refresh_from_db()
    assert org.logo.name != first
    assert not (tmp_path / first).exists()  # the replaced file is not left behind

    assert c.post(url, {"action": "remove"}).status_code == 302
    org.refresh_from_db()
    assert not org.logo


@pytest.mark.django_db
def test_organization_logo_rejects_fake_and_oversized_images(org, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    c = _client(org.user)
    url = reverse("organization-upload-logo")
    # Not an image at all, and a script wearing an image name.
    c.post(url, {"logo": SimpleUploadedFile("logo.png", b"<script>alert(1)</script>", content_type="image/png")})
    # A real GIF is an image but not one of the allowed types.
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "GIF")
    c.post(url, {"logo": SimpleUploadedFile("logo.gif", buf.getvalue(), content_type="image/gif")})
    # Over the 2 MB limit.
    settings.MAX_UPLOAD_SIZE = 100
    c.post(url, {"logo": SimpleUploadedFile("big.png", _png((200, 200)), content_type="image/png")})
    org.refresh_from_db()
    assert not org.logo


@pytest.mark.django_db
def test_logo_upload_needs_login_and_post(client, org):
    assert client.post(reverse("organization-upload-logo")).status_code in (302, 403)
    assert _client(org.user).get(reverse("organization-upload-logo")).status_code == 403
