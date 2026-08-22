from django.http import JsonResponse

from apps.core.cache import get_districts, get_regions, get_wards
from apps.core.rate_limit import token_bucket


@token_bucket(capacity=120, refill_per_second=20.0, scope="locations")
def regions(request):
    return JsonResponse({"regions": get_regions()})


@token_bucket(capacity=120, refill_per_second=20.0, scope="locations")
def districts(request):
    region_id = request.GET.get("region") or None
    data = [
        {"id": d["id"], "name": d["name"], "region": d["region_id"]}
        for d in get_districts(region_id)
    ]
    return JsonResponse({"districts": data})


@token_bucket(capacity=120, refill_per_second=20.0, scope="locations")
def wards(request):
    district_id = request.GET.get("district") or None
    data = [
        {"id": w["id"], "name": w["name"], "district": w["district_id"]}
        for w in get_wards(district_id)
    ]
    return JsonResponse({"wards": data})