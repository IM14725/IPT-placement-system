"""Audit trail helpers.

``audit_event`` writes one AuditLog row (sealed into the integrity ledger by the post_save
signal). Rows record the organization, the actor's role *at the time*, a target, an outcome
and free-form context. The actor's name/email is copied into ``actor_label`` so a former
member's history stays attributable and searchable after their access is revoked.
``filter_audit_logs`` is the single search implementation behind the organization and
platform audit views.
"""

import csv
from datetime import datetime, time

from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditLog

OUTCOMES = ("SUCCESS", "FAILED", "REJECTED")


def actor_label(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return "System"
    name = (user.get_full_name() or "").strip()
    return f"{name} <{user.email}>" if name else user.email


def actor_role_label(user, organization=None):
    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    if user.is_organization:
        from apps.companies.models import MembershipStatus, OrganizationMember

        org = organization
        if org is None:
            return "Organization member"
        if org.user_id == user.id:
            return "Manager (Owner)"
        member = (
            OrganizationMember.objects.filter(organization=org, user=user)
            .exclude(membership_status=MembershipStatus.INVITED)
            .first()
        )
        return member.get_role_display() if member else "Organization member"
    if user.role == "SUPER_ADMIN" or user.is_superuser:
        return "Super Admin"
    role = getattr(user, "admin_role", None)
    return f"Admin: {role.name}" if role else "Admin"


def client_ip(request):
    if request is None:
        return None
    from django.conf import settings

    trusted_proxies = int(getattr(settings, "TRUSTED_PROXY_COUNT", 0) or 0)
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if trusted_proxies > 0 and xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            idx = max(0, len(parts) - trusted_proxies)
            return parts[idx]
    return request.META.get("REMOTE_ADDR") or None


def audit_event(
    request=None,
    *,
    action,
    module,
    actor=None,
    organization=None,
    description="",
    target=None,
    target_type="",
    target_id="",
    target_label="",
    outcome="SUCCESS",
    context=None,
    is_system=False,
):
    """Record an event. ``target`` may be any model instance (type/id/label are derived)."""
    if actor is None and request is not None:
        actor = getattr(request, "user", None)
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None
    if actor is not None and getattr(actor, "role", "") == "SUPER_ADMIN" and hasattr(actor, "break_glass"):
        context = {**(context or {}), "break_glass": True}
    if target is not None:
        target_type = target_type or target.__class__.__name__
        target_id = target_id or str(getattr(target, "pk", "") or "")
        target_label = target_label or str(target)[:200]
    return AuditLog.objects.create(
        actor=actor,
        actor_label=actor_label(actor) if actor else ("System" if is_system else ""),
        actor_role=actor_role_label(actor, organization) if actor else "",
        action=action,
        module=module,
        description=description,
        ip_address=client_ip(request),
        is_system=is_system or actor is None,
        organization=organization,
        target_type=target_type,
        target_id=str(target_id or ""),
        target_label=target_label[:200],
        outcome=outcome if outcome in OUTCOMES else "SUCCESS",
        context=context or {},
    )


def _parse_date(value, end=False):
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    moment = datetime.combine(day, time.max if end else time.min)
    return timezone.make_aware(moment)


def filter_audit_logs(qs, params):
    """Apply the shared audit filters: organization, actor, action, dates, target, status, q."""
    org = (params.get("organization") or "").strip()
    if org:
        qs = qs.filter(organization_id=org) if _is_uuid(org) else qs.none()
    actor = (params.get("actor") or "").strip()
    if actor:
        q = Q(actor_label__icontains=actor)
        if _is_uuid(actor):
            q |= Q(actor_id=actor)
        qs = qs.filter(q)
    action = (params.get("action") or "").strip()
    if action:
        qs = qs.filter(action__icontains=action)
    module = (params.get("module") or "").strip()
    if module:
        qs = qs.filter(module=module)
    start = _parse_date(params.get("date_from"))
    if start:
        qs = qs.filter(created_at__gte=start)
    end = _parse_date(params.get("date_to"), end=True)
    if end:
        qs = qs.filter(created_at__lte=end)
    target = (params.get("target") or "").strip()
    if target:
        qs = qs.filter(
            Q(target_label__icontains=target)
            | Q(target_id__icontains=target)
            | Q(target_type__icontains=target)
        )
    status = (params.get("status") or "").strip().upper()
    if status in OUTCOMES:
        qs = qs.filter(outcome=status)
    keyword = (params.get("q") or "").strip()
    if keyword:
        qs = qs.filter(
            Q(actor_label__icontains=keyword)
            | Q(action__icontains=keyword)
            | Q(description__icontains=keyword)
            | Q(module__icontains=keyword)
            | Q(target_label__icontains=keyword)
            | Q(actor_role__icontains=keyword)
        )
    return qs


FILTER_KEYS = ("organization", "actor", "action", "module", "date_from", "date_to", "target", "status", "q")


def has_filters(params):
    return any((params.get(k) or "").strip() for k in FILTER_KEYS)


def _is_uuid(value):
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def _csv_safe(value):
    text = str(value if value is not None else "")
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def write_csv(response, qs, limit=5000):
    writer = csv.writer(response)
    writer.writerow(
        ["time", "organization", "actor", "actor_role", "action", "module", "target", "outcome", "ip", "description"]
    )
    for log in qs.select_related("organization")[:limit]:
        writer.writerow(
            [
                _csv_safe(log.created_at.isoformat()),
                _csv_safe(log.organization.name if log.organization_id else ""),
                _csv_safe(log.actor_label),
                _csv_safe(log.actor_role),
                _csv_safe(log.action),
                _csv_safe(log.module),
                _csv_safe(f"{log.target_type}:{log.target_label}" if log.target_type else ""),
                _csv_safe(log.outcome),
                _csv_safe(log.ip_address or ""),
                _csv_safe(log.description),
            ]
        )
    return response
