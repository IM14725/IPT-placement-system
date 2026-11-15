import hashlib
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.applications.models import Application, ApplicationStatus
from apps.core.cache import cache_get_or_set, slot_search_key
from apps.core.rate_limit import TokenBucketThrottle
from apps.slots.models import Slot, SlotStatus
from apps.slots.serializers import SlotSerializer
from apps.students.models import VerificationStatus


class SlotSearchThrottle(TokenBucketThrottle):
    scope = "slot-search"
    capacity = 120
    refill_per_second = 20.0


QUICK_FILTERS = ("closing", "new")
QUICK_WINDOW_DAYS = 7
SORTS = ("closing", "stipend", "seats")
KEYWORD_MAX = 100
MAX_STIPEND_FILTER = Decimal("100000000")


def clean_min_stipend(raw):
    """A whole, non-negative amount, or ``""`` when the value is missing or not a number."""
    try:
        value = Decimal(str(raw or "").strip())
    except (InvalidOperation, ValueError):
        return ""
    if not value.is_finite() or value <= 0 or value > MAX_STIPEND_FILTER:
        return ""
    return str(int(value))


def _signature(
    region, district, department, academic_year, education_level, organization="", quick="",
    keyword="", sort="", paid_only=False, min_stipend="",
):
    raw = "|".join(
        [
            quick or "",
            region or "",
            district or "",
            (department or "").lower(),
            academic_year or "",
            education_level or "",
            (organization or "").strip().lower(),
            (keyword or "").strip().lower(),
            sort or "",
            "1" if paid_only else "0",
            min_stipend or "",
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _sort_key(sort):
    """Ordering applied after seat counts are known (the "newest first" default needs no sorting)."""
    if sort == "closing":
        # Soonest deadline first; slots without a deadline go last.
        return lambda s: (s.application_deadline is None, s.application_deadline or timezone.now())
    if sort == "stipend":
        return lambda s: -(s.stipend_amount or 0) if s.stipend_available else 1
    if sort == "seats":
        return lambda s: -s._available
    return None


def _load_slot_search(
    region, district, department, academic_year, education_level, organization="", quick="",
    keyword="", sort="", paid_only=False, min_stipend="",
):
    """Run the search query with counts aggregated in a single DB hit."""
    qs = (
        Slot.objects.filter(
            organization__verification_status=VerificationStatus.APPROVED,
            organization__suspended_at__isnull=True,
        )
        # A paused slot is hidden from students, like on the dashboard; FULL ones stay so the card can say "Slot Full".
        .exclude(status__in=[SlotStatus.CLOSED, SlotStatus.PAUSED])
        .select_related("organization", "district__region")
    )
    if organization:
        qs = qs.filter(organization__name__icontains=organization.strip())
    keyword = (keyword or "").strip()
    if keyword:
        qs = qs.filter(
            Q(title__icontains=keyword)
            | Q(description__icontains=keyword)
            | Q(role_type__icontains=keyword)
            | Q(skills_required__icontains=keyword)
        )
    if paid_only or min_stipend:
        qs = qs.filter(stipend_available=True)
    if min_stipend:
        qs = qs.filter(stipend_amount__gte=Decimal(min_stipend))
    now = timezone.now()
    if quick == "closing":
        qs = qs.filter(
            application_deadline__gt=now,
            application_deadline__lte=now + timedelta(days=QUICK_WINDOW_DAYS),
        ).order_by("application_deadline")
    elif quick == "new":
        qs = qs.filter(created_at__gte=now - timedelta(days=QUICK_WINDOW_DAYS))

    if region:
        qs = qs.filter(district__region_id=region)
    if district:
        qs = qs.filter(district_id=district)
    if department:
        qs = qs.filter(department__icontains=department)
    if academic_year:
        qs = qs.filter(academic_year=academic_year)
    if education_level:
        qs = qs.filter(education_level=education_level)
        if education_level == "7" and academic_year:
            qs = qs.filter(academic_year=academic_year)

    slots = list(qs)
    slot_ids = [s.id for s in slots]
    counts = {}
    if slot_ids:
        counts = dict(
            Application.objects.filter(
                slot_id__in=slot_ids,
                status__in=[ApplicationStatus.PENDING, ApplicationStatus.PAID],
            )
            .values("slot_id")
            .annotate(n=Count("id"))
            .values_list("slot_id", "n")
        )
    for slot in slots:
        booked = counts.get(slot.id, 0)
        slot._booked = booked
        slot._available = max(0, slot.capacity - booked)
    key = _sort_key(sort) if quick != "closing" else None
    if key:
        slots.sort(key=key)
    return SlotSerializer(slots, many=True).data


class SlotSearchView(APIView):
    """Marketplace search with live filter reduction.

    Query params: region, district, department, academic_year, education_level,
    organization (name search), quick ("closing": deadline within 7 days, soonest first;
    "new": posted in the last 7 days), q (keyword in title, description, role and skills),
    sort ("closing", "stipend", "seats"; default newest first), paid_only ("1") and
    min_stipend (monthly TZS, implies paid only).
    All slots from APPROVED organizations are returned (including FULL ones so the
    front-end can render a "Slot Full" state); filters narrow the list.

    The result is cached in Redis against a versioned hot key with singleflight
    protection, so 2000+ students searching the same slot(s) trigger only one
    DB query per TTL window instead of one per request.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [SlotSearchThrottle]
    page_size = 10

    def get(self, request):
        region = request.query_params.get("region")
        district = request.query_params.get("district")
        department = request.query_params.get("department")
        academic_year = request.query_params.get("academic_year")
        education_level = request.query_params.get("education_level")
        organization = (request.query_params.get("organization") or "").strip()[:200]
        quick = request.query_params.get("quick")
        quick = quick if quick in QUICK_FILTERS else ""
        keyword = (request.query_params.get("q") or "").strip()[:KEYWORD_MAX]
        sort = request.query_params.get("sort")
        sort = sort if sort in SORTS else ""
        paid_only = request.query_params.get("paid_only") in ("1", "true", "on")
        min_stipend = clean_min_stipend(request.query_params.get("min_stipend"))

        from apps.core.cache import incr_daily

        incr_daily("searches")
        if region or district:
            from apps.accounts.public import record_region_search

            record_region_search(region, district)

        key = slot_search_key(
            _signature(
                region, district, department, academic_year, education_level, organization, quick,
                keyword, sort, paid_only, min_stipend,
            )
        )
        ttl = getattr(settings, "SLOT_SEARCH_CACHE_TTL", 20)
        data = cache_get_or_set(
            key,
            ttl,
            producer=lambda: _load_slot_search(
                region, district, department, academic_year, education_level, organization, quick,
                keyword, sort, paid_only, min_stipend,
            ),
        )

        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        size = self.page_size
        start = (page - 1) * size
        page_slice = data[start : start + size]
        return Response(
            {
                "count": len(data),
                "page": page,
                "page_size": size,
                "has_more": start + size < len(data),
                "results": page_slice,
            }
        )
