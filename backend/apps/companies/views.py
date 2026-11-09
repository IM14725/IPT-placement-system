import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpResponseForbidden, HttpResponseGone
from django.shortcuts import get_object_or_404, redirect, render

from apps.applications.models import Application, ApplicationStatus
from apps.applications.services import (
    accept_application,
    get_organization_visible_applications,
)
from apps.companies.forms import (
    OrganizationDocumentUploadForm,
    OrganizationProfileForm,
    OrganizationMemberCreateForm,
    SlotForm,
)
from apps.companies.models import (
    MAX_ORGANIZATION_ACCOUNTS,
    MAX_ORGANIZATION_CONCURRENT_LOGINS,
    Organization,
    MembershipStatus,
    OrganizationMember,
    OrganizationMemberRole,
)
from apps.companies.services import (
    active_organization_session_count,
    add_organization_member,
    get_user_organization,
    is_profile_complete,
)
from apps.companies.permissions import (
    APPLICANTS_ACCEPT,
    APPLICANTS_VIEW,
    DOCUMENTS_MANAGE,
    MEMBERS_MANAGE,
    ORG_VIEW,
    PROFILE_EDIT,
    SLOTS_MANAGE,
    SLOTS_VIEW,
    org_has_perm,
    org_permission_required,
)
from apps.core.cache import get_regions
from apps.core.rate_limit import token_bucket
from apps.documents.models import Document
from apps.slots.models import Slot, SlotStatus


def _get_organization(request, require_complete=True):
    # Registration creates a stub profile (name only, no industry). Treat it as "no
    # organization yet" for every gated view; only the profile form needs the raw stub.
    organization = get_user_organization(request.user)
    if require_complete and not is_profile_complete(organization):
        return None
    return organization


def _org_documents_qs(organization, fallback_user):
    if organization is not None:
        return Document.objects.filter(owner_id__in=organization.member_user_ids())
    return Document.objects.filter(owner=fallback_user)


@login_required
@org_permission_required(ORG_VIEW)
def dashboard(request):
    if not request.user.is_organization:
        return HttpResponseForbidden("Organization account required.")
    organization = _get_organization(request)
    if organization is None:
        messages.info(request, "Complete your organization profile first.")
        return redirect("organization-profile")
        
    doc_types = set(_org_documents_qs(organization, request.user).values_list("doc_type", flat=True))
    required = {Document.DocType.BRELA_CERT, Document.DocType.TIN_CERT, Document.DocType.BUSINESS_LICENSE}
    if not required.issubset(doc_types):
        messages.warning(request, "Please upload all 3 required organization documents before proceeding.")
        return redirect("organization-profile")
        
    slots = Slot.objects.filter(organization=organization).order_by("-created_at")[:8]
    return render(
        request,
        "organization/dashboard.html",
        {"organization": organization, "slots": slots},
    )


@login_required
@org_permission_required(ORG_VIEW)
def profile(request):
    if not request.user.is_organization:
        return HttpResponseForbidden("Organization account required.")
    instance = _get_organization(request, require_complete=False)
    if request.method == "POST":
        if not org_has_perm(request.user, PROFILE_EDIT, instance):
            return HttpResponseForbidden("Your organization role does not allow editing the profile.")
        form = OrganizationProfileForm(request.POST, instance=instance)
        if form.is_valid():
            profile_obj = form.save(commit=False)
            if not profile_obj.user_id:
                profile_obj.user = request.user
            profile_obj.save()
            messages.success(request, "Organization profile saved.")
            return redirect("organization-dashboard")
    else:
        form = OrganizationProfileForm(instance=instance)

    docs = _org_documents_qs(instance, request.user)
    upload_form = OrganizationDocumentUploadForm(user=request.user, organization=instance)
    regions = get_regions()
    members = (
        instance.members.filter(membership_status=MembershipStatus.ACTIVE).select_related("user")
        if instance
        else []
    )
    member_count = len(members)
    session_count = active_organization_session_count(instance) if instance else 0
    return render(
        request,
        "organization/profile.html",
        {
            "form": form,
            "organization": instance,
            "docs": docs,
            "upload_form": upload_form,
            "regions_json": json.dumps(regions),
            "members": members,
            "member_count": member_count,
            "max_members": MAX_ORGANIZATION_ACCOUNTS,
            "can_add_member": bool(
                instance
                and member_count < MAX_ORGANIZATION_ACCOUNTS
                and org_has_perm(request.user, MEMBERS_MANAGE, instance)
            ),
            "can_edit_profile": org_has_perm(request.user, PROFILE_EDIT, instance),
            "can_manage_members": org_has_perm(request.user, MEMBERS_MANAGE, instance),
            "can_manage_documents": org_has_perm(request.user, DOCUMENTS_MANAGE, instance),
            "member_form": OrganizationMemberCreateForm(),
            "session_count": session_count,
            "max_sessions": MAX_ORGANIZATION_CONCURRENT_LOGINS,
        },
    )


@login_required
@org_permission_required(MEMBERS_MANAGE)
def add_member(request):
    if not request.user.is_organization or request.method != "POST":
        return HttpResponseForbidden("Organization account and POST required.")
    organization = _get_organization(request, require_complete=False)
    if organization is None:
        messages.error(request, "Save your organization profile before adding team accounts.")
        return redirect("organization-profile")
    form = OrganizationMemberCreateForm(request.POST)
    if form.is_valid():
        try:
            add_organization_member(
                organization,
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
                role=form.cleaned_data["role"],
            )
            messages.success(request, "Organization team account created.")
        except ValidationError as exc:
            messages.error(request, exc.message if hasattr(exc, "message") else "; ".join(exc.messages))
    else:
        for field_errors in form.errors.values():
            for err in field_errors:
                messages.error(request, err)
    return redirect("organization-profile")


@login_required
@org_permission_required(MEMBERS_MANAGE)
def remove_member(request, member_id):
    if not request.user.is_organization or request.method != "POST":
        return HttpResponseForbidden("Organization account and POST required.")
    organization = _get_organization(request, require_complete=False)
    if organization is None:
        return redirect("organization-profile")
    member = get_object_or_404(OrganizationMember, id=member_id, organization=organization)
    if member.user_id == request.user.id or member.user_id == organization.user_id:
        messages.error(request, "Cannot remove the primary organization owner account.")
        return redirect("organization-profile")
    member_user = member.user
    member.delete()
    member_user.is_active = False
    member_user.save(update_fields=["is_active"])
    messages.success(request, "Organization team account removed.")
    return redirect("organization-profile")


@login_required
@org_permission_required(DOCUMENTS_MANAGE)
def upload_document(request):
    if not request.user.is_organization:
        return HttpResponseForbidden("Organization account required.")
    organization = _get_organization(request, require_complete=False)
    form = OrganizationDocumentUploadForm(request.POST, request.FILES, user=request.user, organization=organization)
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
            messages.success(request, "Organization document uploaded for review.")
        except ValidationError as exc:
            messages.error(request, exc.message)
    return redirect("organization-profile")


@login_required
@org_permission_required(DOCUMENTS_MANAGE)
def delete_document(request, doc_id):
    if not request.user.is_organization or request.method != "POST":
        return HttpResponseForbidden("Organization account and POST required.")
    organization = _get_organization(request, require_complete=False)
    if organization is not None:
        doc = get_object_or_404(Document, id=doc_id, owner_id__in=organization.member_user_ids())
    else:
        doc = get_object_or_404(Document, id=doc_id, owner=request.user)
    doc_type_name = doc.get_doc_type_display()
    doc.file.delete(save=False)
    doc.delete()
    messages.success(request, f"{doc_type_name} deleted. You must upload a replacement.")
    return redirect("organization-profile")


def _require_approved_organization(request):
    organization = _get_organization(request)
    if organization is None:
        messages.info(request, "Complete your organization profile first.")
        return None
        
    doc_types = set(_org_documents_qs(organization, request.user).values_list("doc_type", flat=True))
    required = {Document.DocType.BRELA_CERT, Document.DocType.TIN_CERT, Document.DocType.BUSINESS_LICENSE}
    if not required.issubset(doc_types):
        messages.warning(request, "Please upload all 3 required organization documents before proceeding.")
        return None

    if not organization.is_approved:
        messages.warning(request, "Your organization must be approved by the platform admin to post slots.")
        return None
    return organization


@login_required
@org_permission_required(SLOTS_VIEW)
def slot_list(request):
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    slots = (
        Slot.objects.filter(organization=organization)
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
        "organization/slots.html",
        {
            "organization": organization,
            "slots": slots,
            "total_capacity": total_capacity,
            "total_filled": total_filled,
            "fill_percent": fill_percent,
            "total_applications": total_applications,
        },
    )


@login_required
@org_permission_required(SLOTS_MANAGE)
@token_bucket(capacity=10, refill_per_second=1 / 30, scope="organization-slot", methods=["POST"])
def slot_create(request):
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    regions = get_regions()
    if request.method == "POST":
        form = SlotForm(request.POST)
        if form.is_valid():
            slot = form.save(commit=False)
            slot.organization = organization
            slot.save()
            messages.success(request, "Slot created and now visible to students.")
            return redirect("organization-slots")
    else:
        form = SlotForm()
    return render(
        request,
        "organization/slot_form.html",
        {"form": form, "regions_json": json.dumps(regions), "editing": False},
    )


@login_required
@org_permission_required(SLOTS_MANAGE)
@token_bucket(capacity=10, refill_per_second=1 / 30, scope="organization-slot", methods=["POST"])
def slot_edit(request, slot_id):
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    slot = get_object_or_404(Slot, id=slot_id, organization=organization)
    regions = get_regions()
    if request.method == "POST":
        form = SlotForm(request.POST, instance=slot)
        if form.is_valid():
            form.save()
            messages.success(request, "Slot updated.")
            return redirect("organization-slots")
    else:
        form = SlotForm(instance=slot)
    return render(
        request,
        "organization/slot_form.html",
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
@org_permission_required(SLOTS_MANAGE)
def slot_toggle(request, slot_id):
    if request.method != "POST":
        return HttpResponseForbidden("POST required.")
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    slot = get_object_or_404(Slot, id=slot_id, organization=organization)
    if slot.status == SlotStatus.PAUSED:
        slot.status = SlotStatus.OPEN
    else:
        slot.status = SlotStatus.PAUSED
    slot.save(update_fields=["status", "updated_at"])
    return redirect("organization-slots")


@login_required
@org_permission_required(SLOTS_MANAGE)
def slot_delete(request, slot_id):
    if request.method != "POST":
        return HttpResponseForbidden("POST required.")
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    slot = get_object_or_404(Slot, id=slot_id, organization=organization)
    slot.delete()
    messages.success(request, "Slot deleted.")
    return redirect("organization-slots")


@login_required
@org_permission_required(APPLICANTS_VIEW)
def applicants(request, slot_id):
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    slot = get_object_or_404(Slot, id=slot_id, organization=organization)
    from apps.core.education import education_level_choices
    from apps.core.pagination import paginate
    from urllib.parse import urlencode

    education_level = request.GET.get("education_level") or None
    qs = get_organization_visible_applications(slot).select_related(
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
        "organization/applicants.html",
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
@org_permission_required(APPLICANTS_VIEW)
def applicant_documents(request, app_id):
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    app = get_object_or_404(
        Application,
        id=app_id,
        slot__organization=organization,
    )
    if app.status != ApplicationStatus.PAID:
        return HttpResponseForbidden("This application has not been paid.")
    docs = Document.objects.filter(owner=app.student.user)
    return render(
        request,
        "organization/student_documents.html",
        {"application": app, "docs": docs},
    )


@login_required
@org_permission_required(APPLICANTS_ACCEPT)
def send_acceptance_sms(request, app_id):
    if request.method != "POST":
        return HttpResponseForbidden("POST required.")
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    app = get_object_or_404(
        Application, id=app_id, slot__organization=organization
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
    return redirect("organization-applicants", slot_id=app.slot_id)


@login_required
@org_permission_required(APPLICANTS_VIEW)
def view_application_letter(request, app_id):
    from apps.documents.validators import build_safe_file_response

    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    app = get_object_or_404(
        Application, id=app_id, slot__organization=organization
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
    return build_safe_file_response(app.application_letter, filename=filename)


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