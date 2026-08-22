from django.db import migrations

DEFAULT_TEMPLATES = [
    {
        "key": "welcome_email",
        "name": "Welcome Email",
        "channel": "EMAIL",
        "trigger_label": "User Registration",
        "subject": "Welcome to IPT Marketplace, {student_name}!",
        "description": "Sent when a new student registers on the platform.",
        "body": (
            "Hi {student_name},\n\n"
            "Welcome to the IPT Marketplace! We are thrilled to have you on board.\n\n"
            "Your account has been successfully created. You can now log in and start "
            "browsing available training slots from top companies.\n\n"
            "Click here to log in: {login_url}\n\n"
            "If you have any questions, feel free to reach out to our support team at "
            "{support_email}.\n\n"
            "Best regards,\nThe IPT Marketplace Team"
        ),
    },
    {
        "key": "payment_received_sms",
        "name": "Payment Received",
        "channel": "SMS",
        "trigger_label": "Payment Verified",
        "subject": "",
        "description": "Confirmation sent upon successful payment for a training slot.",
        "body": (
            "Application Sent! App ID: #{app_id} | Company: {company_name} | "
            "Slot: {slot_title}. Track it on the IPT Marketplace."
        ),
    },
    {
        "key": "receipt_email",
        "name": "Payment Receipt Email",
        "channel": "EMAIL",
        "trigger_label": "Payment Verified",
        "subject": "Payment Receipt - {reference_id}",
        "description": "Email receipt with the PDF attachment sent after a verified payment.",
        "body": (
            "Dear {student_name},\n\n"
            "Thank you for your application to {company_name} ({slot_title}). Your "
            "application fee of {amount} {currency} has been received.\n\n"
            "Reference: {reference_id}\nPaid on: {paid_at}\n\n"
            "Your application has been submitted and accepted. Please see the attached "
            "receipt.\n\nIPT Marketplace"
        ),
    },
    {
        "key": "acceptance_sms",
        "name": "Acceptance SMS",
        "channel": "SMS",
        "trigger_label": "Company Acceptance",
        "subject": "",
        "description": "Sent by a company when an applicant is accepted.",
        "body": (
            "Congratulations! You have been accepted for {slot_title} at {company_name}. "
            "Check your email for the acceptance letter or further instructions."
        ),
    },
    {
        "key": "verification_rejected_email",
        "name": "Verification Rejected",
        "channel": "EMAIL",
        "trigger_label": "Verification Rejection",
        "subject": "Your IPT Marketplace verification was not approved",
        "description": "Sent to students/companies if their verification documents are invalid.",
        "body": (
            "Dear {user_name},\n\n"
            "Thank you for submitting your verification documents. Unfortunately, your "
            "submission could not be approved.\n\nReason: {reason}\n\n"
            "Please re-upload the required documents and re-submit for review.\n\n"
            "IPT Marketplace"
        ),
    },
]

DEFAULT_SETTINGS = [
    ("app_fee_amount", "Application Fee (TZS)", "number", 15000, None, False),
    ("max_applications_per_student", "Max Applications Per Student", "number", 3, None, False),
    ("default_slot_limit", "Default Slot Applicant Limit", "number", 50, None, False),
    ("sms_enabled", "SMS Gateway Enabled", "bool", None, True, False),
    ("sms_api_endpoint", "SMS API Endpoint URL", "text", None, "https://api.tz-sms-provider.com/v1/send", False),
    ("sms_api_key", "SMS API Key", "text", None, "", True),
    ("maintenance_mode", "Maintenance Mode", "bool", None, False, False),
    ("student_req_student_id", "Student ID Card Required", "bool", None, True, False),
    ("student_req_transcript", "Official Transcript Required", "bool", None, True, False),
    ("student_req_cv", "Curriculum Vitae (CV) Required", "bool", None, False, False),
]

DEFAULT_ROLES = [
    {
        "name": "Super Admin",
        "description": "Full System Access",
        "permissions": {
            "user_management": {"view": True, "create": True, "edit": True, "delete": True},
            "slot_verification": {"view": True, "create": False, "edit": True, "delete": False},
            "financial_ledger": {"view": True, "create": True, "edit": True, "delete": False},
            "system_metrics": {"view": True, "create": False, "edit": False, "delete": False},
        },
        "privileges": {"bypass_approval": True, "impersonate": True},
        "is_system": True,
    },
    {
        "name": "Verification Officer",
        "description": "KYC & Document Review",
        "permissions": {
            "user_management": {"view": True, "create": False, "edit": True, "delete": False},
            "slot_verification": {"view": True, "create": False, "edit": True, "delete": False},
            "financial_ledger": {"view": False, "create": False, "edit": False, "delete": False},
            "system_metrics": {"view": True, "create": False, "edit": False, "delete": False},
        },
        "privileges": {"bypass_approval": False, "impersonate": False},
        "is_system": True,
    },
    {
        "name": "Finance Admin",
        "description": "Ledger & Transactions",
        "permissions": {
            "user_management": {"view": True, "create": False, "edit": False, "delete": False},
            "slot_verification": {"view": False, "create": False, "edit": False, "delete": False},
            "financial_ledger": {"view": True, "create": True, "edit": True, "delete": False},
            "system_metrics": {"view": True, "create": False, "edit": False, "delete": False},
        },
        "privileges": {"bypass_approval": False, "impersonate": False},
        "is_system": True,
    },
    {
        "name": "Support Team",
        "description": "User Inquiries & Ticketing",
        "permissions": {
            "user_management": {"view": True, "create": False, "edit": False, "delete": False},
            "slot_verification": {"view": True, "create": False, "edit": False, "delete": False},
            "financial_ledger": {"view": False, "create": False, "edit": False, "delete": False},
            "system_metrics": {"view": False, "create": False, "edit": False, "delete": False},
        },
        "privileges": {"bypass_approval": False, "impersonate": False},
        "is_system": True,
    },
]


def seed_defaults(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    PlatformSetting = apps.get_model("core", "PlatformSetting")
    AdminRole = apps.get_model("accounts", "AdminRole")

    for t in DEFAULT_TEMPLATES:
        NotificationTemplate.objects.update_or_create(key=t["key"], defaults=t)

    for key, label, vtype, num, text, secret in DEFAULT_SETTINGS:
        defaults = {
            "label": label,
            "value_type": vtype,
            "value_number": num if vtype == "number" else None,
            "value_bool": bool(text) if vtype == "bool" else False,
            "value_text": text if vtype == "text" else "",
            "is_secret": secret,
        }
        PlatformSetting.objects.update_or_create(key=key, defaults=defaults)

    for r in DEFAULT_ROLES:
        AdminRole.objects.update_or_create(name=r["name"], defaults=r)


def remove_defaults(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    PlatformSetting = apps.get_model("core", "PlatformSetting")
    AdminRole = apps.get_model("accounts", "AdminRole")
    NotificationTemplate.objects.filter(key__in=[t["key"] for t in DEFAULT_TEMPLATES]).delete()
    PlatformSetting.objects.filter(key__in=[s[0] for s in DEFAULT_SETTINGS]).delete()
    AdminRole.objects.filter(name__in=[r["name"] for r in DEFAULT_ROLES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0002_notificationtemplate"),
        ("core", "0002_platformsetting_auditlog"),
        ("accounts", "0002_adminrole"),
    ]

    operations = [
        migrations.RunPython(seed_defaults, remove_defaults),
    ]