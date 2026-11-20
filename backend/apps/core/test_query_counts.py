"""N+1 guard: the busy list/dashboard pages must run the same number of queries whether they show
a few rows or many. Each page is rendered with a small data set, then again after adding more rows;
a page that loops over related objects without select_related/prefetch_related grows with the data."""

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.companies.test_dashboard_features import _client, _make_org, _paid, _slot
from apps.locations.models import District, Region
from apps.notifications.models import Notification, NotificationType


def _count(client, url):
    client.get(url)  # warm per-process caches (content types, permissions, template loaders)
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200, url
    return len(ctx)


@pytest.fixture
def district(db):
    return District.objects.create(name="Kinondoni", region=Region.objects.create(name="Dar", slug="dar"))


@pytest.mark.django_db
def test_organization_pages_do_not_grow_with_applicants(district):
    org, owner = _make_org("Scale Ltd", "scale")
    slots = [_slot(org, district, f"Slot {i}", capacity=20) for i in range(2)]
    for i in range(2):
        _paid(slots[0], f"a{i}@x.tz")
    client = _client(owner)
    urls = [
        reverse("organization-dashboard"),
        reverse("organization-slots"),
        reverse("organization-all-applicants"),
        reverse("organization-export-applicants"),
        reverse("organization-applicants", args=[slots[0].id]),
    ]
    before = {u: _count(client, u) for u in urls}

    for i in range(2, 6):
        _paid(slots[i % 2], f"a{i}@x.tz")
    for i in range(3):
        _slot(org, district, f"Extra {i}")
    after = {u: _count(client, u) for u in urls}

    for u in urls:
        assert after[u] <= before[u], f"{u} ran {before[u]} queries for 2 applicants but {after[u]} for 6"


@pytest.mark.django_db
def test_notification_pages_do_not_grow_with_rows(district):
    org, owner = _make_org("Notify Ltd", "notify")

    def add(n):
        for i in range(n):
            Notification.objects.create(
                user=owner, type=NotificationType.SYSTEM, title=f"t{i}", body="b", link="/x/", is_read=bool(i % 2)
            )

    client = Client()
    client.force_login(owner)
    urls = [reverse("notifications"), reverse("notifications-summary")]
    add(3)
    before = {u: _count(client, u) for u in urls}
    add(10)
    after = {u: _count(client, u) for u in urls}
    for u in urls:
        assert after[u] <= before[u], f"{u}: {before[u]} -> {after[u]} queries"
