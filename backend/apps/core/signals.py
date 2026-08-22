"""Seal critical records into the immutable integrity ledger + hot-key invalidation."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.core.immutability import seal


def _audit_fields(instance):
    return {
        "actor_id": instance.actor_id,
        "actor_label": instance.actor_label,
        "action": instance.action,
        "module": instance.module,
        "description": instance.description,
        "ip_address": str(instance.ip_address) if instance.ip_address else "",
        "is_system": instance.is_system,
        "created_at": instance.created_at,
    }


def _payment_fields(instance):
    return {
        "reference_id": instance.reference_id,
        "student_id": instance.student_id,
        "application_id": instance.application_id,
        "amount": str(instance.amount),
        "currency": instance.currency,
        "method": instance.method,
        "gateway": instance.gateway,
        "gateway_txn_id": instance.gateway_txn_id,
        "status": instance.status,
        "is_paid": instance.is_paid,
        "paid_at": instance.paid_at,
    }


def _application_fields(instance):
    return {
        "student_id": instance.student_id,
        "slot_id": instance.slot_id,
        "status": instance.status,
        "is_accepted": instance.is_accepted,
        "company_message": instance.company_message,
        "student_message": instance.student_message,
        "payment_deadline": instance.payment_deadline,
        "letter_original_name": instance.letter_original_name,
        "letter_sha256": getattr(instance, "letter_sha256", ""),
    }


def _document_fields(instance):
    return {
        "owner_id": instance.owner_id,
        "doc_type": instance.doc_type,
        "original_name": instance.original_name,
        "sha256": instance.sha256,
        "size_bytes": instance.size_bytes,
        "mime_type": instance.mime_type,
        "is_verified": instance.is_verified,
    }


@receiver(post_save, sender="core.AuditLog", dispatch_uid="seal_audit_log")
def seal_audit_log(sender, instance, **kwargs):
    from apps.core.models import IntegrityRecord

    seal(IntegrityRecord, record_type="AUDIT", record_id=instance.id, fields=_audit_fields(instance))


@receiver(post_save, sender="payments.Payment", dispatch_uid="seal_payment")
def seal_payment(sender, instance, **kwargs):
    from apps.core.models import IntegrityRecord

    seal(IntegrityRecord, record_type="PAYMENT", record_id=instance.id, fields=_payment_fields(instance))


@receiver(post_save, sender="applications.Application", dispatch_uid="seal_application")
def seal_application(sender, instance, **kwargs):
    from apps.core.models import IntegrityRecord

    seal(IntegrityRecord, record_type="APPLICATION", record_id=instance.id, fields=_application_fields(instance))


@receiver(post_save, sender="documents.Document", dispatch_uid="seal_document")
def seal_document(sender, instance, **kwargs):
    from apps.core.models import IntegrityRecord

    seal(IntegrityRecord, record_type="DOCUMENT", record_id=instance.id, fields=_document_fields(instance))


# --- Hot-key invalidation: bump the slot-search generation / clear validation
# and location caches whenever the underlying data changes. These are no-ops
# when CACHE_ENABLED is off (tests), and cheap INCR/DELETE in production. The
# FastAPI payment webhook bumps the same version key directly (raw SQL path).


def _bump_slot_version():
    from apps.core.cache import bump_slot_version

    bump_slot_version()


@receiver(post_save, sender="slots.Slot", dispatch_uid="invalidate_slot_save")
@receiver(post_delete, sender="slots.Slot", dispatch_uid="invalidate_slot_delete")
def invalidate_slot(sender, instance, **kwargs):
    _bump_slot_version()


@receiver(post_save, sender="companies.CompanyProfile", dispatch_uid="invalidate_company_save")
@receiver(post_delete, sender="companies.CompanyProfile", dispatch_uid="invalidate_company_delete")
def invalidate_company(sender, instance, **kwargs):
    _bump_slot_version()


@receiver(post_save, sender="applications.Application", dispatch_uid="invalidate_application_save")
def invalidate_application(sender, instance, **kwargs):
    _bump_slot_version()


@receiver(post_save, sender="payments.Payment", dispatch_uid="invalidate_payment_save")
def invalidate_payment(sender, instance, **kwargs):
    _bump_slot_version()


@receiver(post_save, sender="students.StudentProfile", dispatch_uid="invalidate_student_save")
@receiver(post_delete, sender="students.StudentProfile", dispatch_uid="invalidate_student_delete")
def invalidate_student(sender, instance, **kwargs):
    from apps.core.cache import invalidate_user_validation

    invalidate_user_validation(instance.user_id)


@receiver(post_save, sender="locations.Region", dispatch_uid="invalidate_region_save")
@receiver(post_delete, sender="locations.Region", dispatch_uid="invalidate_region_delete")
@receiver(post_save, sender="locations.District", dispatch_uid="invalidate_district_save")
@receiver(post_delete, sender="locations.District", dispatch_uid="invalidate_district_delete")
def invalidate_locations(sender, instance, **kwargs):
    from apps.core.cache import invalidate_locations

    invalidate_locations()