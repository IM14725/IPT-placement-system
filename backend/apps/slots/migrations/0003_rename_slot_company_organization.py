from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("slots", "0002_remove_slot_level_slot_academic_year"),
        ("companies", "0003_rename_companyprofile_organization"),
    ]

    operations = [
        migrations.RenameField(model_name="slot", old_name="company", new_name="organization"),
    ]
