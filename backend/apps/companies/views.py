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
    change_member_role,
    remove_organization_member,
    get_user_organization,
    is_profile_complete,
)
from apps.companies.permissions import (
    AUDIT_VIEW,
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
from apps.core import audit as audit_lib
from apps.core.audit import audit_event
from apps.core.cache import get_regions
from apps.core.models import AuditLog
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
        # Organization-owned: includes documents uploaded by members who have since left.
        return Document.objects.filter(owner_id__in=organization.historical_member_user_ids())
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
            audit_event(
                request,
                action="Organization Profile Updated",
                module="Organization Profile",
                organization=profile_obj,
                target=profile_obj,
                description="Organization profile saved",
                context={"changed": sorted(form.changed_data)},
            )
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
            "former_count": instance.members.filter(membership_status=MembershipStatus.REMOVED).count() if instance else 0,
            "can_view_audit": org_has_perm(request.user, AUDIT_VIEW, instance),
            "assignable_roles": [
                (OrganizationMemberRole.RECRUITER, "Recruiter"),
                (OrganizationMemberRole.MANAGER, "Deputy Manager"),
            ],
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
                phone=form.cleaned_data.get("phone", ""),
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                position=form.cleaned_data.get("position", ""),
                added_by=request.user,
                request=request,
            )
            messages.success(
                request,
                "Team account created. They must verify their email and choose a new password at first sign-in.",
            )
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
    # Tenant scoped: a member id from another organization is a 404, not a removal.
    member = get_object_or_404(OrganizationMember, id=member_id, organization=organization)
    try:
        remove_organization_member(
            organization, member, request.user, request.POST.get("reason", ""), request=request
        )
        messages.success(
            request, "Access revoked. The member now appears under Former Members; their history is kept."
        )
    except ValidationError as exc:
        messages.error(request, exc.message if hasattr(exc, "message") else "; ".join(exc.messages))
    return redirect("organization-profile")


@login_required
@org_permission_required(MEMBERS_MANAGE)
def change_member_role_view(request, member_id):
    if not request.user.is_organization or request.method != "POST":
        return HttpResponseForbidden("Organization account and POST required.")
    organization = _get_organization(request, require_complete=False)
    if organization is None:
        return redirect("organization-profile")
    member = get_object_or_404(OrganizationMember, id=member_id, organization=organization)
    try:
        change_member_role(organization, member, request.POST.get("role", ""), request.user, request=request)
        messages.success(request, "Role updated. The member must sign in again to use it.")
    except ValidationError as exc:
        messages.error(request, exc.message if hasattr(exc, "message") else "; ".join(exc.messages))
    return redirect("organization-profile")


@login_required
@org_permission_required(MEMBERS_MANAGE)
def former_members(request):
    organization = _get_organization(request, require_complete=False)
    if organization is None:
        return redirect("organization-profile")
    former = (
        organization.members.filter(membership_status=MembershipStatus.REMOVED)
        .select_related("user", "removed_by")
        .order_by("-removed_at")
    )
    return render(
        request,
        "organization/former_members.html",
        {"organization": organization, "former": former},
    )


def _org_for_audit(request):
    return get_user_organization(request.user)


@login_required
@org_permission_required(AUDIT_VIEW)
def audit_logs(request):
    from apps.core.pagination import paginate
    from django.http import HttpResponse
    from urllib.parse import urlencode

    organization = _org_for_audit(request)
    if organization is None:
        return redirect("organization-profile")
    base = AuditLog.objects.filter(organization=organization)
    params = {k: v for k, v in request.GET.items() if k != "organization"}  # never cross tenants
    qs = audit_lib.filter_audit_logs(base, params).select_related("actor")
    if audit_lib.has_filters(params):
        audit_event(
            request,
            action="Audit Log Searched",
            module="Audit",
            organization=organization,
            description="Searched the organization audit log",
            context={k: v for k, v in params.items() if k in audit_lib.FILTER_KEYS and v},
        )
    if request.GET.get("export") == "csv":
        audit_event(
            request,
            action="Audit Log Exported",
            module="Audit",
            organization=organization,
            description="Exported the organization audit log (CSV)",
        )
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="organization-audit.csv"'
        return audit_lib.write_csv(response, qs.order_by("-created_at"))
    page_obj = paginate(qs.order_by("-created_at"), request.GET.get("page"), page_size=50)
    actions = list(base.values_list("action", flat=True).distinct().order_by("action"))
    return render(
        request,
        "organization/audit_logs.html",
        {
            "organization": organization,
            "logs": page_obj,
            "page_obj": page_obj,
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
            "actions": actions,
            "f": params,
        },
    )


@login_required
@org_permission_required(AUDIT_VIEW)
def audit_detail(request, log_id):
    organization = _org_for_audit(request)
    log = get_object_or_404(AuditLog, id=log_id, organization=organization)
    return render(
        request,
        "core/audit_detail.html",
        {"log": log, "back_url": "organization-audit-logs", "scope": "organization"},
    )


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
            audit_event(
                request,
                action="Document Uploaded",
                module="Documents",
                organization=organization,
                target=doc,
                target_label=f"{doc.get_doc_type_display()} ({doc.original_name})",
                description=f"Uploaded {doc.get_doc_type_display()}",
            )
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
        doc = get_object_or_404(Document, id=doc_id, owner_id__in=organization.historical_member_user_ids())
    else:
        doc = get_object_or_404(Document, id=doc_id, owner=request.user)
    doc_type_name = doc.get_doc_type_display()
    audit_event(
        request,
        action="Document Deleted",
        module="Documents",
        organization=organization,
        target_type="Document",
        target_id=str(doc.id),
        target_label=f"{doc_type_name} ({doc.original_name})",
        description=f"Deleted {doc_type_name}",
    )
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
            audit_event(
                request, action="Slot Created", module="Opportunities", organization=organization,
                target=slot, description=f"Created slot {slot.title}",
            )
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
            audit_event(
                request, action="Slot Edited", module="Opportunities", organization=organization,
                target=slot, description=f"Edited slot {slot.title}",
                context={"changed": sorted(form.changed_data)},
            )
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
    audit_event(
        request, action="Slot Published" if slot.status == SlotStatus.OPEN else "Slot Paused",
        module="Opportunities", organization=organization, target=slot,
        description=f"Slot {slot.title} is now {slot.status}",
    )
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
    audit_event(
        request, action="Slot Deleted", module="Opportunities", organization=organization,
        target_type="Slot", target_id=str(slot.id), target_label=slot.title,
        description=f"Deleted slot {slot.title}",
    )
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
    audit_event(
        request, action="Applicant Documents Viewed", module="Documents", organization=organization,
        target=app, target_label=f"{app.student.user.email} -> {app.slot.title}",
        description="Opened an applicant's documents",
    )
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
    audit_event(
        request, action="Application Accepted", module="Applications", organization=organization,
        target=app, target_label=f"{app.student.user.email} -> {app.slot.title}",
        description="Accepted an application and sent confirmation",
    )
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