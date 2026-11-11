"""Login must stay fast: Argon2id hashing, no slow hash on the hot path, and no Redis/DB name-resolution stalls."""

import time

import pytest
from django.contrib.auth.hashers import check_password, make_password
from django.test import Client

from apps.accounts.models import User, UserRole


def test_new_passwords_use_tuned_argon2id():
    hashed = make_password("Secret123!x")
    assert hashed.startswith("argon2$argon2id$") and "m=19456,t=2,p=1" in hashed


def test_hashing_is_fast_enough_for_login():
    hashed = make_password("Secret123!x")
    start = time.perf_counter()
    assert check_password("Secret123!x", hashed)
    assert time.perf_counter() - start < 0.5


def test_old_pbkdf2_hashes_still_verify_and_are_upgraded(db):
    from django.contrib.auth.hashers import PBKDF2PasswordHasher

    hasher = PBKDF2PasswordHasher()
    user = User.objects.create_user(email="old@speed.tz", password="x", role=UserRole.STUDENT)
    user.password = hasher.encode("Secret123!x", hasher.salt(), iterations=1000)
    user.save(update_fields=["password"])
    assert user.check_password("Secret123!x")
    user.refresh_from_db()
    assert user.password.startswith("argon2$argon2id$")


def test_local_services_are_addressed_by_ip_not_localhost():
    """On Windows 'localhost' tries IPv6 first and every new connection waits seconds for the fallback."""
    from django.conf import settings

    for url in (settings.REDIS_URL, settings.CELERY_BROKER_URL, settings.CELERY_RESULT_BACKEND):
        assert "//localhost" not in url
    assert settings.DATABASES["default"]["HOST"] != "localhost"


def test_middleware_rewrites_only_ajax_post_redirects():
    from django.http import HttpResponse, HttpResponseRedirect
    from django.test import RequestFactory

    from apps.core.ajax import AjaxRedirectMiddleware

    mw = AjaxRedirectMiddleware(lambda request: HttpResponseRedirect("/student/dashboard/"))
    ajax = mw(RequestFactory().post("/x/", HTTP_X_REQUESTED_WITH="XMLHttpRequest"))
    assert ajax.status_code == 200 and ajax["X-Redirect-To"] == "/student/dashboard/" and not ajax.has_header("Location")
    normal = mw(RequestFactory().post("/x/"))
    assert normal.status_code == 302 and normal["Location"] == "/student/dashboard/"
    get = mw(RequestFactory().get("/x/", HTTP_X_REQUESTED_WITH="XMLHttpRequest"))
    assert get.status_code == 302
