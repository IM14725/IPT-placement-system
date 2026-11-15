"""Numbers and lists for the organization dashboard and its "View all" pages.

Read-only and always scoped to one organization: every function takes the organization and filters
by it, so one tenant can never see another's applicants, slots or activity.
"""

from collections import Counter
from datetime import datetime, time, timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncWeek
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from apps.applications.models import Application, ApplicationStatus
from apps.core.models import AuditLog
from apps.documents.models import Document
from apps.slots.models import Slot, SlotStatus

DEADLINE_WARNING_DAYS = 3
CHART_WEEKS = 12

_HELD = [ApplicationStatus.PENDING, ApplicationStatus.PAID]


def slots_with_counts(organization):
    """The organization's slots with seat and applicant counts in one query."""
    slots = list(
        Slot.objects.filter(organization=organization)
        .annotate(
            booked=Count("applications", filter=Q(applications__status__in=_HELD)),
            paid_count=Count("applications", filter=Q(applications__status=ApplicationStatus.PAID)),
            accepted_count=Count("applications", filter=Q(applications__is_accepted=True)),
            total_applications=Count("applications"),
        )
        .select_related("district__region")
        .order_by("-created_at")
    )
    now = timezone.now()
    for slot in slots:
        slot.seats_left = max(0, slot.capacity - slot.booked)
        slot.fill_percent = min(100, round(slot.booked * 100 / slot.capacity)) if slot.capacity else 0
        slot.waiting_count = max(0, slot.paid_count - slot.accepted_count)
        # A slot can be deleted only while nobody has ever applied to it.
        slot.can_delete = slot.total_applications == 0
        slot.days_left = None
        slot.deadline_soon = False
        if slot.application_deadline:
            remaining = slot.application_deadline - now
            slot.days_left = remaining.days if remaining > timedelta(0) else None
            slot.deadline_soon = timedelta(0) < remaining <= timedelta(days=DEADLINE_WARNING_DAYS)
    return slots


def _pct(part, whole):
    """``part`` as a whole-number percentage of ``whole``; ``None`` when it cannot be meaningful."""
    if not whole or part > whole:
        return None
    return round(part * 100 / whole)


def _duration_label(delta):
    if delta is None:
        return ""
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 3600:
        return _("under 1 hour")
    hours = seconds / 3600
    if hours < 48:
        return _("%(n).1f hours") % {"n": hours}
    return _("%(n).1f days") % {"n": hours / 24}


def slot_funnel(slot):
    """How one slot performs: viewed -> applied -> paid -> accepted, plus where seats were lost.

    Views are students who opened the slot's detail page (once each). "Paid" counts everyone who
    ever paid, including students who later cancelled inside the 24-hour window. Time to accept is
    measured from the payment to the acceptance; an accepted application is write-protected by a
    database trigger, so its ``updated_at`` is the moment it was accepted.
    """
    from django.db.models import Avg, DurationField, ExpressionWrapper, F

    from apps.slots.models import SlotView

    counts = Application.objects.filter(slot=slot).aggregate(
        applied=Count("id"),
        paid=Count("id", filter=Q(payment__is_paid=True)),
        accepted=Count("id", filter=Q(is_accepted=True)),
        awaiting=Count("id", filter=Q(status=ApplicationStatus.PENDING)),
        unpaid=Count("id", filter=Q(status=ApplicationStatus.UNPAID)),
        cancelled=Count("id", filter=Q(status=ApplicationStatus.CANCELLED)),
    )
    average = Application.objects.filter(
        slot=slot, is_accepted=True, payment__is_paid=True, payment__paid_at__isnull=False
    ).aggregate(
        avg=Avg(ExpressionWrapper(F("updated_at") - F("payment__paid_at"), output_field=DurationField()))
    )["avg"]
    views = SlotView.objects.filter(slot=slot).count()
    steps = [
        {"label": _("Viewed"), "count": views, "pct": None},
        {"label": _("Applied"), "count": counts["applied"], "pct": _pct(counts["applied"], views)},
        {"label": _("Paid"), "count": counts["paid"], "pct": _pct(counts["paid"], counts["applied"])},
        {"label": _("Accepted"), "count": counts["accepted"], "pct": _pct(counts["accepted"], counts["paid"])},
    ]
    top = max([step["count"] for step in steps] or [0]) or 1
    for step in steps:
        step["width"] = max(2, round(step["count"] * 100 / top)) if step["count"] else 0
    return {
        "steps": steps,
        "awaiting": counts["awaiting"],
        "unpaid": counts["unpaid"],
        "cancelled": counts["cancelled"],
        "avg_time_to_accept": _duration_label(average),
    }


def kpis(slots):
    active = [s for s in slots if s.status in (SlotStatus.OPEN, SlotStatus.FULL)]
    capacity = sum(s.capacity for s in active)
    filled = sum(s.booked for s in active)
    paid = sum(s.paid_count for s in slots)
    accepted = sum(s.accepted_count for s in slots)
    return {
        "active_slots": len(active),
        "total_slots": len(slots),
        "capacity": capacity,
        "filled": filled,
        "fill_percent": min(100, round(filled * 100 / capacity)) if capacity else 0,
        "paid": paid,
        "accepted": accepted,
        "waiting": max(0, paid - accepted),
    }


def todo_items(organization, slots, *, can_view_applicants=True, can_manage_slots=True, can_manage_documents=True):
    """What needs the team's attention, most urgent first. Each item links to where it is fixed."""
    items = []

    if can_view_applicants:
        for slot in slots:
            if slot.waiting_count:
                items.append({
                    "tone": "primary",
                    "icon": "how_to_reg",
                    "text": ngettext(
                        "%(n)d paid applicant is waiting for your acceptance: %(slot)s",
                        "%(n)d paid applicants are waiting for your acceptance: %(slot)s",
                        slot.waiting_count,
                    ) % {"n": slot.waiting_count, "slot": slot.title},
                    "action": _("Review"),
                    "url": reverse("organization-applicants", args=[slot.id]),
                })

    for slot in slots:
        if slot.status == SlotStatus.OPEN and slot.deadline_soon and slot.seats_left > 0:
            days = slot.days_left or 0
            when = _("today") if days == 0 else ngettext("in %(d)d day", "in %(d)d days", days) % {"d": days}
            items.append({
                "tone": "warn",
                "icon": "event_busy",
                "text": ngettext(
                    "%(slot)s closes %(when)s with %(n)d seat still empty",
                    "%(slot)s closes %(when)s with %(n)d seats still empty",
                    slot.seats_left,
                ) % {"slot": slot.title, "when": when, "n": slot.seats_left},
                "action": _("Edit slot") if can_manage_slots else _("View"),
                "url": reverse("organization-slot-edit", args=[slot.id]) if can_manage_slots else reverse("organization-slots"),
            })

    for slot in slots:
        if slot.seats_left == 0 and slot.status in (SlotStatus.OPEN, SlotStatus.FULL):
            items.append({
                "tone": "ok",
                "icon": "task_alt",
                "text": _("%(slot)s is full (%(n)d of %(n)d seats taken)") % {"slot": slot.title, "n": slot.capacity},
                "action": _("Post another") if can_manage_slots else _("View"),
                "url": reverse("organization-slot-duplicate", args=[slot.id]) if can_manage_slots else reverse("organization-slots"),
            })

    if can_manage_documents:
        failed = Document.objects.filter(
            owner_id__in=organization.historical_member_user_ids(),
            doc_type__in=[Document.DocType.BRELA_CERT, Document.DocType.TIN_CERT, Document.DocType.BUSINESS_LICENSE],
            scan_status="ERROR",
        )
        for doc in failed:
            items.append({
                "tone": "error",
                "icon": "report",
                "text": _("%(doc)s failed its security check. Delete it and upload it again.") % {"doc": doc.get_doc_type_display()},
                "action": _("Fix"),
                "url": reverse("organization-profile"),
            })
    order = {"error": 0, "primary": 1, "warn": 2, "ok": 3}
    items.sort(key=lambda item: order[item["tone"]])
    return items


def paid_applications(organization):
    """Every paid application to this organization's slots (the only ones a team may see)."""
    return (
        Application.objects.filter(slot__organization=organization, status=ApplicationStatus.PAID)
        .select_related(
            "student__user", "student__university", "student__district__region", "slot", "payment"
        )
        .annotate(note_count=Count("notes"))
    )


def recent_applicants(organization, limit=5):
    return list(paid_applications(organization).order_by("-payment__paid_at", "-created_at")[:limit])


def team_activity(organization, limit=8):
    return list(
        AuditLog.objects.filter(organization=organization).select_related("actor").order_by("-created_at")[:limit]
    )


# ── charts (same spec shapes as apps/core/metrics.py, drawn by static/js/metrics-charts.js) ──

def _top(counter, limit=8):
    return [{"label": label or _("Not specified"), "values": [count], "region": ""} for label, count in counter.most_common(limit)]


def charts(organization):
    """Chart specs for the Insights page."""
    paid = Application.objects.filter(slot__organization=organization, status=ApplicationStatus.PAID)

    # Applications per week, last CHART_WEEKS weeks (by payment date = when the team first sees them).
    today = timezone.localdate()
    this_week = today - timedelta(days=today.weekday())
    weeks = [this_week - timedelta(weeks=i) for i in range(CHART_WEEKS - 1, -1, -1)]
    start = timezone.make_aware(datetime.combine(weeks[0], time.min))
    per_week = {
        row["week"].date(): row
        for row in paid.filter(payment__paid_at__gte=start)
        .annotate(week=TruncWeek("payment__paid_at"))
        .values("week")
        .annotate(total=Count("id"), accepted=Count("id", filter=Q(is_accepted=True)))
    }
    weekly = {
        "id": "applications_per_week",
        "shape": "series",
        "kind": "bar",
        "title": _("Paid applications per week"),
        "icon": "bar_chart",
        "note": _("Last %(n)d weeks. Each bar is the week starting on that Monday.") % {"n": CHART_WEEKS},
        "labels": [w.strftime("%d %b") for w in weeks],
        "datasets": [
            {"label": _("Paid applications"), "color": "primary", "data": [per_week.get(w, {}).get("total", 0) for w in weeks]},
            {"label": _("Accepted so far"), "color": "ok", "data": [per_week.get(w, {}).get("accepted", 0) for w in weeks]},
        ],
    }

    rows = list(
        paid.values_list(
            "student__university__name", "student__region__name", "student__course", "is_accepted"
        )
    )
    accepted = sum(1 for row in rows if row[3])
    status = {
        "id": "acceptance_status",
        "shape": "doughnut",
        "title": _("Accepted vs waiting"),
        "icon": "donut_large",
        "labels": [_("Accepted"), _("Waiting for acceptance")],
        "data": [accepted, len(rows) - accepted],
        "colors": ["ok", "warn"],
    }

    def bars(chart_id, title, icon, index):
        return {
            "id": chart_id,
            "shape": "bars",
            "title": title,
            "icon": icon,
            "horizontal": True,
            "datasets": [{"label": _("Applicants"), "color": "primary"}],
            "items": _top(Counter((row[index] or "").strip() for row in rows)),
        }

    return [
        weekly,
        status,
        bars("by_university", _("Applicants by university"), "school", 0),
        bars("by_region", _("Applicants by home region"), "map", 1),
        bars("by_course", _("Applicants by course"), "menu_book", 2),
    ]
