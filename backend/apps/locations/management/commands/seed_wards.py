import json

from django.core.management.base import BaseCommand

from apps.locations.models import Ward


class Command(BaseCommand):
    help = "Seed all Tanzania wards (kata) from data/tanzania_wards.json"

    def handle(self, *args, **options):
        from django.conf import settings

        path = settings.WARDS_DATA_FILE
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)

        created = 0
        for item in payload["wards"]:
            _, was_created = Ward.objects.get_or_create(
                name=item["name"], district_id=item["district_id"]
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