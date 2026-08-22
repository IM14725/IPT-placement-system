import pytest

from apps.locations.models import District, Region


@pytest.mark.django_db
def test_seed_creates_regions_and_districts():
    region = Region.objects.create(name="Dar es Salaam", slug="dar-es-salaam")
    District.objects.create(name="Kinondoni", region=region)
    District.objects.create(name="Ilala", region=region)
    assert Region.objects.count() == 1
    assert region.districts.count() == 2


@pytest.mark.django_db
def test_district_unique_per_region():
    region = Region.objects.create(name="Arusha", slug="arusha")
    District.objects.create(name="Meru", region=region)
    with pytest.raises(Exception):
        District.objects.create(name="Meru", region=region)