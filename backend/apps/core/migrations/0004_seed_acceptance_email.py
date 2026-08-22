from django.db import migrations


def seed_acceptance_email(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    NotificationTemplate.objects.update_or_create(
        key="acceptance_email",
        defaults={
            "name": "Acceptance Email",
            "channel": "EMAIL",
            "trigger_label": "Company Acceptance",
            "subject": "Congratulations! You have been accepted — {slot_title}",
            "description": "Email confirming acceptance and asking the student to open their email for the acceptance letter.",
            "body": (
                "Dear {student_name},\n\n"
                "Congratulations! Your application (ID: #{app_id}) for '{slot_title}' "
                "at {company_name} has been accepted.\n\n"
                "Please open your email and check for the official acceptance letter and "
                "further instructions from the company.\n\n"
                "{company_message}\n\n"
                "Best regards,\n{company_name}"
            ),
        },
    )


def remove_acceptance_email(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    NotificationTemplate.objects.filter(key="acceptance_email").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0002_notificationtemplate"),
        ("core", "0003_seed_admin_console"),
    ]

    operations = [
        migrations.RunPython(seed_acceptance_email, remove_acceptance_email),
    ]