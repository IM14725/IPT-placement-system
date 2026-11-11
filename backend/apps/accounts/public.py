"""Data for the public welcome page: trust counters, searchable open slots and the fee.

Only aggregate numbers and the non-sensitive slot fields (title, industry, place, level,
seats left, stipend yes/no) leave this module; organization names and descriptions are never
exposed to anonymous visitors. Results are cached briefly so the page costs a cache read.
"""

import hashlib
import uuid

from django.conf import settings
from django.db.models import Count, F, Q
from django.utils import timezone

from apps.core.cache import cache_get_or_set, daily_field_totals, incr_daily_field, slot_search_key
from apps.core.education import EducationLevel

CACHE_KEY = "public:home:v1"
CACHE_TTL = 300
SEARCH_TTL = 30
PREVIEW_LIMIT = 6  # slots shown on the welcome page, searched or not
SEARCH_LIMIT = PREVIEW_LIMIT
POPULAR_DAYS = 30  # how far back "most searched" looks
REGION_SEARCH_KEY = "region-searches"
INDUSTRY_MAX_LENGTH = 80


def _public_slots():
    from apps.applications.models import ApplicationStatus
    from apps.slots.models import Slot, SlotStatus
    from apps.students.models import VerificationStatus

    now = timezone.now()
    return (
        Slot.objects.filter(
            status=SlotStatus.OPEN,
            organization__verification_status=VerificationStatus.APPROVED,
            organization__suspended_at__isnull=True,
        )
        .filter(Q(application_deadline__isnull=True) | Q(application_deadline__gt=now))
        .annotate(
            booked=Count(
                "applications",
                filter=Q(applications__status__in=[ApplicationStatus.PENDING, ApplicationStatus.PAID]),
            )
        )
    )


def _row(slot):
    return {
        "title": slot.title,
        "industry": slot.industry,
        "district": slot.district.name,
        "region": slot.district.region.name,
        "level": slot.education_level,
        "seats_left": slot.capacity - slot.booked,
        "stipend": slot.stipend_available,
    }


def record_region_search(region="", district=""):
    """Count one search for a region (looked up from the district when only that was chosen)."""
    from apps.locations.models import District

    filters = clean_filters(region=region, district=district)
    region_id = filters["region"]
    if not region_id and filters["district"]:
        region_id = str(
            District.objects.filter(pk=filters["district"]).values_list("region_id", flat=True).first() or ""
        )
    incr_daily_field(REGION_SEARCH_KEY, region_id)


def _popular_slots(open_slots):
    """Up to PREVIEW_LIMIT open slots from the most-searched regions, newest first inside a region.

    Regions are ranked by searches in the last POPULAR_DAYS days, then by how many open slots they have.
    Returns ``(rows, name_of_top_searched_region_or_"")``.
    """
    searches = daily_field_totals(REGION_SEARCH_KEY, days=POPULAR_DAYS)
    by_region = {}
    for slot in open_slots:
        by_region.setdefault(str(slot.district.region_id), []).append(slot)
    ranked = sorted(
        by_region,
        key=lambda rid: (-searches.get(rid, 0), -len(by_region[rid]), by_region[rid][0].district.region.name),
    )
    rows = []
    for rid in ranked:
        for slot in sorted(by_region[rid], key=lambda s: s.created_at, reverse=True):
            rows.append(_row(slot))
            if len(rows) == PREVIEW_LIMIT:
                break
        if len(rows) == PREVIEW_LIMIT:
            break
    top = ranked[0] if ranked and searches.get(ranked[0], 0) > 0 else None
    return rows, (by_region[top][0].district.region.name if top else "")


def clean_filters(region="", district="", industry="", level=""):
    """Validate untrusted query-string values; anything malformed is simply ignored."""

    def _uuid(value):
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            return ""

    try:
        level_value = int(level)
        level_value = level_value if level_value in EducationLevel.values else ""
    except (TypeError, ValueError):
        level_value = ""
    return {
        "region": _uuid(region),
        "district": _uuid(district),
        "industry": (industry or "").strip()[:INDUSTRY_MAX_LENGTH],
        "level": level_value,
    }


def _localise(rows):
    # Cached rows hold the numeric level; the label follows the visitor's language.
    return [
        {**row, "level_label": EducationLevel(row["level"]).label if row.get("level") else ""}
        for row in rows
    ]


def search_public_slots(region="", district="", industry="", level="", limit=SEARCH_LIMIT):
    """Open slots matching the filters, newest first: ``{"rows": [...], "total": n, "filters": {...}}``."""
    filters = clean_filters(region, district, industry, level)
    signature = hashlib.sha1(
        f"{filters['region']}|{filters['district']}|{filters['industry'].lower()}|{filters['level']}|{limit}".encode()
    ).hexdigest()[:20]

    def produce():
        qs = _public_slots().select_related("district__region").filter(capacity__gt=F("booked"))
        if filters["region"]:
            qs = qs.filter(district__region_id=filters["region"])
        if filters["district"]:
            qs = qs.filter(district_id=filters["district"])
        if filters["industry"]:
            qs = qs.filter(industry__icontains=filters["industry"])
        if filters["level"]:
            qs = qs.filter(education_level=filters["level"])
        qs = qs.order_by("-created_at")
        return {"rows": [_row(s) for s in qs[:limit]], "total": qs.count()}

    data = cache_get_or_set(slot_search_key("public:" + signature), SEARCH_TTL, produce)
    return {"rows": _localise(data["rows"]), "total": data["total"], "filters": filters}


def _compute():
    from apps.accounts.models import User, UserRole
    from apps.companies.models import Organization
    from apps.payments.utils import get_application_fee
    from apps.students.models import University, VerificationStatus

    open_slots = [
        s for s in _public_slots().select_related("district__region") if s.capacity - s.booked > 0
    ]
    popular_rows, popular_region = _popular_slots(open_slots)
    return {
        "popular": popular_rows,
        "popular_region": popular_region,
        "stats": {
            "organizations": Organization.objects.filter(
                verification_status=VerificationStatus.APPROVED
            ).count(),
            "slots": len(open_slots),
            "seats": sum(s.capacity - s.booked for s in open_slots),
            # Universities that actually have registered students, not the size of the pick-list.
            "universities": University.objects.filter(
                verification_status=VerificationStatus.APPROVED,
                students__user__is_active=True,
            )
            .distinct()
            .count(),
            "regions": len({s.district.region_id for s in open_slots}),
            "students": User.objects.filter(role=UserRole.STUDENT, is_active=True).count(),
        },
        # Suggestions for the industry search box (what visitors can actually find).
        "industries": sorted({s.industry.strip() for s in open_slots if s.industry.strip()}, key=str.lower)[:60],
        "fee": int(get_application_fee()),
    }


def get_public_home(region="", district="", industry="", level=""):
    """Welcome-page data: counters (above their minimums), the fee, and the slots to show."""
    data = cache_get_or_set(CACHE_KEY, CACHE_TTL, _compute)
    minimums = settings.PUBLIC_STATS_MIN
    stats = data["stats"]
    counters = []
    for key, icon in (
        ("organizations", "business_center"),
        ("slots", "work"),
        ("universities", "school"),
        ("students", "groups"),
        ("regions", "location_on"),
    ):
        value = stats.get(key, 0)
        if value >= minimums.get(key, 1):
            counters.append({"key": key, "icon": icon, "value": value})

    filters = clean_filters(region, district, industry, level)
    filtered = any(filters.values())
    if filtered:
        searched = search_public_slots(region, district, industry, level, limit=SEARCH_LIMIT)
        rows, total = searched["rows"], searched["total"]
    else:  # nobody searched: the open slots in the most-searched places
        rows, total = _localise(data.get("popular", [])), stats.get("slots", 0)
    return {
        "counters": counters,
        "preview": rows,  # kept under the old name for the template and tests
        "rows": rows,
        "total": total,
        "popular_region": "" if filtered else data.get("popular_region", ""),
        "filters": filters,
        "filtered": filtered,
        "industries": data.get("industries", []),
        "has_open_slots": stats.get("slots", 0) > 0,
        "fee": data["fee"],
        "seats": stats.get("seats", 0),
    }
