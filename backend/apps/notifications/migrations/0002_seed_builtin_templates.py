"""Preload the four system messages as editable templates (same wording the senders already use)."""

from django.db import migrations


def seed(apps, schema_editor):
    from apps.notifications.events import EVENTS

    Template = apps.get_model("notifications", "NotificationTemplate")
    for event in EVENTS.values():
        Template.objects.get_or_create(
            key=event.key,
            defaults={
                "name": event.label,
                "channel": event.channel,
                "trigger_label": event.trigger,
                "subject": event.default_subject,
                "body": event.default_body,
                "description": f"Sent when: {event.trigger.lower()}.",
                "is_active": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
