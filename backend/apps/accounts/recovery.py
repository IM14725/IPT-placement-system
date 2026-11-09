"""Super Admin / administrator MFA recovery by multi-admin approval (M-of-N).

A locked-out administrator files a request (self-service, always answered generically).
Designated recovery approvers vote. The requester can never approve their own request,
only designated approvers may vote, every vote and outcome is audited, and the reset
executes only when the threshold is met. Executing resets the target's MFA factors and
recovery codes and ends their sessions; they must then re-enroll (a hardware key for
Super Admins) before getting any administrative access.
"""

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts import mfa
from apps.accounts.models import (
    AdminRecoveryApproval,
    AdminRecoveryRequest,
    RecoveryStatus,
    User,
    UserRole,
)
from apps.core.audit import audit_event
from apps.core.providers import send_email


def eligible_approvers(target=None):
    qs = User.objects.filter(
        is_recovery_approver=True, is_active=True, role__in=[UserRole.ADMIN, UserRole.SUPER_ADMIN]
    )
    return qs.exclude(pk=target.pk) if target is not None else qs


def expire_stale():
    AdminRecoveryRequest.objects.filter(
        status=RecoveryStatus.PENDING, expires_at__lt=timezone.now()
    ).update(status=RecoveryStatus.EXPIRED)


def request_recovery(email, request=None):
    """Create a pending request for an administrator. Returns it, or None (callers say nothing)."""
    expire_stale()
    target = User.objects.filter(email=email, is_active=True).first()
    if target is None or not mfa.is_admin_user(target) or mfa.is_break_glass(target):
        return None
    if AdminRecoveryRequest.objects.filter(target=target, status=RecoveryStatus.PENDING).exists():
        return None
    approvers = list(eligible_approvers(target))
    required = settings.ADMIN_RECOVERY_APPROVALS
    if len(approvers) < required:
        audit_event(
            request, action="Recovery Unavailable", module="Security", actor=None, is_system=True,
            outcome="REJECTED", target=target,
            description=f"{target.email} asked for recovery but fewer than {required} approvers are designated",
        )
        return None
    req = AdminRecoveryRequest.objects.create(
        target=target,
        required_approvals=required,
        expires_at=timezone.now() + timedelta(hours=settings.ADMIN_RECOVERY_HOURS),
        requester_ip=(request.META.get("REMOTE_ADDR") if request is not None else None),
    )
    audit_event(
        request, action="Recovery Requested", module="Security", is_system=True, target=target,
        target_label=target.email,
        description=f"{target.email} requested MFA recovery ({required} of {len(approvers)} approvals needed)",
        context={"request_id": str(req.id)},
    )
    for approver in approvers:
        try:
            send_email(
                "Administrator recovery request needs your decision",
                [approver.email],
                f"{target.email} has asked to have their MFA reset. Verify their identity out of band, "
                f"then approve or reject in the admin console (Recovery). {required} approvals are needed. "
                "You cannot approve a request you made yourself.",
            )
        except Exception:  # noqa: BLE001
            pass
    return req


def _impossible(req, rejects):
    pool = eligible_approvers(req.target).count()
    return pool - rejects < req.required_approvals


@transaction.atomic
def decide(req, approver, approve, request=None, note=""):
    """Record one approver's vote and apply the outcome. Raises on any rule violation."""
    req = AdminRecoveryRequest.objects.select_for_update().get(pk=req.pk)
    expire_stale()
    req.refresh_from_db()
    if req.status != RecoveryStatus.PENDING:
        raise ValidationError("This request is no longer open.")
    if approver.pk == req.target_id:
        raise PermissionDenied("You cannot decide your own recovery request.")
    if not (approver.is_recovery_approver and approver.is_active and mfa.is_admin_user(approver)):
        raise PermissionDenied("Only designated recovery approvers can decide.")
    if req.decisions.filter(approver=approver).exists():
        raise ValidationError("You have already decided this request.")
    AdminRecoveryApproval.objects.create(request=req, approver=approver, approve=approve, note=note[:200])
    audit_event(
        request,
        action="Recovery Approved" if approve else "Recovery Rejected",
        module="Security",
        actor=approver,
        target=req.target,
        target_label=req.target.email,
        description=f"{approver.email} {'approved' if approve else 'rejected'} recovery for {req.target.email}",
        context={"request_id": str(req.id), "note": note[:200]},
    )
    approvals = req.decisions.filter(approve=True).count()
    rejects = req.decisions.filter(approve=False).count()
    if approvals >= req.required_approvals:
        _execute(req, request)
    elif _impossible(req, rejects):
        req.status = RecoveryStatus.REJECTED
        req.save(update_fields=["status"])
        audit_event(
            request, action="Recovery Denied", module="Security", is_system=True, target=req.target,
            target_label=req.target.email, outcome="REJECTED",
            description="The approval threshold can no longer be reached",
            context={"request_id": str(req.id)},
        )
    return req


def _execute(req, request=None):
    from apps.accounts.sessions import revoke_user_session

    target = req.target
    target.recovery_codes.all().delete()
    target.webauthn_credentials.all().delete()
    if hasattr(target, "totp_device"):
        target.totp_device.delete()
    req.status = RecoveryStatus.EXECUTED
    req.executed_at = timezone.now()
    req.save(update_fields=["status", "executed_at"])
    revoke_user_session(target, "mfa_recovered")
    audit_event(
        request, action="Recovery Executed", module="Security", is_system=True, target=target,
        target_label=target.email,
        description=f"MFA for {target.email} was reset by quorum; re-enrollment required",
        context={"request_id": str(req.id), "approvers": [d.approver.email for d in req.decisions.filter(approve=True)]},
    )
    try:
        send_email(
            "Your administrator MFA was reset",
            [target.email],
            "Your recovery request was approved. Sign in with your password, then register a new "
            "security key (Super Admins) or authenticator app and generate new recovery codes.",
        )
    except Exception:  # noqa: BLE001
        pass
