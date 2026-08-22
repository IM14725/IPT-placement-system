import json

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.locations.models import District, Region


class Command(BaseCommand):
    help = "Seed all Tanzania regions and districts from data/tanzania_locations.json"

    def handle(self, *args, **options):
        from django.conf import settings

        path = settings.LOCATIONS_DATA_FILE
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)

        created_regions = 0
        created_districts = 0
        for item in payload["regions"]:
            region, region_created = Region.objects.get_or_create(
                name=item["name"], defaults={"slug": slugify(item["name"])}
            )
            if region_created:
                created_regions += 1
            for district_name in item["districts"]:
                _, created = District.objects.get_or_create(
                    name=district_name, region=region
                )
                if created:
                    created_districts += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded locations: {created_regions} new regions, "
                f"{created_districts} new districts "
                f"(total {Region.objects.count()} regions / {District.objects.count()} districts)."
            )
        )