"""Platform console: issue voucher batches, see how they are used, pause or revoke them.

Permissions reuse the Financial Ledger module: view to see, edit to issue, pause, revoke or export
codes (a code is a bearer secret, so exporting needs the edit capability and is audited).
"""

import csv
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.decorators import hardware_reverify
from apps.core.audit import audit_event
from apps.core.pagination import paginate
from apps.core.views import _require_admin
from apps.vouchers import stats
from apps.vouchers.forms import VoucherBatchForm
from apps.vouchers.models import ACTIVE_APPLICATION_STATUSES, CodeMode, FeeVoucher, VoucherBatch, VoucherRedemption
from apps.vouchers.services import MAX_CODES_PER_REQUEST, create_codes

MODULE = "financial_ledger"


def _forbidden(request):
    return render(request, "core/forbidden.html", status=403)


def batch_state(batch, now=None):
    """``(key, label)``: where this batch stands right now."""
    now = now or timezone.now()
    if not batch.is_active:
        return "paused", "Paused"
    if batch.expires_at and now > batch.expires_at:
        return "expired", "Expired"
    if batch.starts_at and now < batch.starts_at:
        return "scheduled", "Scheduled"
    return "active", "Active"


def restriction_summary(batch):
    """Plain-language list of who may use the batch, for the detail page."""
    lines = []

    def names(relation, label, attr="name"):
        items = [str(getattr(o, attr)) for o in getattr(batch, relation).all()]
        if items:
            lines.append((label, ", ".join(items[:12]) + (f" and {len(items) - 12} more" if len(items) > 12 else "")))

    names("universities", "Universities")
    if batch.courses:
        lines.append(("Courses", ", ".join(batch.courses)))
    names("regions", "Student's region")
    names("districts", "Student's district")
    if batch.signup_from or batch.signup_to:
        start = f"{batch.signup_from:%d %b %Y %H:%M}" if batch.signup_from else "any time"
        end = f"{batch.signup_to:%d %b %Y %H:%M}" if batch.signup_to else "any time"
        lines.append(("Registered", f"{start} to {end}"))
    if batch.allowed_emails:
        lines.append(("Named students", f"{len(batch.allowed_emails)} email address(es)"))
    names("organizations", "Organizations")
    if batch.slots.exists():
        lines.append(("Specific slots", f"{batch.slots.count()} slot(s)"))
    names("slot_districts", "Slot in district")
    if batch.slot_start_from or batch.slot_start_to:
        lines.append((
            "Slot starts",
            (f"from {batch.slot_start_from:%d %b %Y} " if batch.slot_start_from else "")
            + (f"until {batch.slot_start_to:%d %b %Y}" if batch.slot_start_to else ""),
        ))
    return lines


@login_required
def batch_list(request):
    if not _require_admin(request, module=MODULE):
        return _forbidden(request)
    state = request.GET.get("state", "")
    q = (request.GET.get("q") or "").strip()
    batches = VoucherBatch.objects.all()
    if q:
        batches = batches.filter(Q(name__icontains=q) | Q(codes__code__icontains=q.replace("-", "").replace(" ", "").upper())).distinct()
    now = timezone.now()
    if state == "paused":
        batches = batches.filter(is_active=False)
    elif state == "expired":
        batches = batches.filter(is_active=True, expires_at__lt=now)
    elif state == "active":
        batches = batches.filter(is_active=True).filter(Q(expires_at__isnull=True) | Q(expires_at__gte=now)).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now)
        )
    page_obj = paginate(batches.order_by("-created_at"), request.GET.get("page"), page_size=25)
    numbers = stats.batch_stats([b.pk for b in page_obj])
    rows = []
    for batch in page_obj:
        key, label = batch_state(batch, now)
        rows.append({"batch": batch, "state": key, "state_label": label, **numbers[batch.pk]})
    return render(
        request,
        "core/vouchers.html",
        {
            "rows": rows,
            "page_obj": page_obj,
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
            "q": q,
            "state": state,
            "signup": stats.signup_overview(),
            "can_edit": _require_admin(request, module=MODULE, cap="edit"),
        },
    )


@login_required
@hardware_reverify
def batch_create(request):
    if not _require_admin(request, module=MODULE, cap="edit"):
        return _forbidden(request)
    if request.method == "POST":
        form = VoucherBatchForm(request.POST)
        if form.is_valid():
            batch = form.save(issued_by=request.user, issuer_label=request.user.get_full_name() or request.user.email)
            audit_event(
                request,
                action="Voucher Batch Created",
                module="Vouchers",
                target=batch,
                description=(
                    f"Issued '{batch.name}': {batch.discount_label}, {batch.codes.count()} code(s), "
                    f"{batch.get_code_mode_display().lower()}"
                    + (f", budget TZS {batch.budget_tzs:,.0f}" if batch.budget_tzs is not None else "")
                ),
                context={"batch_id": str(batch.pk)},
            )
            messages.success(request, f"Batch '{batch.name}' created with {batch.codes.count()} code(s).")
            return redirect("platform-voucher-detail", pk=batch.pk)
    else:
        form = VoucherBatchForm(initial={"discount_type": "PERCENT", "code_mode": "UNIQUE", "per_student_limit": 1})
    groups = (
        ("Terms", ("name", "description", "discount_type", "discount_value", "starts_at", "expires_at", "per_student_limit", "budget_tzs")),
        ("Codes", ("code_mode", "quantity", "shared_code", "shared_max_uses")),
        ("Who may use it", ("universities", "courses", "regions", "districts", "signup_from", "signup_to", "allowed_emails")),
        ("Which slots", ("organizations", "slot_ids", "slot_districts", "slot_start_from", "slot_start_to")),
    )
    return render(
        request,
        "core/voucher_form.html",
        {"form": form, "groups": [(title, [form[name] for name in names]) for title, names in groups]},
    )


@login_required
@hardware_reverify
def batch_detail(request, pk):
    if not _require_admin(request, module=MODULE):
        return _forbidden(request)
    batch = get_object_or_404(VoucherBatch, pk=pk)

    if request.GET.get("export") == "csv":
        if not _require_admin(request, module=MODULE, cap="edit"):
            return _forbidden(request)
        return _export_codes(request, batch)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "toggle":
            batch.is_active = not batch.is_active
            batch.save(update_fields=["is_active", "updated_at"])
            audit_event(
                request, action="Voucher Batch Resumed" if batch.is_active else "Voucher Batch Paused",
                module="Vouchers", target=batch, description=f"{'Resumed' if batch.is_active else 'Paused'} batch '{batch.name}'",
            )
            messages.success(request, "Batch resumed." if batch.is_active else "Batch paused: none of its codes can be used until you resume it.")
        elif action in ("revoke", "restore"):
            voucher = get_object_or_404(FeeVoucher, pk=request.POST.get("code_id"), batch=batch)
            voucher.is_active = action == "restore"
            voucher.save(update_fields=["is_active", "updated_at"])
            audit_event(
                request, action="Voucher Code Restored" if voucher.is_active else "Voucher Code Revoked",
                module="Vouchers", target=voucher, target_label=voucher.display_code,
                description=f"{'Restored' if voucher.is_active else 'Revoked'} code {voucher.display_code} in '{batch.name}'",
            )
            messages.success(request, f"Code {voucher.display_code} {'restored' if voucher.is_active else 'revoked'}.")
        elif action == "add_codes" and batch.code_mode == CodeMode.UNIQUE:
            try:
                count = max(1, min(int(request.POST.get("count") or 0), MAX_CODES_PER_REQUEST))
            except ValueError:
                count = 0
            if count:
                created = create_codes(batch, count, max_uses=1)
                audit_event(
                    request, action="Voucher Codes Added", module="Vouchers", target=batch,
                    description=f"Added {len(created)} code(s) to '{batch.name}'",
                )
                messages.success(request, f"{len(created)} code(s) added.")
            else:
                messages.error(request, "Enter how many codes to add.")
        return redirect("platform-voucher-detail", pk=batch.pk)

    status_filter = request.GET.get("codes", "")
    codes = batch.codes.annotate(
        holding=Count("redemptions", filter=Q(redemptions__application__status__in=ACTIVE_APPLICATION_STATUSES))
    )
    if status_filter == "used":
        codes = codes.filter(holding__gt=0)
    elif status_filter == "unused":
        codes = codes.filter(holding=0, is_active=True)
    elif status_filter == "revoked":
        codes = codes.filter(is_active=False)
    page_obj = paginate(codes.order_by("created_at", "code"), request.GET.get("page"), page_size=50)
    redemptions = (
        VoucherRedemption.objects.filter(batch=batch)
        .select_related("voucher", "student__user", "application__slot__organization")
        .order_by("-created_at")[:25]
    )
    key, label = batch_state(batch)
    numbers = stats.batch_stats([batch.pk])[batch.pk]
    return render(
        request,
        "core/voucher_detail.html",
        {
            "batch": batch,
            "state": key,
            "state_label": label,
            "numbers": numbers,
            "restrictions": restriction_summary(batch),
            "codes": page_obj,
            "page_obj": page_obj,
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
            "codes_filter": status_filter,
            "redemptions": redemptions,
            "remaining_budget": (batch.budget_tzs - numbers["given"]) if batch.budget_tzs is not None else None,
            "can_edit": _require_admin(request, module=MODULE, cap="edit"),
        },
    )


def _export_codes(request, batch):
    audit_event(
        request, action="Voucher Codes Exported", module="Vouchers", target=batch,
        description=f"Downloaded the {batch.codes.count()} code(s) of '{batch.name}'",
    )
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="vouchers-{batch.pk}.csv"'
    writer = csv.writer(response)
    writer.writerow(["code", "status", "max_uses", "uses_held"])
    for voucher in batch.codes.annotate(
        holding=Count("redemptions", filter=Q(redemptions__application__status__in=ACTIVE_APPLICATION_STATUSES))
    ).order_by("created_at", "code"):
        writer.writerow([voucher.display_code, "active" if voucher.is_active else "revoked", voucher.max_uses, voucher.holding])
    return response
