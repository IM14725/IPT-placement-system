from django.core.management.base import BaseCommand

from apps.students.universities import seed_universities


class Command(BaseCommand):
    help = "Create the approved university reference list from data/tanzania_institutions.json."

    def handle(self, *args, **options):
        created = seed_universities()
        self.stdout.write(self.style.SUCCESS(f"Universities created: {created}"))
