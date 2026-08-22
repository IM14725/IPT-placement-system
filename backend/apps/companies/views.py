import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpResponseForbidden, HttpResponseGone
from django.shortcuts import get_object_or_404, redirect, render

from apps.applications.models import Application, ApplicationStatus
from apps.applications.services import (
    accept_application,
    get_company_visible_applications,
)
from apps.companies.forms import (
    CompanyDocumentUploadForm,
    CompanyProfileForm,
    SlotForm,
)
from apps.companies.models import CompanyProfile
from apps.core.cache import get_regions
from apps.core.rate_limit import token_bucket
from apps.documents.models import Document
from apps.slots.models import Slot, SlotStatus


def _get_company(request):
    return CompanyProfile.objects.filter(user=request.user).first()


@login_required
def dashboard(request):
    if not request.user.is_company:
        return HttpResponseForbidden("Company account required.")
    company = _get_company(request)
    slots = Slot.objects.filter(company=company).order_by("-created_at")[:8]
    return render(
        request,
        "company/dashboard.html",
        {"company": company, "slots": slots},
    )


@login_required
def profile(request):
    if not request.user.is_company:
        return HttpResponseForbidden("Company account required.")
    instance = _get_company(request)
    if request.method == "POST":
        form = CompanyProfileForm(request.POST, instance=instance)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user
            profile.save()
            messages.success(request, "Company profile saved.")
            return redirect("company-dashboard")
    else:
        form = CompanyProfileForm(instance=instance, initial={"name": request.user.first_name})

    docs = Document.objects.filter(owner=request.user)
    upload_form = CompanyDocumentUploadForm()
    regions = get_regions()
    return render(
        request,
        "company/profile.html",
        {
            "form": form,
            "company": instance,
            "docs": docs,
            "upload_form": upload_form,
            "regions_json": json.dumps(regions),
        },
    )


@login_required
def upload_document(request):
    if not request.user.is_company:
        return HttpResponseForbidden("Company account required.")
    form = CompanyDocumentUploadForm(request.POST, request.FILES)
    if form.is_valid():
        file = request.FILES["file"]
        try:
            doc = Document.objects.create(
                owner=request.user,
                doc_type=form.cleaned_data["doc_type"],
                file=file,
                original_name=file.name,
            )
            from apps.core.cache import enqueue_once

            enqueue_once("documents.scan", [doc.id])
            messages.success(request, "Corporate document uploaded for review.")
        except ValidationError as exc:
            messages.error(request, exc.message)
    return redirect("company-profile")


def _require_approved_company(request):
    company = _get_company(request)
    if company is None:
        messages.info(request, "Complete your company profile first.")
        return None
    if not company.is_approved:
        messages.warning(request, "Your company must be approved by the platform admin to post slots.")
        return None
    return company


@login_required
def slot_list(request):
    company = _require_approved_company(request)
    if company is None:
        return redirect("company-profile")
    slots = (
        Slot.objects.filter(company=company)
        .select_related("district__region")
        .order_by("-created_at")
    )
    total_capacity = sum(s.capacity for s in slots)
    total_filled = sum(s.applications.count() for s in slots)
    total_applications = sum(s.applications.count() for s in slots)
    for slot in slots:
        slot.fill_percent = min(100, round((slot.applications.count() / slot.capacity) * 100))
    fill_percent = min(100, round((total_filled / total_capacity) * 100)) if total_capacity else 0
    return render(
        request,
        "company/slots.html",
        {
            "company": company,
            "slots": slots,
            "total_capacity": total_capacity,
            "total_filled": total_filled,
            "fill_percent": fill_percent,
            "total_applications": total_applications,
        },
    )


@login_required
@token_bucket(capacity=10, refill_per_second=1 / 30, scope="company-slot", methods=["POST"])
def slot_create(request):
    company = _require_approved_company(request)
    if company is None:
        return redirect("company-profile")
    regions = get_regions()
    if request.method == "POST":
        form = SlotForm(request.POST)
        if form.is_valid():
            slot = form.save(commit=False)
            slot.company = company
            slot.save()
            messages.success(request, "Slot created and now visible to students.")
            return redirect("company-slots")
    else:
        form = SlotForm()
    return render(
        request,
        "company/slot_form.html",
        {"form": form, "regions_json": json.dumps(regions), "editing": False},
    )


@login_required
@token_bucket(capacity=10, refill_per_second=1 / 30, scope="company-slot", methods=["POST"])
def slot_edit(request, slot_id):
    company = _require_approved_company(request)
    if company is None:
        return redirect("company-profile")
    slot = get_object_or_404(Slot, id=slot_id, company=company)
    regions = get_regions()
    if request.method == "POST":
        form = SlotForm(request.POST, instance=slot)
        if form.is_valid():
            form.save()
            messages.success(request, "Slot updated.")
            return redirect("company-slots")
    else:
        form = SlotForm(instance=slot)
    return render(
        request,
        "company/slot_form.html",
        {
            "form": form,
            "regions_json": json.dumps(regions),
            "editing": True,
            "slot": slot,
            "current_region": slot.district.region_id,
            "current_district": slot.district_id,
        },
    )


@login_required
def slot_toggle(request, slot_id):
    if request.method != "POST":
        return HttpResponseForbidden("POST required.")
    company = _require_approved_company(request)
    if company is None:
        return redirect("company-profile")
    slot = get_object_or_404(Slot, id=slot_id, company=company)
    if slot.status == SlotStatus.PAUSED:
        slot.status = SlotStatus.OPEN
    else:
        slot.status = SlotStatus.PAUSED
    slot.save(update_fields=["status", "updated_at"])
    return redirect("company-slots")


@login_required
def slot_delete(request, slot_id):
    if request.method != "POST":
        return HttpResponseForbidden("POST required.")
    company = _require_approved_company(request)
    if company is None:
        return redirect("company-profile")
    slot = get_object_or_404(Slot, id=slot_id, company=company)
    slot.delete()
    messages.success(request, "Slot deleted.")
    return redirect("company-slots")


@login_required
def applicants(request, slot_id):
    company = _require_approved_company(request)
    if company is None:
        return redirect("company-profile")
    slot = get_object_or_404(Slot, id=slot_id, company=company)
    from apps.core.education import education_level_choices
    from apps.core.pagination import paginate
    from urllib.parse import urlencode

    education_level = request.GET.get("education_level") or None
    qs = get_company_visible_applications(slot).select_related(
        "student__user", "payment", "student__district__region"
    )
    if education_level:
        qs = qs.filter(student__education_level=education_level)
    accepted_count = qs.filter(is_accepted=True).count()
    pending_count = qs.filter(is_accepted=False).count()
    fill_percent = min(100, round((qs.count() / slot.capacity) * 100)) if slot.capacity else 0
    page_obj = paginate(qs.order_by("-created_at"), request.GET.get("page"))
    return render(
        request,
        "company/applicants.html",
        {
            "slot": slot,
            "applications": page_obj,
            "page_obj": page_obj,
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
            "accepted_count": accepted_count,
            "pending_count": pending_count,
            "fill_percent": fill_percent,
            "education_levels": education_level_choices(),
            "selected_education_level": education_level or "",
        },
    )


@login_required
def applicant_documents(request, app_id):
    company = _require_approved_company(request)
    if company is None:
        return redirect("company-profile")
    app = get_object_or_404(
        Application,
        id=app_id,
        slot__company=company,
    )
    if app.status != ApplicationStatus.PAID:
        return HttpResponseForbidden("This application has not been paid.")
    docs = Document.objects.filter(owner=app.student.user)
    return render(
        request,
        "company/student_documents.html",
        {"application": app, "docs": docs},
    )


@login_required
def send_acceptance_sms(request, app_id):
    if request.method != "POST":
        return HttpResponseForbidden("POST required.")
    company = _require_approved_company(request)
    if company is None:
        return redirect("company-profile")
    app = get_object_or_404(
        Application, id=app_id, slot__company=company
    )
    if app.status != ApplicationStatus.PAID:
        return HttpResponseForbidden("Not paid.")
    message = request.POST.get("message", "").strip()
    from apps.applications.tasks import acceptance_email, acceptance_sms

    accept_application(app, company_message=message)
    acceptance_sms.delay(app.id, message, actor_id=request.user.id)
    acceptance_email.delay(app.id, message)
    messages.success(
        request,
        "Application confirmed. Confirmation SMS and email queued to "
        + app.student.user.phone,
    )
    return redirect("company-applicants", slot_id=app.slot_id)


@login_required
def view_application_letter(request, app_id):
    company = _require_approved_company(request)
    if company is None:
        return redirect("company-profile")
    app = get_object_or_404(
        Application, id=app_id, slot__company=company
    )
    if app.status != ApplicationStatus.PAID:
        return HttpResponseForbidden("This application has not been paid.")
    if not app.application_letter:
        return HttpResponseForbidden("No application letter uploaded.")
    if app.letter_sha256:
        app.application_letter.open("rb")
        try:
            from apps.core.immutability import sha256_bytes

            actual = sha256_bytes(app.application_letter.read())
        finally:
            app.application_letter.close()
        if actual != app.letter_sha256:
            return HttpResponseGone(
                "This application letter failed its integrity check and is unavailable."
            )
    filename = app.letter_original_name or "application_letter.pdf"
    return FileResponse(
        app.application_letter.open("rb"),
        content_type=_mime_for_filename(filename) or "application/pdf",
        filename=filename,
    )


def _mime_for_filename(filename):
    """Best-effort content type so browsers can preview the file inline."""
    from pathlib import Path

    ext = Path(filename).suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(ext)