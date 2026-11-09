from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0002_alter_companyprofile_options_companyprofile_address_and_more"),
        ("locations", "0001_initial"),
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
