from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0002_alter_companyprofile_options_companyprofile_address_and_more"),
        ("locations", "0001_initial"),
        # slots.0001 creates Slot.company -> companies.CompanyProfile under the OLD model name, so it
        # must already be in the migration state when this rename runs (the rename then repoints it).
        # Without this edge the order was only right by accident of graph traversal, and a fresh
        # database (e.g. the test database) failed with "companies.companyprofile cannot be resolved".
        ("slots", "0002_remove_slot_level_slot_academic_year"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameModel(old_name="CompanyProfile", new_name="Organization"),
        migrations.AlterField(
            model_name="organization",
            name="user",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="owned_organization",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="organization",
            name="reviewed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reviewed_organizations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="organization",
            name="region",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="organizations",
                to="locations.region",
            ),
        ),
        migrations.AlterField(
            model_name="organization",
            name="district",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="organizations",
                to="locations.district",
            ),
        ),
        migrations.AlterField(
            model_name="organization",
            name="ward",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="organizations",
                to="locations.ward",
            ),
        ),
    ]
