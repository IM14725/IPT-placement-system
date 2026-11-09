from django.db import migrations, models


def company_to_organization(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(role="COMPANY").update(role="ORGANIZATION")


def organization_to_company(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(role="ORGANIZATION").update(role="COMPANY")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_super_admin_and_admin_role"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("STUDENT", "Student"),
                    ("ORGANIZATION", "Organization"),
                    ("ADMIN", "Platform Admin"),
                    ("SUPER_ADMIN", "Platform Super Admin"),
                ],
                default="STUDENT",
                max_length=20,
            ),
        ),
        migrations.RunPython(company_to_organization, organization_to_company),
    ]
