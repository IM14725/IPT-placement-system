import json
import uuid

import django.core.validators
import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


def _seed_rows():
    try:
        with open(settings.INSTITUTIONS_DATA_FILE, encoding="utf-8") as fh:
            return json.load(fh)["institutions"]
    except (OSError, KeyError, ValueError):
        return []


def link_existing_universities(apps, schema_editor):
    """Seed the reference list, then point each profile at a record.

    Free-text values that match a known name, abbreviation or alias reuse the approved
    record. Anything else becomes a PENDING record suggested by that student, so admins
    can review it instead of the value being lost.
    """
    University = apps.get_model("students", "University")
    StudentProfile = apps.get_model("students", "StudentProfile")

    by_key = {}
    for row in _seed_rows():
        name = " ".join((row.get("name") or "").split())
        if not name:
            continue
        uni = University.objects.filter(name__iexact=name).first()
        if uni is None:
            uni = University.objects.create(
                name=name[:200],
                abbreviation=(row.get("abbreviation") or "")[:20],
                verification_status="APPROVED",
            )
        keys = [name, row.get("abbreviation") or ""] + list(row.get("aliases") or [])
        for key in keys:
            key = " ".join(key.split()).lower()
            if key:
                by_key.setdefault(key, uni)

    for profile in StudentProfile.objects.exclude(university=""):
        raw = " ".join((profile.university or "").split())
        if not raw:
            continue
        uni = by_key.get(raw.lower()) or University.objects.filter(name__iexact=raw).first()
        if uni is None:
            uni = University.objects.create(
                name=raw[:200],
                verification_status="PENDING",
                suggested_by_id=profile.user_id,
            )
            by_key[raw.lower()] = uni
        StudentProfile.objects.filter(pk=profile.pk).update(university_new=uni)


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0004_studentprofile_reapplication_credit"),
        ("locations", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="University",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=200)),
                ("abbreviation", models.CharField(blank=True, max_length=20)),
                (
                    "institution_type",
                    models.CharField(
                        choices=[("UNIVERSITY", "University"), ("COLLEGE", "College"), ("INSTITUTE", "Institute")],
                        default="UNIVERSITY",
                        max_length=12,
                    ),
                ),
                ("website", models.URLField(blank=True)),
                (
                    "verification_status",
                    models.CharField(
                        choices=[("PENDING", "Pending"), ("APPROVED", "Approved"), ("REJECTED", "Rejected")],
                        default="APPROVED",
                        max_length=10,
                    ),
                ),
                (
                    "region",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="universities",
                        to="locations.region",
                    ),
                ),
                (
                    "suggested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="suggested_universities",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"verbose_name_plural": "universities", "ordering": ["name"]},
        ),
        migrations.AddConstraint(
            model_name="university",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("name"), name="uniq_university_name_ci"
            ),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="university_new",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="students",
                to="students.university",
            ),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="graduation_year",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Expected year of graduation.",
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(2000),
                    django.core.validators.MaxValueValidator(2100),
                ],
            ),
        ),
        migrations.RunPython(link_existing_universities, migrations.RunPython.noop),
        migrations.RemoveField(model_name="studentprofile", name="university"),
        migrations.RenameField(model_name="studentprofile", old_name="university_new", new_name="university"),
        migrations.AlterField(
            model_name="studentprofile",
            name="university",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional. Pick your university, or choose 'not listed' to suggest it.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="students",
                to="students.university",
            ),
        ),
    ]
