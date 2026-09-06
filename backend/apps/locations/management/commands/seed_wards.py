import json

from django.core.management.base import BaseCommand

from apps.locations.models import Ward


class Command(BaseCommand):
    help = "Seed all Tanzania wards (kata) from data/tanzania_wards.json"

    def handle(self, *args, **options):
        from django.conf import settings

        from apps.locations.models import Region, District

        path = settings.WARDS_DATA_FILE
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)

        loc_path = settings.LOCATIONS_DATA_FILE
        with open(loc_path, encoding="utf-8") as fh:
            loc_payload = json.load(fh)

        # Build mapping of index (1-based) to District UUID
        district_mapping = {}
        idx = 1
        for r_item in loc_payload["regions"]:
            region = Region.objects.get(name=r_item["name"])
            for d_name in r_item["districts"]:
                dist = District.objects.get(name=d_name, region=region)
                district_mapping[idx] = dist.id
                idx += 1

        created = 0
        for item in payload["wards"]:
            dist_uuid = district_mapping.get(item["district_id"])
            if not dist_uuid:
                continue
            _, was_created = Ward.objects.get_or_create(
                name=item["name"], district_id=dist_uuid
            )
            if was_created:
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded wards: {created} new "
                f"(total {Ward.objects.count()} wards across "
                f"{Ward.objects.values('district_id').distinct().count()} districts)."
            )
        )