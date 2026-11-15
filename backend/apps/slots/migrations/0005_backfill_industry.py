from django.db import migrations


def backfill_industry(apps, schema_editor):
    """Slots posted before the form had an industry field have it blank: use the organization's."""
    Slot = apps.get_model("slots", "Slot")
    for slot in Slot.objects.filter(industry="").select_related("organization"):
        industry = (slot.organization.industry or "").strip()[:150]
        if industry:
            Slot.objects.filter(pk=slot.pk).update(industry=industry)


class Migration(migrations.Migration):
    dependencies = [
        ("slots", "0004_slot_application_deadline"),
    ]

    operations = [
        migrations.RunPython(backfill_industry, migrations.RunPython.noop),
    ]
