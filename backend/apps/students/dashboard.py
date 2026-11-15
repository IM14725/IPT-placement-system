"""Everything the student dashboard and its "View all" pages show.

Read-only: nothing here books a seat, takes a payment or changes an application. The slot lists
come from one query each (seat counts are annotated, not counted per card).
"""

import re
from datetime import timedelta

from django.db.models import Count, F, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.applications.models import Application, ApplicationStatus
from apps.core.cache import cache_get_or_set, get_slot_version
from apps.documents.models import Document
from apps.slots.models import Slot, SlotStatus
from apps.students.models import SavedSlot, VerificationStatus

CLOSING_SOON_DAYS = 7
NEW_SLOT_DAYS = 7
RECOMMENDATION_POOL = 300
RECOMMENDATION_TTL = 120

_STOPWORDS = {
    "and", "the", "for", "with", "bachelor", "bachelors", "diploma", "degree", "certificate",
    "ordinary", "higher", "master", "masters", "science", "sciences", "studies", "in", "of",
    "bsc", "msc", "ba", "intern", "internship", "training", "field", "practical", "industrial",
}


# ── slots ────────────────────────────────────────────────────────────────────

def open_slots():
    """Slots a student can still apply to: approved organization, open, seats left, deadline ahead."""
    now = timezone.now()
    return (
        Slot.objects.filter(
            organization__verification_status=VerificationStatus.APPROVED,
            organization__suspended_at__isnull=True,
            status=SlotStatus.OPEN,
        )
        .filter(Q(application_deadline__isnull=True) | Q(application_deadline__gt=now))
        .annotate(
            booked=Count(
                "applications",
                filter=Q(applications__status__in=[ApplicationStatus.PENDING, ApplicationStatus.PAID]),
            )
        )
        .filter(booked__lt=F("capacity"))
        .select_related("organization", "district__region")
    )


def decorate_slots(slots, profile=None):
    """Attach the display fields the slot card needs (seats left, days left, saved or not)."""
    slots = list(slots)
    now = timezone.now()
    saved = set()
    if profile is not None and slots:
        saved = set(
            SavedSlot.objects.filter(student=profile, slot_id__in=[s.id for s in slots]).values_list(
                "slot_id", flat=True
            )
        )
    for slot in slots:
        booked = getattr(slot, "booked", None)
        if booked is None:
            booked = slot.booked_count
        slot.seats_left = max(0, slot.capacity - booked)
        slot.is_saved = slot.id in saved
        slot.days_left = None
        slot.closing_soon = False
        slot.is_new = slot.created_at >= now - timedelta(days=NEW_SLOT_DAYS)
        if slot.application_deadline:
            remaining = slot.application_deadline - now
            slot.days_left = max(0, remaining.days)
            slot.closing_soon = timedelta(0) < remaining <= timedelta(days=CLOSING_SOON_DAYS)
    return slots


def _applied_slot_ids(profile):
    """Slots the student already holds a seat for (or was accepted to): never recommend those."""
    return set(
        Application.objects.filter(
            student=profile, status__in=[ApplicationStatus.PENDING, ApplicationStatus.PAID]
        ).values_list("slot_id", flat=True)
    )


def _tokens(text):
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _STOPWORDS}


def match_slot(profile, slot):
    """Score a slot for a student: ``(score 0-100, [reasons])`` or ``None`` when it does not fit.

    Education level and year (45), location (30) and course vs the slot's field (25).
    A slot that demands a higher level, or a different year, than the student has is excluded.
    """
    score = 0
    reasons = []

    level, need = profile.education_level, slot.education_level
    if need and level:
        if need > level:
            return None
        if need == level:
            score += 30
            reasons.append(_("Matches your education level"))
        else:
            score += 10
    elif not need:
        score += 15
    else:
        score += 10

    year, need_year = profile.academic_year, slot.academic_year
    if need_year and year:
        if need_year != year:
            return None
        score += 15
        reasons.append(_("For your academic year"))
    elif not need_year:
        score += 5

    if profile.district_id and slot.district_id == profile.district_id:
        score += 30
        reasons.append(_("In your district"))
    elif profile.region_id and slot.district.region_id == profile.region_id:
        score += 20
        reasons.append(_("In your region"))

    course = _tokens(profile.course)
    field = _tokens(" ".join([slot.department or "", slot.industry or "", slot.title or "", slot.role_type or ""]))
    if course and course & field:
        score += 25
        reasons.append(_("Related to your course"))

    if not reasons:
        return None
    return min(100, score), reasons


def _score_pool(profile):
    applied = _applied_slot_ids(profile)
    scored = []
    for slot in open_slots().order_by("-created_at")[:RECOMMENDATION_POOL]:
        if slot.id in applied:
            continue
        result = match_slot(profile, slot)
        if result:
            scored.append({"id": str(slot.id), "score": result[0], "reasons": [str(r) for r in result[1]]})
    scored.sort(key=lambda row: -row["score"])
    return scored


def recommended_slots(profile, limit=None):
    """Open slots that fit the student, best match first. Each slot gets ``match`` and ``reasons``."""
    if profile is None:
        return []
    key = f"student:recs:v{get_slot_version()}:{profile.id}:{int(profile.updated_at.timestamp())}"
    scored = cache_get_or_set(key, RECOMMENDATION_TTL, producer=lambda: _score_pool(profile))
    if limit:
        scored = scored[:limit]
    by_id = {str(s.id): s for s in open_slots().filter(id__in=[row["id"] for row in scored])}
    slots = []
    for row in scored:
        slot = by_id.get(row["id"])
        if slot is None:  # filled or closed since the list was cached
            continue
        slot.match = row["score"]
        slot.reasons = row["reasons"]
        slots.append(slot)
    return decorate_slots(slots, profile)


def closing_soon_slots(profile, limit=None):
    now = timezone.now()
    qs = open_slots().filter(
        application_deadline__lte=now + timedelta(days=CLOSING_SOON_DAYS)
    ).order_by("application_deadline")
    if profile is not None:
        qs = qs.exclude(id__in=_applied_slot_ids(profile))
    return decorate_slots(qs[:limit] if limit else qs[:100], profile)


def new_nearby_slots(profile, limit=None):
    """Slots posted in the student's region in the last week."""
    if profile is None or not profile.region_id:
        return []
    qs = (
        open_slots()
        .filter(
            district__region_id=profile.region_id,
            created_at__gte=timezone.now() - timedelta(days=NEW_SLOT_DAYS),
        )
        .exclude(id__in=_applied_slot_ids(profile))
        .order_by("-created_at")
    )
    return decorate_slots(qs[:limit] if limit else qs[:100], profile)


def saved_slots(profile, limit=None):
    """The student's bookmarks, newest first (kept even when the slot is now full or closed)."""
    if profile is None:
        return []
    qs = (
        Slot.objects.filter(saved_by__student=profile)
        .annotate(
            booked=Count(
                "applications",
                filter=Q(applications__status__in=[ApplicationStatus.PENDING, ApplicationStatus.PAID]),
                distinct=True,
            )
        )
        .select_related("organization", "district__region")
        .order_by("-saved_by__created_at")
    )
    return decorate_slots(qs[:limit] if limit else qs, profile)


# ── profile completeness ─────────────────────────────────────────────────────

def profile_completeness(user, profile):
    """``{"percent": 0-100, "items": [...], "missing": [...]}``.

    Counts profile fields, uploaded documents and the verified phone. Platform-admin approval is
    not part of it: students no longer need it to apply.
    """
    doc_types = set()
    if user is not None:
        doc_types = set(Document.objects.filter(owner=user).values_list("doc_type", flat=True))
    profile_url = reverse("student-profile")
    documents_url = reverse("student-documents")
    p = profile
    items = [
        (_("Course and education level"), bool(p and p.course and p.education_level), profile_url),
        (_("Region and district"), bool(p and p.region_id and p.district_id), profile_url),
        (_("University"), bool(p and p.university_id), profile_url),
        (_("Profile photo"), bool(p and p.profile_photo), profile_url),
        (_("GPA"), bool(p and p.gpa is not None), profile_url),
        (_("Skills"), bool(p and p.skills), profile_url),
        (_("Student ID card uploaded"), Document.DocType.STUDENT_ID in doc_types or bool(p and p.id_card_photo), documents_url),
        (_("Results matrix uploaded"), Document.DocType.RESULTS_MATRIX in doc_types, documents_url),
        (_("Phone number verified"), bool(user and user.phone_verified), reverse("student-profile") + "#security"),
    ]
    rows = [{"label": label, "done": done, "url": url} for label, done, url in items]
    done = sum(1 for row in rows if row["done"])
    return {
        "percent": round(done * 100 / len(rows)),
        "items": rows,
        "missing": [row for row in rows if not row["done"]],
        "done": done,
        "total": len(rows),
    }


# ── next step ────────────────────────────────────────────────────────────────

def next_step(user, profile, applications):
    """The single most urgent thing for the student to do, or ``None``.

    Priority: pay before the seat is released > finish the profile > verify the phone >
    a new acceptance > the 24h cancel window > an unused re-application credit > start applying.
    """
    now = timezone.now()
    applications = list(applications)

    unpaid = [
        a for a in applications
        if a.status == ApplicationStatus.PENDING and a.payment_deadline and a.payment_deadline > now
        and getattr(a, "payment", None) is not None and not a.payment.is_paid
    ]
    if unpaid:
        app = min(unpaid, key=lambda a: a.payment_deadline)
        return {
            "kind": "pay",
            "tone": "error",
            "icon": "payments",
            "title": _("Pay now to keep your seat"),
            "body": _("Your seat for %(slot)s at %(org)s is released if the fee is not paid in time.")
            % {"slot": app.slot.title, "org": app.slot.organization.name},
            "countdown": app.payment_deadline,
            "countdown_label": _("Seat released in"),
            "expired_label": _("Seat released"),
            "action": _("Pay now"),
            "url": reverse("student-payment", args=[app.payment.id]),
        }

    if profile is None or not profile.is_complete:
        return {
            "kind": "profile",
            "tone": "warn",
            "icon": "assignment_ind",
            "title": _("Complete your profile to start applying"),
            "body": _("Add your course, education level and location so organizations can assess you."),
            "action": _("Complete profile"),
            "url": reverse("student-profile"),
        }

    if not user.phone_verified:
        return {
            "kind": "phone",
            "tone": "warn",
            "icon": "phonelink_lock",
            "title": _("Verify your phone number"),
            "body": _("You need a verified phone number before you can apply. It takes one SMS code."),
            "action": _("Verify phone"),
            "url": reverse("student-profile") + "#security",
        }

    accepted = [a for a in applications if a.is_accepted and a.updated_at >= now - timedelta(days=14)]
    if accepted:
        app = max(accepted, key=lambda a: a.updated_at)
        return {
            "kind": "accepted",
            "tone": "ok",
            "icon": "celebration",
            "title": _("You have been accepted!"),
            "body": _("%(org)s accepted your application for %(slot)s. Download your acceptance letter and see what to prepare.")
            % {"slot": app.slot.title, "org": app.slot.organization.name},
            "action": _("See next steps"),
            "url": reverse("student-checklist", args=[app.id]),
        }

    cancellable = [a for a in applications if a.is_cancellable and a.cancel_deadline]
    if cancellable:
        app = min(cancellable, key=lambda a: a.cancel_deadline)
        return {
            "kind": "cancel-window",
            "tone": "info",
            "icon": "hourglass_top",
            "title": _("Changed your mind? You can still cancel"),
            "body": _("You can cancel your application for %(slot)s once and get a free re-application credit.")
            % {"slot": app.slot.title},
            "countdown": app.cancel_deadline,
            "countdown_label": _("Cancel window closes in"),
            "expired_label": _("Window closed"),
            "action": _("My applications"),
            "url": reverse("student-applications"),
        }

    if profile.reapplication_credit:
        return {
            "kind": "credit",
            "tone": "ok",
            "icon": "redeem",
            "title": _("You have a free re-application credit"),
            "body": _("Apply to another slot without paying the application fee again."),
            "action": _("Find a slot"),
            "url": reverse("student-marketplace"),
        }

    if not any(a.status in (ApplicationStatus.PENDING, ApplicationStatus.PAID) for a in applications):
        return {
            "kind": "apply",
            "tone": "info",
            "icon": "rocket_launch",
            "title": _("You are ready to apply"),
            "body": _("Your profile is complete. Browse the slots that match you and send your first application."),
            "action": _("Find a slot"),
            "url": reverse("student-marketplace"),
        }
    return None


def application_stats(profile):
    if profile is None:
        return {"sent": 0, "paid": 0, "accepted": 0}
    row = Application.objects.filter(student=profile).aggregate(
        sent=Count("id"),
        paid=Count("id", filter=Q(status=ApplicationStatus.PAID)),
        accepted=Count("id", filter=Q(is_accepted=True)),
    )
    return {"sent": row["sent"] or 0, "paid": row["paid"] or 0, "accepted": row["accepted"] or 0}
