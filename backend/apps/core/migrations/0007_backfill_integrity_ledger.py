from django.db import migrations


def _audit_fields(row):
    return {
        "actor_id": row.actor_id,
        "actor_label": row.actor_label,
        "action": row.action,
        "module": row.module,
        "description": row.description,
        "ip_address": str(row.ip_address) if row.ip_address else "",
        "is_system": row.is_system,
        "created_at": row.created_at,
    }


def _payment_fields(row):
    return {
        "reference_id": row.reference_id,
        "student_id": row.student_id,
        "application_id": row.application_id,
        "amount": str(row.amount),
        "currency": row.currency,
        "method": row.method,
        "gateway": row.gateway,
        "gateway_txn_id": row.gateway_txn_id,
        "status": row.status,
        "is_paid": row.is_paid,
        "paid_at": row.paid_at,
    }


def _application_fields(row):
    return {
        "student_id": row.student_id,
        "slot_id": row.slot_id,
        "status": row.status,
        "is_accepted": row.is_accepted,
        "company_message": row.company_message,
        "student_message": row.student_message,
        "payment_deadline": row.payment_deadline,
        "letter_original_name": row.letter_original_name,
        "letter_sha256": row.letter_sha256,
    }


def _document_fields(row):
    return {
        "owner_id": row.owner_id,
        "doc_type": row.doc_type,
        "original_name": row.original_name,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "mime_type": row.mime_type,
        "is_verified": row.is_verified,
    }


def backfill(apps, schema_editor):
    from apps.core.immutability import seal

    IntegrityRecord = apps.get_model("core", "IntegrityRecord")
    for model, rtype, fields_fn in (
        (apps.get_model("core", "AuditLog"), "AUDIT", _audit_fields),
        (apps.get_model("payments", "Payment"), "PAYMENT", _payment_fields),
        (apps.get_model("applications", "Application"), "APPLICATION", _application_fields),
        (apps.get_model("documents", "Document"), "DOCUMENT", _document_fields),
    ):
        for row in model.objects.order_by("id").iterator():
            seal(IntegrityRecord, record_type=rtype, record_id=row.id, fields=fields_fn(row))


def remove_ledger(apps, schema_editor):
    apps.get_model("core", "IntegrityRecord").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_audit_log_immutable"),
        ("payments", "0001_initial"),
        ("applications", "0006_application_letter_sha256"),
        ("documents", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill, remove_ledger),
    ]