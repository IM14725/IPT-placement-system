import hashlib

from django.conf import settings
from django.db.models import Count
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


def _signature(region, district, department, level, education_level):
    raw = "|".join(
        [
            region or "",
            district or "",
            (department or "").lower(),
            level or "",
            education_level or "",
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _load_slot_search(region, district, department, level, education_level):
    """Run the search query with counts aggregated in a single DB hit."""
    qs = (
        Slot.objects.filter(
            company__verification_status=VerificationStatus.APPROVED,
        )
        .exclude(status__in=[SlotStatus.CLOSED])
        .select_related("company", "district__region")
    )

    if region:
        qs = qs.filter(district__region_id=region)
    if district:
        qs = qs.filter(district_id=district)
    if department:
        qs = qs.filter(department__icontains=department)
    if level:
        qs = qs.filter(level=level)
    if education_level:
        qs = qs.filter(education_level=education_level)

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
    return SlotSerializer(slots, many=True).data


class SlotSearchView(APIView):
    """Marketplace search with live filter reduction.

    Query params: region, district, department, level, education_level.
    All slots from APPROVED companies are returned (including FULL ones so the
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
        level = request.query_params.get("level")
        education_level = request.query_params.get("education_level")

        from apps.core.cache import incr_daily

        incr_daily("searches")

        key = slot_search_key(
            _signature(region, district, department, level, education_level)
        )
        ttl = getattr(settings, "SLOT_SEARCH_CACHE_TTL", 20)
        data = cache_get_or_set(
            key,
            ttl,
            producer=lambda: _load_slot_search(
                region, district, department, level, education_level
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