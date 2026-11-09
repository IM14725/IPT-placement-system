"""Centralized university records: seeding and "university not listed" suggestions."""
import json

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.students.models import University, VerificationStatus

MAX_PENDING_SUGGESTIONS_PER_USER = 3


class SuggestionError(Exception):
    pass


def institution_seed_rows():
    """Name/abbreviation pairs from the bundled Tanzanian institutions dataset."""
    with open(settings.INSTITUTIONS_DATA_FILE, encoding="utf-8") as fh:
        data = json.load(fh)["institutions"]
    return [
        {"name": row["name"].strip(), "abbreviation": (row.get("abbreviation") or "").strip()}
        for row in data
        if row.get("name")
    ]


def seed_universities():
    """Create the approved reference list; safe to run repeatedly. Returns number created."""
    created = 0
    for row in institution_seed_rows():
        if University.objects.filter(name__iexact=row["name"]).exists():
            continue
        University.objects.create(
            name=row["name"],
            abbreviation=row["abbreviation"],
            verification_status=VerificationStatus.APPROVED,
        )
        created += 1
    return created


def check_suggestion(user, name):
    """Validate a suggestion without saving. Returns (clean_name, existing_or_None)."""
    name = " ".join((name or "").split())
    if len(name) < 3:
        raise SuggestionError("Enter the full name of your university.")
    if len(name) > 200:
        raise SuggestionError("University name is too long.")

    existing = University.objects.filter(name__iexact=name).first() or (
        University.objects.filter(abbreviation__iexact=name).first()
    )
    if existing is not None:
        if existing.verification_status == VerificationStatus.REJECTED:
            raise SuggestionError("This university was reviewed and could not be added.")
        return name, existing

    pending = University.objects.filter(
        suggested_by=user, verification_status=VerificationStatus.PENDING
    ).count()
    if pending >= MAX_PENDING_SUGGESTIONS_PER_USER:
        raise SuggestionError(
            "You already have university suggestions awaiting review. Please wait for an admin."
        )
    return name, None


def suggest_university(user, name):
    """Record a student's "my university is not listed" entry.

    If the name already matches an existing record (approved or someone else's pending
    suggestion) that record is reused, so duplicates never pile up. Otherwise a PENDING
    record is created for platform admins to review.
    """
    name, existing = check_suggestion(user, name)
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return University.objects.create(
                name=name,
                verification_status=VerificationStatus.PENDING,
                suggested_by=user,
            )
    except IntegrityError:
        return University.objects.get(name__iexact=name)


def selectable_universities(for_user=None):
    """Approved universities, plus the student's own pending suggestion if they have one."""
    from django.db.models import Q

    q = Q(verification_status=VerificationStatus.APPROVED)
    if for_user is not None and getattr(for_user, "is_authenticated", False):
        q |= Q(suggested_by=for_user, verification_status=VerificationStatus.PENDING)
    return University.objects.filter(q).order_by("name")
