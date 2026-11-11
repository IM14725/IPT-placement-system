"""Chart data for the admin Metrics page.

Every chart is returned as plain data and drawn by one generic renderer (static/js/metrics-charts.js),
which also provides the "no data" state and dark-mode colours. Time-based charts follow a range:
7, 30 or 90 days (one point per day) or "all" (one point per month since the first record, up to 3 years).
Snapshot charts (slots by status, verification, places) describe the platform as it is now.
"""

import datetime

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

from apps.core.cache import daily_series

RANGES = ("7", "30", "90", "all")
DEFAULT_RANGE = "30"
MAX_MONTHS = 36
TOP_ORGANIZATIONS = 10


def normalise_range(value):
    return value if value in RANGES else DEFAULT_RANGE


def _first_of_month(day):
    return day.replace(day=1)


def _add_months(day, n):
    month = day.month - 1 + n
    return datetime.date(day.year + month // 12, month % 12 + 1, 1)


def periods(range_key):
    """``(dates, monthly)``: the x-axis buckets for a range, oldest first."""
    today = timezone.localdate()
    if range_key == "all":
        earliest = _earliest_activity() or today
        start = max(_first_of_month(earliest), _add_months(_first_of_month(today), -(MAX_MONTHS - 1)))
        dates, cursor = [], start
        while cursor <= today:
            dates.append(cursor)
            cursor = _add_months(cursor, 1)
        return dates, True
    days = int(range_key)
    return [today - datetime.timedelta(days=days - 1 - i) for i in range(days)], False


def _earliest_activity():
    from apps.accounts.models import User
    from apps.applications.models import Application
    from apps.payments.models import Payment

    stamps = [
        User.objects.order_by("date_joined").values_list("date_joined", flat=True).first(),
        Application.objects.order_by("created_at").values_list("created_at", flat=True).first(),
        Payment.objects.order_by("created_at").values_list("created_at", flat=True).first(),
    ]
    stamps = [timezone.localtime(s).date() for s in stamps if s]
    return min(stamps) if stamps else None


def _label(date, monthly):
    return date.strftime("%b %Y") if monthly else date.strftime("%m-%d")


def _fill(rows, dates, monthly, value_key="n", cast=int):
    by_date = {}
    for row in rows:
        day = row["bucket"].date() if hasattr(row["bucket"], "date") else row["bucket"]
        by_date[day] = by_date.get(day, 0) + (row[value_key] or 0)
    return [cast(by_date.get(d, 0)) for d in dates]


def _series_chart(chart_id, title, icon, labels, datasets, *, kind="bar", stacked=False, money=False, note=""):
    return {
        "id": chart_id, "title": title, "icon": icon, "shape": "series", "kind": kind, "stacked": stacked,
        "money": money, "note": note, "labels": labels, "datasets": datasets, "ranged": True,
    }


def build_charts(range_key):
    from apps.accounts.models import User, UserRole
    from apps.applications.models import Application, ApplicationStatus
    from apps.companies.models import Organization
    from apps.payments.models import Payment, PaymentMethod, PaymentStatus
    from apps.slots.models import Slot, SlotStatus
    from apps.students.models import StudentProfile, VerificationStatus

    range_key = normalise_range(range_key)
    dates, monthly = periods(range_key)
    labels = [_label(d, monthly) for d in dates]
    start = timezone.make_aware(datetime.datetime.combine(dates[0], datetime.time.min))

    trends, market, places = [], [], []

    # ---- Revenue (paid payments, by day of payment)
    paid = Payment.objects.filter(status=PaymentStatus.PAID, paid_at__isnull=False)
    trunc = TruncMonth("paid_at") if monthly else TruncDate("paid_at")
    revenue = _fill(
        paid.filter(paid_at__gte=start).annotate(bucket=trunc).values("bucket").annotate(n=Sum("amount")),
        dates, monthly, cast=float,
    )
    trends.append(_series_chart("revenue", "Revenue (TZS)", "account_balance_wallet", labels,
                                [{"label": "Fees collected (TZS)", "data": revenue, "color": "primary"}], money=True))

    # ---- Sign-ups: students vs organizations
    trunc = TruncMonth("date_joined") if monthly else TruncDate("date_joined")
    signups = {role: [0] * len(dates) for role in (UserRole.STUDENT, UserRole.ORGANIZATION)}
    for role in signups:
        signups[role] = _fill(
            User.objects.filter(role=role, date_joined__gte=start).annotate(bucket=trunc).values("bucket").annotate(n=Count("id")),
            dates, monthly,
        )
    trends.append(_series_chart("signups", "New accounts", "person_add", labels, [
        {"label": "Students", "data": signups[UserRole.STUDENT], "color": "primary"},
        {"label": "Organizations", "data": signups[UserRole.ORGANIZATION], "color": "secondary"},
    ], stacked=True))

    # ---- Applications by status
    trunc = TruncMonth("created_at") if monthly else TruncDate("created_at")
    app_datasets = []
    for status, color in ((ApplicationStatus.PAID, "ok"), (ApplicationStatus.PENDING, "warn"),
                          (ApplicationStatus.UNPAID, "outline"), (ApplicationStatus.CANCELLED, "error")):
        data = _fill(
            Application.objects.filter(status=status, created_at__gte=start).annotate(bucket=trunc).values("bucket").annotate(n=Count("id")),
            dates, monthly,
        )
        app_datasets.append({"label": status.label, "data": data, "color": color})
    trends.append(_series_chart("applications", "Applications by status", "assignment", labels, app_datasets, stacked=True))

    # ---- Logins (distinct users by their latest login day) and slot searches (Redis, kept 45 days)
    trunc = TruncMonth("last_login") if monthly else TruncDate("last_login")
    logins = _fill(
        User.objects.filter(last_login__gte=start).annotate(bucket=trunc).values("bucket").annotate(n=Count("id")),
        dates, monthly,
    )
    trends.append(_series_chart("logins", "Active users by last login", "login", labels,
                                [{"label": "Users", "data": logins, "color": "primary"}], kind="line"))
    span = (timezone.localdate() - dates[0]).days + 1
    searches_by_day = {datetime.date.fromisoformat(p["date"]): p["value"] for p in daily_series("searches", days=min(span, 45))}
    searches = [0] * len(dates)
    for day, value in searches_by_day.items():
        earlier = [i for i, d in enumerate(dates) if d <= day]
        if earlier and (monthly or dates[earlier[-1]] == day):
            searches[earlier[-1]] += value
    trends.append(_series_chart("searches", "Slot searches", "search", labels,
                                [{"label": "Searches", "data": searches, "color": "secondary"}], kind="line",
                                note="Searches are only kept for the last 45 days." if range_key in ("90", "all") else ""))

    # ---- Marketplace snapshots
    slot_counts = dict(Slot.objects.values_list("status").annotate(n=Count("id")))
    market.append({
        "id": "slots_status", "title": "Slots by status", "icon": "work", "shape": "doughnut", "ranged": False,
        "labels": [s.label for s in SlotStatus], "data": [slot_counts.get(s.value, 0) for s in SlotStatus],
        "colors": ["ok", "warn", "outline", "error"],
    })
    statuses = list(VerificationStatus)
    student_counts = dict(StudentProfile.objects.values_list("verification_status").annotate(n=Count("id")))
    org_counts = dict(Organization.objects.values_list("verification_status").annotate(n=Count("id")))
    market.append({
        "id": "verification", "title": "Verification status", "icon": "verified_user", "shape": "series", "kind": "bar",
        "stacked": False, "money": False, "note": "", "ranged": False, "labels": [s.label for s in statuses],
        "datasets": [
            {"label": "Students", "data": [student_counts.get(s.value, 0) for s in statuses], "color": "primary"},
            {"label": "Organizations", "data": [org_counts.get(s.value, 0) for s in statuses], "color": "secondary"},
        ],
    })
    in_range = Payment.objects.filter(created_at__gte=start)
    status_counts = dict(in_range.values_list("status").annotate(n=Count("id")))
    market.append({
        "id": "payments_status", "title": "Payments by status", "icon": "payments", "shape": "doughnut", "ranged": True,
        "labels": [s.label for s in PaymentStatus], "data": [status_counts.get(s.value, 0) for s in PaymentStatus],
        "colors": ["warn", "ok", "error"],
    })
    method_counts = dict(in_range.values_list("method").annotate(n=Count("id")))
    method_labels = [m.label for m in PaymentMethod]
    method_data = [method_counts.get(m.value, 0) for m in PaymentMethod]
    other = sum(method_counts.values()) - sum(method_data)  # older records may carry another method code
    if other:
        method_labels.append("Other")
        method_data.append(other)
    market.append({
        "id": "payments_method", "title": "Payments by method", "icon": "smartphone", "shape": "doughnut", "ranged": True,
        "labels": method_labels, "data": method_data, "colors": ["ok", "primary", "error", "outline"][: len(method_labels)],
    })

    # ---- Places and companies (all time)
    regions = {}
    seat_rows = (
        Slot.objects.exclude(status=SlotStatus.CLOSED)
        .values("district__id", "district__name", "district__region__id", "district__region__name")
        .annotate(slots=Count("id"), seats=Sum("capacity"))
    )
    region_totals, district_items = {}, []
    for r in seat_rows:
        rid = str(r["district__region__id"])
        regions[rid] = r["district__region__name"]
        agg = region_totals.setdefault(rid, [0, 0])
        agg[0] += r["seats"] or 0
        agg[1] += r["slots"]
        district_items.append({"region": rid, "label": r["district__name"], "values": [r["seats"] or 0, r["slots"]]})
    seat_datasets = [{"label": "Seats", "color": "primary"}, {"label": "Slots", "color": "secondary"}]
    places.append({
        "id": "slots_region", "ranged": False, "title": "Slots and seats by region", "icon": "map", "shape": "bars", "horizontal": False,
        "datasets": seat_datasets, "regions": [],
        "items": sorted(({"region": rid, "label": regions[rid], "values": v} for rid, v in region_totals.items()),
                        key=lambda i: -i["values"][0]),
    })
    places.append({
        "id": "slots_district", "ranged": False, "title": "Slots and seats by district", "icon": "location_on", "shape": "bars",
        "horizontal": False, "datasets": seat_datasets,
        "regions": [{"id": rid, "name": name} for rid, name in sorted(regions.items(), key=lambda kv: kv[1])],
        "items": sorted(district_items, key=lambda i: -i["values"][0]),
    })

    applied_regions, applied_region_rows, applied_district_items = {}, {}, []
    for r in (Application.objects.values("slot__district__id", "slot__district__name", "slot__district__region__id", "slot__district__region__name")
              .annotate(n=Count("id"))):
        rid = str(r["slot__district__region__id"])
        applied_regions[rid] = r["slot__district__region__name"] or "Unknown"
        applied_region_rows[rid] = applied_region_rows.get(rid, 0) + r["n"]
        applied_district_items.append({"region": rid, "label": r["slot__district__name"] or "Unknown", "values": [r["n"]]})
    applied_datasets = [{"label": "Applications", "color": "primary"}]
    places.append({
        "id": "applied_region", "ranged": False, "title": "Applications by region", "icon": "map", "shape": "bars", "horizontal": False,
        "datasets": applied_datasets, "regions": [],
        "items": sorted(({"region": rid, "label": applied_regions[rid], "values": [n]} for rid, n in applied_region_rows.items()),
                        key=lambda i: -i["values"][0]),
    })
    places.append({
        "id": "applied_district", "ranged": False, "title": "Applications by district", "icon": "location_on", "shape": "bars",
        "horizontal": False, "datasets": applied_datasets,
        "regions": [{"id": rid, "name": name} for rid, name in sorted(applied_regions.items(), key=lambda kv: kv[1])],
        "items": sorted(applied_district_items, key=lambda i: -i["values"][0]),
    })

    # ---- Which organizations' slots students choose most (within the range)
    org_rows = (
        Application.objects.filter(created_at__gte=start)
        .values("slot__organization__id", "slot__organization__name")
        .annotate(total=Count("id"), paid_count=Count("id", filter=_paid_filter()))
        .order_by("-total")[:TOP_ORGANIZATIONS]
    )
    places.append({
        "id": "top_organizations", "ranged": True, "title": "Applications per organization", "icon": "business", "shape": "bars",
        "horizontal": True, "stacked": True, "regions": [],
        "datasets": [{"label": "Paid", "color": "ok"}, {"label": "Not paid", "color": "warn"}],
        "items": [{"region": "", "label": r["slot__organization__name"], "values": [r["paid_count"], r["total"] - r["paid_count"]]} for r in org_rows],
    })

    sections = [
        {"title": "Activity trends", "icon": "monitoring", "charts": trends},
        {"title": "Marketplace", "icon": "storefront", "charts": market},
        {"title": "Places and organizations", "icon": "map", "charts": places},
    ]
    return {
        "range": range_key,
        "all": [chart for section in sections for chart in section["charts"]],
        "range_label": {"7": "last 7 days", "30": "last 30 days", "90": "last 90 days", "all": "all time (monthly)"}[range_key],
        "sections": sections,
    }


def _paid_filter():
    from django.db.models import Q

    from apps.applications.models import ApplicationStatus

    return Q(status=ApplicationStatus.PAID)
