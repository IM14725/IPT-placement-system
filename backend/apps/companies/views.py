from django.utils.formats import date_format
from django.utils.translation import gettext, ngettext
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import FileResponse, HttpResponseForbidden, HttpResponseGone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.functional import lazy

from apps.applications.models import ApplicantNote, Application, ApplicationStatus
from apps.applications.services import (
    AcceptanceError,
    accept_and_notify,
    acceptance_blocker,
    get_organization_visible_applications,
)
from apps.companies.forms import (
    OrganizationDocumentUploadForm,
    OrganizationLogoForm,
    OrganizationProfileForm,
    OrganizationMemberCreateForm,
    SlotForm,
)
from apps.companies.models import (
    MAX_ACCEPTANCE_TEMPLATES,
    MAX_ORGANIZATION_ACCOUNTS,
    AcceptanceTemplate,
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
from apps.accounts.views_account import security_context
from apps.core import audit as audit_lib
from apps.core.audit import audit_event
from apps.core.cache import get_regions
from apps.core.models import AuditLog
from apps.core.rate_limit import token_bucket
from apps.core.security import safe_json_dumps
from apps.documents.models import Document
from apps.slots.models import Slot, SlotStatus


# Team-account actions return to the profile page's "Team accounts" tab.
TEAM_TAB_URL = lazy(lambda: reverse("organization-profile") + "#team", str)()


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
        return HttpResponseForbidden(gettext("Organization account required."))
    organization = _get_organization(request)
    if organization is None:
        messages.info(request, gettext("Complete your organization profile first."))
        return redirect("organization-profile")
        
    doc_types = set(_org_documents_qs(organization, request.user).values_list("doc_type", flat=True))
    required = {Document.DocType.BRELA_CERT, Document.DocType.TIN_CERT, Document.DocType.BUSINESS_LICENSE}
    if not required.issubset(doc_types):
        messages.warning(request, gettext("Please upload all 3 required organization documents before proceeding."))
        return redirect("organization-profile")
        
    from apps.companies import dashboard as dash

    user = request.user
    can_view_applicants = org_has_perm(user, APPLICANTS_VIEW, organization)
    can_accept = org_has_perm(user, APPLICANTS_ACCEPT, organization)
    can_manage_slots = org_has_perm(user, SLOTS_MANAGE, organization)
    can_view_audit = org_has_perm(user, AUDIT_VIEW, organization)
    slots = dash.slots_with_counts(organization) if organization.is_approved else []
    recent = dash.recent_applicants(organization) if (organization.is_approved and can_view_applicants) else []
    for app in recent:
        app.blocker = acceptance_blocker(app)
    return render(
        request,
        "organization/dashboard.html",
        {
            "organization": organization,
            "slots": slots[:6],
            "slot_total": len(slots),
            "kpis": dash.kpis(slots),
            "todo": dash.todo_items(
                organization,
                slots,
                can_view_applicants=can_view_applicants,
                can_manage_slots=can_manage_slots,
                can_manage_documents=org_has_perm(user, DOCUMENTS_MANAGE, organization),
            ),
            "recent_applicants": recent,
            "activity": dash.team_activity(organization, limit=5) if can_view_audit else [],
            "can_view_applicants": can_view_applicants,
            "can_accept": can_accept,
            "can_manage_slots": can_manage_slots,
            "can_view_audit": can_view_audit,
        },
    )


@login_required
@org_permission_required(ORG_VIEW)
def profile(request):
    # Tabs: #profile (default), #team (team accounts), #security (sign-in details); see profile.html.
    if not request.user.is_organization:
        return HttpResponseForbidden(gettext("Organization account required."))
    instance = _get_organization(request, require_complete=False)
    if request.method == "POST":
        if not org_has_perm(request.user, PROFILE_EDIT, instance):
            return HttpResponseForbidden(gettext("Your organization role does not allow editing the profile."))
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
            messages.success(request, gettext("Organization profile saved."))
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
    my_membership = next((m for m in members if m.user_id == request.user.id), None)  # for the account header
    session_count = active_organization_session_count(instance) if instance else 0
    return render(
        request,
        "organization/profile.html",
        {
            "form": form,
            "organization": instance,
            "docs": docs,
            "upload_form": upload_form,
            "regions_json": safe_json_dumps(regions),
            "members": members,
            "my_membership": my_membership,
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
            **security_context(request),
        },
    )


@login_required
@org_permission_required(MEMBERS_MANAGE)
def add_member(request):
    if not request.user.is_organization or request.method != "POST":
        return HttpResponseForbidden(gettext("Organization account and POST required."))
    organization = _get_organization(request, require_complete=False)
    if organization is None:
        messages.error(request, gettext("Save your organization profile before adding team accounts."))
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
                gettext("Team account created. They must verify their email and choose a new password at first sign-in."),
            )
        except ValidationError as exc:
            messages.error(request, exc.message if hasattr(exc, "message") else "; ".join(exc.messages))
    else:
        for field_errors in form.errors.values():
            for err in field_errors:
                messages.error(request, err)
    return redirect(TEAM_TAB_URL)


@login_required
@org_permission_required(MEMBERS_MANAGE)
def remove_member(request, member_id):
    if not request.user.is_organization or request.method != "POST":
        return HttpResponseForbidden(gettext("Organization account and POST required."))
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
            request, gettext("Access revoked. The member now appears under Former Members; their history is kept.")
        )
    except ValidationError as exc:
        messages.error(request, exc.message if hasattr(exc, "message") else "; ".join(exc.messages))
    return redirect(TEAM_TAB_URL)


@login_required
@org_permission_required(MEMBERS_MANAGE)
def change_member_role_view(request, member_id):
    if not request.user.is_organization or request.method != "POST":
        return HttpResponseForbidden(gettext("Organization account and POST required."))
    organization = _get_organization(request, require_complete=False)
    if organization is None:
        return redirect("organization-profile")
    member = get_object_or_404(OrganizationMember, id=member_id, organization=organization)
    try:
        change_member_role(organization, member, request.POST.get("role", ""), request.user, request=request)
        messages.success(request, gettext("Role updated. The member must sign in again to use it."))
    except ValidationError as exc:
        messages.error(request, exc.message if hasattr(exc, "message") else "; ".join(exc.messages))
    return redirect(TEAM_TAB_URL)


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
@token_bucket(capacity=10, refill_per_second=1 / 30, scope="org-doc-upload", methods=["POST"])
def upload_document(request):
    if not request.user.is_organization:
        return HttpResponseForbidden(gettext("Organization account required."))
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
            messages.success(request, gettext("Organization document uploaded for review."))
        except ValidationError as exc:
            messages.error(request, exc.message)
    return redirect("organization-profile")


@login_required
@org_permission_required(PROFILE_EDIT)
@token_bucket(capacity=10, refill_per_second=1 / 30, scope="org-logo-upload", methods=["POST"])
def upload_logo(request):
    """Set (``logo`` file) or clear (``action=remove``) the organization's picture shown in the account header."""
    if not request.user.is_organization or request.method != "POST":
        return HttpResponseForbidden(gettext("Organization account and POST required."))
    organization = _get_organization(request, require_complete=False)
    if organization is None:
        messages.error(request, gettext("Save your organization profile before adding a logo."))
        return redirect("organization-profile")
    if request.POST.get("action") == "remove":
        if organization.logo:
            organization.logo.delete(save=False)
            organization.logo = ""
            organization.save(update_fields=["logo", "updated_at"])
            audit_event(
                request,
                action="Organization Logo Removed",
                module="Organization Profile",
                organization=organization,
                target=organization,
                description="Removed the organization logo",
            )
            messages.success(request, gettext("Logo removed."))
        return redirect("organization-profile")
    form = OrganizationLogoForm(request.POST, request.FILES)
    if form.is_valid():
        old = organization.logo
        organization.logo = form.cleaned_data["logo"]
        organization.save(update_fields=["logo", "updated_at"])
        if old and old.name != organization.logo.name:
            old.delete(save=False)  # drop the replaced file
        audit_event(
            request,
            action="Organization Logo Updated",
            module="Organization Profile",
            organization=organization,
            target=organization,
            description="Updated the organization logo",
        )
        messages.success(request, gettext("Logo updated."))
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect("organization-profile")


@login_required
@org_permission_required(DOCUMENTS_MANAGE)
def delete_document(request, doc_id):
    if not request.user.is_organization or request.method != "POST":
        return HttpResponseForbidden(gettext("Organization account and POST required."))
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
    messages.success(request, gettext('%(doc_type_name)s deleted. You must upload a replacement.') % {"doc_type_name": doc_type_name})
    return redirect("organization-profile")


def _require_approved_organization(request):
    organization = _get_organization(request)
    if organization is None:
        messages.info(request, gettext("Complete your organization profile first."))
        return None
        
    doc_types = set(_org_documents_qs(organization, request.user).values_list("doc_type", flat=True))
    required = {Document.DocType.BRELA_CERT, Document.DocType.TIN_CERT, Document.DocType.BUSINESS_LICENSE}
    if not required.issubset(doc_types):
        messages.warning(request, gettext("Please upload all 3 required organization documents before proceeding."))
        return None

    if not organization.is_approved:
        messages.warning(request, gettext("Your organization must be approved by the platform admin to post slots."))
        return None
    return organization


@login_required
@org_permission_required(SLOTS_VIEW)
def slot_list(request):
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    from apps.companies import dashboard as dash

    # One query for every slot's seat counts (the same numbers the dashboard shows). "Filled" is the
    # seats held (PENDING + PAID), matching the capacity rule; "applicants" are the paid ones the
    # organization can actually see, not unpaid or cancelled attempts.
    slots = dash.slots_with_counts(organization)
    total_capacity = sum(s.capacity for s in slots)
    total_filled = sum(s.booked for s in slots)
    total_applications = sum(s.paid_count for s in slots)
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
            slot.industry = (slot.industry or organization.industry or "").strip()[:150]
            slot.save()
            audit_event(
                request, action="Slot Created", module="Opportunities", organization=organization,
                target=slot, description=f"Created slot {slot.title}",
            )
            _alert_matching_students(slot)
            messages.success(request, gettext("Slot created and now visible to students."))
            return redirect("organization-slots")
    else:
        form = SlotForm(initial={"industry": organization.industry})
    return render(
        request,
        "organization/slot_form.html",
        {"form": form, "regions_json": safe_json_dumps(regions), "editing": False},
    )


def _alert_matching_students(slot):
    """After commit, notify students whose saved searches (or saved slots) match this slot."""
    from django.db import transaction

    def _send():
        from apps.students.tasks import match_saved_searches

        try:
            match_saved_searches.delay(str(slot.id))
        except Exception:  # noqa: BLE001 - alerts are best effort; posting the slot must not fail
            pass

    transaction.on_commit(_send)


@login_required
@org_permission_required(SLOTS_MANAGE)
def slot_duplicate(request, slot_id):
    """Open the "new slot" form pre-filled from an existing slot ("post again")."""
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    source = get_object_or_404(Slot, id=slot_id, organization=organization)
    initial = {
        name: getattr(source, name)
        for name in (
            "title", "industry", "description", "role_type", "department", "education_level", "academic_year",
            "duration_weeks", "reporting_contact", "capacity", "stipend_available", "stipend_amount",
        )
    }
    from apps.core.skill_fields import skills_to_text

    initial["skills_required"] = skills_to_text(source.skills_required)
    # The deadline is left empty on purpose: the old one is usually in the past.
    form = SlotForm(initial=initial)
    messages.info(request, gettext('Copied from “%(v1)s”. Check the details, set a new deadline and save.') % {"v1": source.title})
    return render(
        request,
        "organization/slot_form.html",
        {
            "form": form,
            "regions_json": safe_json_dumps(get_regions()),
            "editing": False,
            "form_action": reverse("organization-slot-create"),
            "current_region": source.district.region_id,
            "current_district": source.district_id,
        },
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
    # Taken before the form touches the instance: validating a ModelForm already writes the new values onto it.
    before = _material_snapshot(slot)
    holders = _seat_holder_users(slot)
    pending_changes = None
    if request.method == "POST":
        form = SlotForm(request.POST, instance=slot)
        if form.is_valid():
            changes = _material_changes(before, _material_snapshot(slot))
            if changes and holders and not request.POST.get("confirm_changes"):
                # Students already hold seats on the strength of these details: confirm first.
                pending_changes = changes
            else:
                slot = form.save(commit=False)
                slot.industry = (slot.industry or organization.industry or "").strip()[:150]
                slot.save()
                # A new capacity can make the slot full or free up seats again.
                slot.refresh_status()
                audit_event(
                    request, action="Slot Edited", module="Opportunities", organization=organization,
                    target=slot, description=f"Edited slot {slot.title}",
                    context={"changed": sorted(form.changed_data)},
                )
                if changes and holders:
                    _notify_slot_changed(slot, organization, holders, changes, request.user)
                    messages.success(
                        request, ngettext(
                            'Slot updated. %(n)s student was notified of the change.',
                            'Slot updated. %(n)s students were notified of the change.',
                            len(holders),
                        ) % {"n": len(holders)}
                    )
                else:
                    messages.success(request, gettext("Slot updated."))
                return redirect("organization-slots")
    else:
        form = SlotForm(instance=slot)
    return render(
        request,
        "organization/slot_form.html",
        {
            "form": form,
            "regions_json": safe_json_dumps(regions),
            "editing": True,
            "slot": slot,
            "current_region": slot.district.region_id,
            "current_district": slot.district_id,
            "holder_count": len(holders),
            "pending_changes": pending_changes,
        },
    )


# What a student weighs when they decide to apply and pay: change one of these after seats are
# held and those students must be told (see slot_edit).
def _material_snapshot(slot):
    stipend = (
        f"TZS {slot.stipend_amount:,.0f} per month"
        if slot.stipend_available and slot.stipend_amount
        else "No stipend"
    )
    return {
        "Stipend": stipend,
        "Location": f"{slot.district.name}, {slot.district.region.name}",
        "Qualification": slot.requirement_label or "Any",
        "Start date": f"{slot.start_date.day} {slot.start_date:%b %Y}" if slot.start_date else "Not set",
        "Duration": f"{slot.duration_weeks} weeks" if slot.duration_weeks else "Not set",
    }


def _material_changes(before, after):
    return {label: (before[label], after[label]) for label in before if before[label] != after[label]}


def _seat_holder_users(slot):
    """Students who currently hold a seat on the slot (awaiting payment, or paid)."""
    return [
        a.student.user
        for a in Application.objects.filter(
            slot=slot, status__in=[ApplicationStatus.PENDING, ApplicationStatus.PAID]
        ).select_related("student__user")
    ]


def _notify_slot_changed(slot, organization, users, changes, actor):
    from apps.notifications.inapp import NotificationType, notify_many

    summary = "; ".join(f"{label}: {old} -> {new}" for label, (old, new) in changes.items())
    notify_many(
        users,
        NotificationType.APPLICATION,
        "A slot you applied to was updated",
        f"{organization.name} changed '{slot.title}'. {summary}.",
        link=f"/student/slots/{slot.id}/",
        actor=actor,
    )


EXTEND_CHOICES = (3, 7, 14, 30)


@login_required
@org_permission_required(SLOTS_MANAGE)
def slot_extend(request, slot_id):
    """Push the application deadline back by a few days (from now, or from the current deadline if later)."""
    from datetime import timedelta

    from django.utils import timezone

    if request.method != "POST":
        return HttpResponseForbidden(gettext("POST required."))
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    slot = get_object_or_404(Slot, id=slot_id, organization=organization)
    try:
        days = int(request.POST.get("days") or 0)
    except ValueError:
        days = 0
    if days not in EXTEND_CHOICES:
        messages.error(request, gettext("Choose how many days to extend by."))
        return redirect("organization-slots")
    now = timezone.now()
    base = slot.application_deadline if slot.application_deadline and slot.application_deadline > now else now
    slot.application_deadline = base + timedelta(days=days)
    slot.save(update_fields=["application_deadline", "updated_at"])
    audit_event(
        request, action="Slot Deadline Extended", module="Opportunities", organization=organization,
        target=slot, description=f"Extended the deadline of {slot.title} by {days} days",
        context={"new_deadline": slot.application_deadline.isoformat()},
    )
    messages.success(
        request,
        gettext("Deadline extended to %(date)s.")
        % {"date": date_format(timezone.localtime(slot.application_deadline), "d M Y, H:i")},
    )
    return redirect("organization-slots")


@login_required
@org_permission_required(SLOTS_MANAGE)
def slot_close(request, slot_id):
    """Close a slot to new applications for good (until it is reopened). Existing applicants are untouched."""
    if request.method != "POST":
        return HttpResponseForbidden(gettext("POST required."))
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    slot = get_object_or_404(Slot, id=slot_id, organization=organization)
    if slot.status == SlotStatus.CLOSED:
        slot.status = SlotStatus.FULL if slot.is_full else SlotStatus.OPEN
        action, text = "Slot Reopened", "reopened"
    else:
        slot.status = SlotStatus.CLOSED
        action, text = "Slot Closed", "closed"
    slot.save(update_fields=["status", "updated_at"])
    audit_event(
        request, action=action, module="Opportunities", organization=organization,
        target=slot, description=f"Slot {slot.title} {text}",
    )
    if text == "reopened" and slot.deadline_passed:
        messages.warning(request, gettext("Slot reopened, but its application deadline has passed. Extend the deadline so students can apply."))
    else:
        messages.success(request, gettext("Slot reopened.") if text == "reopened" else gettext("Slot closed."))
    return redirect("organization-slots")


@login_required
@org_permission_required(SLOTS_MANAGE)
def slot_toggle(request, slot_id):
    if request.method != "POST":
        return HttpResponseForbidden(gettext("POST required."))
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    slot = get_object_or_404(Slot, id=slot_id, organization=organization)
    if slot.status == SlotStatus.CLOSED:
        messages.info(request, gettext("This slot is closed. Reopen it instead of pausing."))
        return redirect("organization-slots")
    resumed = slot.status == SlotStatus.PAUSED
    if resumed:
        # Back to what the seat count says: a paused slot that filled up resumes as FULL, not OPEN.
        slot.status = SlotStatus.FULL if slot.is_full else SlotStatus.OPEN
    else:
        slot.status = SlotStatus.PAUSED
    slot.save(update_fields=["status", "updated_at"])
    audit_event(
        request, action="Slot Published" if resumed else "Slot Paused",
        module="Opportunities", organization=organization, target=slot,
        description=f"Slot {slot.title} is now {slot.status}",
    )
    if slot.status == SlotStatus.OPEN:
        _alert_matching_students(slot)
    return redirect("organization-slots")


@login_required
@org_permission_required(SLOTS_MANAGE)
def slot_delete(request, slot_id):
    if request.method != "POST":
        return HttpResponseForbidden(gettext("POST required."))
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    slot = get_object_or_404(Slot, id=slot_id, organization=organization)
    # A slot can be deleted only while nobody has applied to it. Once a student has applied
    # (even unpaid or cancelled), the slot is part of their history: pause it instead.
    if slot.applications.exists():
        audit_event(
            request, action="Slot Delete Refused", module="Opportunities", organization=organization,
            target=slot, outcome="REJECTED", description=f"Slot {slot.title} has applications",
        )
        messages.error(
            request,
            gettext("This slot already has applications, so it cannot be deleted. Pause it to stop new applications."),
        )
        return redirect("organization-slots")
    audit_event(
        request, action="Slot Deleted", module="Opportunities", organization=organization,
        target_type="Slot", target_id=str(slot.id), target_label=slot.title,
        description=f"Deleted slot {slot.title}",
    )
    slot.delete()
    messages.success(request, gettext("Slot deleted."))
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

    from apps.companies import dashboard as dash

    education_level = request.GET.get("education_level") or None
    sort = request.GET.get("sort") if request.GET.get("sort") in ("newest", "earliest") else "newest"
    qs = get_organization_visible_applications(slot).select_related(
        "student__user", "student__university", "payment", "student__district__region"
    ).annotate(note_count=Count("notes"))
    if education_level:
        qs = qs.filter(student__education_level=education_level)
    accepted_count = qs.filter(is_accepted=True).count()
    pending_count = qs.filter(is_accepted=False).count()
    fill_percent = min(100, round((qs.count() / slot.capacity) * 100)) if slot.capacity else 0
    ordering = ("payment__paid_at", "created_at") if sort == "earliest" else ("-payment__paid_at", "-created_at")
    page_obj = paginate(qs.order_by(*ordering), request.GET.get("page"))
    for app in page_obj:  # same rows as All applicants: the 24 h cancellation window blocks acceptance
        app.blocker = acceptance_blocker(app)
    return render(
        request,
        "organization/applicants.html",
        {
            "slot": slot,
            "can_accept": org_has_perm(request.user, APPLICANTS_ACCEPT, organization),
            "templates": AcceptanceTemplate.objects.filter(organization=organization),
            "applications": page_obj,
            "page_obj": page_obj,
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
            "accepted_count": accepted_count,
            "pending_count": pending_count,
            "fill_percent": fill_percent,
            "education_levels": education_level_choices(),
            "selected_education_level": education_level or "",
            "sort": sort,
            "funnel": dash.slot_funnel(slot),
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
        return HttpResponseForbidden(gettext("This application has not been paid."))
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
        return HttpResponseForbidden(gettext("POST required."))
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    app = get_object_or_404(
        Application, id=app_id, slot__organization=organization
    )
    if app.status != ApplicationStatus.PAID:
        return HttpResponseForbidden(gettext("Not paid."))
    message = _acceptance_message(request, organization)
    try:
        accept_and_notify(app, company_message=message, request=request)
    except AcceptanceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            gettext("Application confirmed. The student was notified in the app, and the SMS and email are queued to %(contact)s")
            % {"contact": app.student.user.phone or app.student.user.email},
        )
    return _redirect_next(request, "organization-applicants", slot_id=app.slot_id)


def _uuid_or_404(value):
    """A posted id as a UUID; anything else is a 404 rather than a server error."""
    import uuid

    from django.http import Http404

    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise Http404


def _redirect_next(request, fallback, **kwargs):
    """Go back to the page the form was posted from (same site only), else to ``fallback``."""
    from django.utils.http import url_has_allowed_host_and_scheme

    target = request.POST.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(fallback, **kwargs)


def _acceptance_message(request, organization):
    """The acceptance note: typed text, or else the chosen saved template of this organization."""
    message = (request.POST.get("message") or "").strip()
    template_id = (request.POST.get("template") or "").strip()
    if not message and template_id:
        try:
            template = AcceptanceTemplate.objects.filter(id=template_id, organization=organization).first()
        except (ValueError, ValidationError):
            template = None
        if template:
            message = template.body
    return message[:500]


# ── Applicants across all slots, bulk accept, export ─────────────────────────

def _filtered_applicants(request, organization):
    from django.db.models import Q

    from apps.companies import dashboard as dash

    qs = dash.paid_applications(organization)
    slot_id = (request.GET.get("slot") or "").strip()
    status = request.GET.get("status") or ""
    query = (request.GET.get("q") or "").strip()[:100]
    selected_slot = None
    if slot_id:
        try:
            selected_slot = Slot.objects.filter(id=slot_id, organization=organization).first()
        except (ValueError, ValidationError):
            selected_slot = None
        # An unknown or foreign slot id shows nothing rather than everything.
        qs = qs.filter(slot=selected_slot) if selected_slot else qs.none()
    if status == "accepted":
        qs = qs.filter(is_accepted=True)
    elif status == "waiting":
        qs = qs.filter(is_accepted=False)
    if query:
        qs = qs.filter(
            Q(student__user__first_name__icontains=query)
            | Q(student__user__last_name__icontains=query)
            | Q(student__user__email__icontains=query)
            | Q(student__course__icontains=query)
            | Q(student__university__name__icontains=query)
        )
    return qs.order_by("-payment__paid_at", "-created_at"), {
        "selected_slot": str(selected_slot.id) if selected_slot else "",
        "status": status,
        "q": query,
    }


@login_required
@org_permission_required(APPLICANTS_VIEW)
def all_applicants(request):
    """Every paid applicant of the organization, across slots: filter, accept one or many, export."""
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    from apps.core.pagination import paginate
    from urllib.parse import urlencode

    qs, filters = _filtered_applicants(request, organization)
    page_obj = paginate(qs, request.GET.get("page"))
    for app in page_obj:
        app.blocker = acceptance_blocker(app)
    querystring = urlencode({k: v for k, v in request.GET.items() if k != "page"})
    return render(
        request,
        "organization/all_applicants.html",
        {
            "organization": organization,
            "applications": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
            "total": page_obj.paginator.count,
            "slots": Slot.objects.filter(organization=organization).order_by("title"),
            "templates": AcceptanceTemplate.objects.filter(organization=organization),
            "can_accept": org_has_perm(request.user, APPLICANTS_ACCEPT, organization),
            **filters,
        },
    )


@login_required
@org_permission_required(APPLICANTS_ACCEPT)
@token_bucket(capacity=10, refill_per_second=1 / 30, scope="org-bulk-accept", methods=["POST"])
def bulk_accept(request):
    """Accept several paid applicants with one message. Each one goes through the normal accept flow."""
    if request.method != "POST":
        return HttpResponseForbidden(gettext("POST required."))
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    ids = request.POST.getlist("application")[:50]
    if not ids:
        messages.error(request, gettext("Select at least one applicant to accept."))
        return _redirect_next(request, "organization-all-applicants")
    message = _acceptance_message(request, organization)
    try:
        # Tenant scoped: ids from another organization simply match nothing.
        apps = list(
            Application.objects.filter(id__in=ids, slot__organization=organization)
            .select_related("student__user", "slot__organization", "payment")
        )
    except (ValueError, ValidationError):
        apps = []
    accepted, skipped = 0, []
    for app in apps:
        try:
            accept_and_notify(app, company_message=message, request=request)
            accepted += 1
        except AcceptanceError as exc:
            skipped.append(f"{app.student.user.full_name or app.student.user.email}: {exc}")
    if accepted:
        messages.success(request, gettext('%(accepted)s applicant(s) accepted. Each one was notified in the app, by SMS and by email.') % {"accepted": accepted})
    for line in skipped[:5]:
        messages.error(request, gettext('Not accepted. %(line)s') % {"line": line})
    if len(skipped) > 5:
        messages.error(request, gettext('%(v1)s more applicant(s) could not be accepted yet.') % {"v1": len(skipped) - 5})
    if not accepted and not skipped:
        messages.error(request, gettext("None of the selected applicants could be found."))
    return _redirect_next(request, "organization-all-applicants")


@login_required
@org_permission_required(APPLICANTS_VIEW)
def export_applicants(request):
    """Download the (filtered) applicant list as CSV. Audited: it contains personal data."""
    import csv

    from django.http import HttpResponse

    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    qs, filters = _filtered_applicants(request, organization)
    audit_event(
        request, action="Applicants Exported", module="Applications", organization=organization,
        description="Exported the applicant list (CSV)",
        context={k: v for k, v in filters.items() if v} | {"rows": qs.count()},
    )
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="applicants.csv"'
    response["Cache-Control"] = "no-store"
    response.write("﻿")  # so Excel opens UTF-8 names correctly
    writer = csv.writer(response)
    writer.writerow([
        "Slot", "Student name", "Email", "Phone", "University", "Course", "Education level",
        "Academic year", "GPA", "Home region", "Home district", "Paid at", "Status", "Acceptance note",
    ])

    def safe(value):
        # Neutralise spreadsheet formulas in user-entered text (CSV injection).
        text = "" if value is None else str(value)
        return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text

    for app in qs.iterator(chunk_size=500):
        student = app.student
        writer.writerow([
            safe(app.slot.title),
            safe(student.user.full_name),
            safe(student.user.email),
            safe(student.user.phone),
            safe(student.university or ""),
            safe(student.course),
            safe(student.get_education_level_display() if student.education_level else ""),
            safe(student.get_academic_year_display() if student.academic_year else ""),
            safe(student.gpa if student.gpa is not None else ""),
            safe(student.district.region if student.district_id else ""),
            safe(student.district or ""),
            app.payment.paid_at.strftime("%Y-%m-%d %H:%M") if getattr(app, "payment", None) and app.payment.paid_at else "",
            "Accepted" if app.is_accepted else "Waiting for acceptance",
            safe(app.company_message),
        ])
    return response


@login_required
@org_permission_required(APPLICANTS_VIEW)
def applicant_acceptance_letter(request, app_id):
    """The acceptance letter the student receives, for the organization's own records."""
    from django.http import HttpResponse

    from apps.applications.letters import build_acceptance_letter, letter_filename

    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    app = get_object_or_404(
        Application.objects.select_related(
            "slot__organization", "slot__district__region", "student__user", "student__university"
        ),
        id=app_id, slot__organization=organization, is_accepted=True,
    )
    response = HttpResponse(build_acceptance_letter(app), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{letter_filename(app)}"'
    response["Cache-Control"] = "no-store"
    return response


# ── Private notes on an applicant ────────────────────────────────────────────

@login_required
@org_permission_required(APPLICANTS_VIEW)
@token_bucket(capacity=30, refill_per_second=1 / 5, scope="org-applicant-note", methods=["POST"])
def applicant_notes(request, app_id):
    """The team's private notes about one applicant. The student never sees them."""
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    app = get_object_or_404(
        Application.objects.select_related("student__user", "student__university", "slot", "payment"),
        id=app_id, slot__organization=organization, status=ApplicationStatus.PAID,
    )
    if request.method == "POST":
        action = request.POST.get("action", "add")
        if action == "delete":
            note = get_object_or_404(
                ApplicantNote, id=_uuid_or_404(request.POST.get("note_id")), application=app, organization=organization
            )
            # Authors remove their own notes; managers (who can accept) may remove any.
            if note.author_id != request.user.id and not org_has_perm(request.user, APPLICANTS_ACCEPT, organization):
                return HttpResponseForbidden(gettext("You can only delete your own notes."))
            note.delete()
            audit_event(
                request, action="Applicant Note Deleted", module="Applications", organization=organization,
                target=app, target_label=f"{app.student.user.email} -> {app.slot.title}",
                description="Deleted a private note on an applicant",
            )
            messages.success(request, gettext("Note deleted."))
        else:
            body = (request.POST.get("body") or "").strip()
            if not body:
                messages.error(request, gettext("Write something before saving the note."))
            elif len(body) > 2000:
                messages.error(request, gettext("A note can be at most 2000 characters."))
            else:
                ApplicantNote.objects.create(
                    application=app,
                    organization=organization,
                    author=request.user,
                    author_label=request.user.full_name or request.user.email,
                    body=body,
                )
                audit_event(
                    request, action="Applicant Note Added", module="Applications", organization=organization,
                    target=app, target_label=f"{app.student.user.email} -> {app.slot.title}",
                    description="Added a private note on an applicant",
                )
                messages.success(request, gettext("Note saved. Only your team can see it."))
        return redirect("organization-applicant-notes", app_id=app.id)
    notes = ApplicantNote.objects.filter(application=app, organization=organization).select_related("author")
    can_accept = org_has_perm(request.user, APPLICANTS_ACCEPT, organization)
    return render(
        request,
        "organization/applicant_notes.html",
        {
            "application": app,
            "notes": notes,
            "can_accept": can_accept,
            "blocker": acceptance_blocker(app),
            "templates": AcceptanceTemplate.objects.filter(organization=organization) if can_accept else [],
        },
    )


# ── Reusable acceptance messages ─────────────────────────────────────────────

@login_required
@org_permission_required(APPLICANTS_ACCEPT)
@token_bucket(capacity=20, refill_per_second=1 / 10, scope="org-accept-template", methods=["POST"])
def acceptance_templates(request):
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    templates = AcceptanceTemplate.objects.filter(organization=organization)
    if request.method == "POST":
        action = request.POST.get("action", "save")
        template = None
        if request.POST.get("template_id"):
            template = get_object_or_404(
                AcceptanceTemplate, id=_uuid_or_404(request.POST.get("template_id")), organization=organization
            )
        if action == "delete" and template:
            template.delete()
            messages.success(request, gettext("Template deleted."))
            return redirect("organization-templates")
        name = (request.POST.get("name") or "").strip()[:120]
        body = (request.POST.get("body") or "").strip()
        clash = templates.filter(name__iexact=name)
        if template:
            clash = clash.exclude(pk=template.pk)
        if not name or not body:
            messages.error(request, gettext("Give the template a name and a message."))
        elif len(body) > 500:
            messages.error(request, gettext("The message can be at most 500 characters (it is also sent by SMS)."))
        elif clash.exists():
            messages.error(request, gettext("You already have a template with that name."))
        elif not template and templates.count() >= MAX_ACCEPTANCE_TEMPLATES:
            messages.error(request, gettext('You can keep up to %(MAX_ACCEPTANCE_TEMPLATES)s templates. Delete one first.') % {"MAX_ACCEPTANCE_TEMPLATES": MAX_ACCEPTANCE_TEMPLATES})
        else:
            if template:
                template.name, template.body = name, body
                template.save(update_fields=["name", "body", "updated_at"])
            else:
                AcceptanceTemplate.objects.create(
                    organization=organization, name=name, body=body, created_by=request.user
                )
            audit_event(
                request, action="Acceptance Template Saved", module="Applications", organization=organization,
                target_type="AcceptanceTemplate", target_label=name, description=f"Saved acceptance template {name}",
            )
            messages.success(request, gettext("Template saved."))
        return redirect("organization-templates")
    return render(
        request,
        "organization/acceptance_templates.html",
        {"organization": organization, "templates": templates, "max_templates": MAX_ACCEPTANCE_TEMPLATES},
    )


# ── Insights, team activity, ratings ─────────────────────────────────────────

@login_required
@org_permission_required(APPLICANTS_VIEW)
def insights(request):
    organization = _require_approved_organization(request)
    if organization is None:
        return redirect("organization-profile")
    from apps.companies import dashboard as dash

    slots = dash.slots_with_counts(organization)
    return render(
        request,
        "organization/insights.html",
        {"organization": organization, "kpis": dash.kpis(slots), "charts": dash.charts(organization)},
    )


@login_required
@org_permission_required(AUDIT_VIEW)
def team_activity(request):
    """A readable feed of what the team did recently. The full, filterable record is the audit log."""
    organization = _org_for_audit(request)
    if organization is None:
        return redirect("organization-profile")
    from apps.core.pagination import paginate

    logs = AuditLog.objects.filter(organization=organization).select_related("actor").order_by("-created_at")
    page_obj = paginate(logs, request.GET.get("page"), page_size=30)
    return render(
        request,
        "organization/team_activity.html",
        {"organization": organization, "logs": page_obj, "page_obj": page_obj, "querystring": ""},
    )


@login_required
@org_permission_required(ORG_VIEW)
def reviews(request):
    """Ratings students left after this organization accepted them."""
    from django.db.models import Avg, Count

    from apps.applications.models import OrganizationReview
    from apps.core.pagination import paginate

    organization = _get_organization(request)
    if organization is None:
        return redirect("organization-profile")
    qs = OrganizationReview.objects.filter(organization=organization).select_related(
        "application__slot", "student__university"
    )
    summary = qs.aggregate(average=Avg("rating"), total=Count("id"))
    breakdown = dict(qs.values_list("rating").annotate(n=Count("id")))
    total = summary["total"] or 0
    page_obj = paginate(qs, request.GET.get("page"), page_size=10)
    return render(
        request,
        "organization/reviews.html",
        {
            "organization": organization,
            "reviews": page_obj,
            "page_obj": page_obj,
            "querystring": "",
            "average": round(summary["average"], 1) if summary["average"] else None,
            "review_total": total,
            "breakdown": [
                {"stars": stars, "count": breakdown.get(stars, 0),
                 "percent": round(breakdown.get(stars, 0) * 100 / total) if total else 0}
                for stars in (5, 4, 3, 2, 1)
            ],
        },
    )


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
        return HttpResponseForbidden(gettext("This application has not been paid."))
    if not app.application_letter:
        return HttpResponseForbidden(gettext("No application letter uploaded."))
    if app.letter_sha256:
        app.application_letter.open("rb")
        try:
            from apps.core.immutability import sha256_bytes

            actual = sha256_bytes(app.application_letter.read())
        finally:
            app.application_letter.close()
        if actual != app.letter_sha256:
            return HttpResponseGone(
                gettext("This application letter failed its integrity check and is unavailable.")
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