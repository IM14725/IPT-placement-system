from django.utils.translation import gettext
import json
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseForbidden, HttpResponseGone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.applications.forms import ApplicationLetterForm
from apps.applications.models import Application
from apps.applications.services import (
    SlotFullError,
    create_application,
    eligibility_blocker,
    finalize_verified_payment,
)
from apps.core.cache import get_institutions, get_regions, get_verification_status
from apps.core.education import education_level_choices, academic_year_choices
from apps.core.immutability import sha256_bytes
from apps.core.rate_limit import token_bucket
from apps.core.security import safe_json_dumps
from apps.documents.models import Document
from apps.payments.models import Payment
from apps.payments.utils import get_application_fee
from apps.students.forms import DocumentUploadForm, StudentProfileForm
from apps.students.models import StudentProfile, VerificationStatus, student_can_apply


def _file_sha256(uploaded):
    uploaded.seek(0)
    data = uploaded.read()
    uploaded.seek(0)
    return sha256_bytes(data)


def _completeness(user, profile):
    from apps.students import dashboard as dash

    return dash.profile_completeness(user, profile)


def _banner_step(user, page):
    from apps.students import dashboard as dash

    return dash.banner_step(user, _profile_required_for(user), page)


def _profile_required_for(user):
    return StudentProfile.objects.filter(user=user).first()


def _profile_required(request):
    return StudentProfile.objects.filter(user=request.user).first()


def _slot_list_page(request, template, slots, extra=None):
    from apps.core.pagination import paginate

    page_obj = paginate(slots, request.GET.get("page"), page_size=12)
    context = {
        "slots": page_obj,
        "page_obj": page_obj,
        "querystring": "",
        "profile": _profile_required(request),
        "can_apply": student_can_apply(request.user),
    }
    context.update(extra or {})
    return render(request, template, context)


@login_required
def recommended(request):
    """Recommendations live at the top of Find a Slot now."""
    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    return redirect("student-marketplace")


@login_required
def discover(request):
    """Closing soon / new slots are quick filters inside Find a Slot now."""
    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    return redirect(reverse("student-marketplace") + "?quick=closing")


@login_required
def saved_slots(request):
    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    from apps.students import dashboard as dash

    profile = _profile_required(request)
    return _slot_list_page(request, "student/saved_slots.html", dash.saved_slots(profile))


@login_required
@token_bucket(capacity=60, refill_per_second=1.0, scope="student-save-slot", methods=["POST"])
def toggle_saved_slot(request, slot_id):
    """Bookmark / un-bookmark a slot. Answers JSON to the marketplace script, a redirect to a form."""
    from django.http import JsonResponse
    from django.utils.http import url_has_allowed_host_and_scheme

    from apps.slots.models import Slot
    from apps.students.models import SavedSlot

    if not request.user.is_student or request.method != "POST":
        return HttpResponseForbidden(gettext("Student account and POST required."))
    is_ajax = request.headers.get("Accept", "").startswith("application/json")
    profile = _profile_required(request)
    if profile is None:
        if is_ajax:
            return JsonResponse({"detail": "Complete your profile before saving slots."}, status=400)
        messages.info(request, gettext("Complete your profile before saving slots."))
        return redirect("student-profile")
    slot = get_object_or_404(Slot, id=slot_id)
    existing = SavedSlot.objects.filter(student=profile, slot=slot)
    if existing.exists():
        existing.delete()
        saved = False
    else:
        SavedSlot.objects.get_or_create(student=profile, slot=slot)
        saved = True
    if is_ajax:
        return JsonResponse({"saved": saved})
    messages.success(request, gettext("Slot saved.") if saved else gettext("Slot removed from your saved list."))
    target = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = reverse("student-saved-slots")
    return redirect(target)


def _alerts_redirect():
    """Alerts are managed on the Notifications page (one page for both)."""
    return redirect(reverse("notifications") + "#alerts")


@login_required
@token_bucket(capacity=20, refill_per_second=1 / 10, scope="student-alerts", methods=["POST"])
def alerts(request):
    """Saved searches ("Notify me"): create from the marketplace filters, pause, delete.

    They are listed on the Notifications page; opening this address just goes there."""
    from django.http import JsonResponse

    from apps.core.education import AcademicYear, EducationLevel
    from apps.locations.models import District, Region
    from apps.students.models import MAX_SAVED_SEARCHES, SavedSearch

    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    profile = _profile_required(request)
    is_ajax = request.headers.get("Accept", "").startswith("application/json")

    def fail(message, status=400):
        if is_ajax:
            return JsonResponse({"detail": message}, status=status)
        messages.error(request, message)
        return _alerts_redirect()

    if request.method == "POST":
        if profile is None:
            return fail("Complete your profile before creating alerts.")
        action = request.POST.get("action", "create")
        if action in ("delete", "toggle"):
            alert = get_object_or_404(SavedSearch, id=request.POST.get("alert_id"), student=profile)
            if action == "delete":
                alert.delete()
                messages.success(request, gettext("Alert deleted."))
            else:
                alert.is_active = not alert.is_active
                alert.save(update_fields=["is_active", "updated_at"])
                messages.success(request, gettext("Alert resumed.") if alert.is_active else gettext("Alert paused."))
            return _alerts_redirect()

        def _int_choice(name, enum):
            raw = request.POST.get(name) or ""
            return int(raw) if raw.isdigit() and int(raw) in enum.values else None

        def _by_id(model, name):
            raw = (request.POST.get(name) or "").strip()
            if not raw:
                return None
            try:
                return model.objects.filter(id=raw).first()
            except (ValueError, ValidationError):
                return None

        region = _by_id(Region, "region")
        district = _by_id(District, "district")
        if district and region and district.region_id != region.id:
            district = None
        fields = {
            "region": region or (district.region if district else None),
            "district": district,
            "department": (request.POST.get("department") or "").strip()[:200],
            "organization": (request.POST.get("organization") or "").strip()[:200],
            "education_level": _int_choice("education_level", EducationLevel),
            "academic_year": _int_choice("academic_year", AcademicYear),
        }
        if not any(fields.values()):
            return fail("Choose at least one filter (region, discipline, level...) before saving an alert.")
        if SavedSearch.objects.filter(student=profile, **fields).exists():
            return fail("You already have an alert for this search.")
        if SavedSearch.objects.filter(student=profile).count() >= MAX_SAVED_SEARCHES:
            return fail(f"You can keep up to {MAX_SAVED_SEARCHES} alerts. Delete one first.")
        alert = SavedSearch.objects.create(student=profile, **fields)
        if is_ajax:
            return JsonResponse({"saved": True, "label": alert.label})
        messages.success(request, gettext("Alert saved. We will notify you when a matching slot is posted."))
        return _alerts_redirect()

    return _alerts_redirect()


def _own_accepted_application(request, app_id):
    return get_object_or_404(
        Application.objects.select_related(
            "slot__organization", "slot__district__region", "student__user", "student__university", "payment"
        ),
        id=app_id,
        student__user=request.user,
        is_accepted=True,
    )


@login_required
def acceptance_letter(request, app_id):
    """Download the acceptance letter (PDF) for one of the student's accepted applications."""
    from django.http import HttpResponse

    from apps.applications.letters import build_acceptance_letter, letter_filename

    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    application = _own_accepted_application(request, app_id)
    response = HttpResponse(build_acceptance_letter(application), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{letter_filename(application)}"'
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def checklist(request, app_id):
    """What to do after being accepted: letter, contacts, things to prepare."""
    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    application = _own_accepted_application(request, app_id)
    return render(
        request,
        "student/checklist.html",
        {
            "application": application,
            "organization": application.slot.organization,
            "review": getattr(application, "review", None),
        },
    )


@login_required
@token_bucket(capacity=6, refill_per_second=1 / 60, scope="student-review", methods=["POST"])
def review(request, app_id):
    """Rate the organization that accepted the student (one review per accepted application)."""
    from apps.applications.models import OrganizationReview
    from apps.companies.permissions import ORG_VIEW
    from apps.notifications.inapp import NotificationType, notify_many, organization_recipients

    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    application = _own_accepted_application(request, app_id)
    organization = application.slot.organization
    existing = OrganizationReview.objects.filter(application=application).first()
    error = ""
    if request.method == "POST":
        raw = request.POST.get("rating") or ""
        comment = (request.POST.get("comment") or "").strip()[:1000]
        if not raw.isdigit() or not 1 <= int(raw) <= 5:
            error = "Choose a rating from 1 to 5 stars."
        else:
            if existing:
                existing.rating = int(raw)
                existing.comment = comment
                existing.save(update_fields=["rating", "comment", "updated_at"])
                messages.success(request, gettext("Your review was updated."))
            else:
                OrganizationReview.objects.create(
                    application=application,
                    organization=organization,
                    student=application.student,
                    rating=int(raw),
                    comment=comment,
                )
                notify_many(
                    organization_recipients(organization, ORG_VIEW),
                    NotificationType.REVIEW,
                    "New rating received",
                    f"A student you accepted for '{application.slot.title}' rated your organization {raw}/5.",
                    link="/organization/reviews/",
                )
                messages.success(request, gettext("Thank you. Your review was submitted."))
            return redirect("organization-public-profile", org_id=organization.id)
    return render(
        request,
        "student/review.html",
        {"application": application, "organization": organization, "review": existing, "error": error},
    )


@login_required
def organization_public_profile(request, org_id):
    """An organization's page for signed-in users: who they are, their rating and open slots."""
    from django.db.models import Avg, Count

    from apps.applications.models import OrganizationReview
    from apps.companies.models import Organization
    from apps.core.pagination import paginate
    from apps.students import dashboard as dash

    organization = get_object_or_404(
        Organization.objects.select_related("region", "district"),
        id=org_id,
        verification_status=VerificationStatus.APPROVED,
        suspended_at__isnull=True,
    )
    reviews = OrganizationReview.objects.filter(organization=organization).select_related(
        "student__university", "application__slot"
    )
    summary = reviews.aggregate(average=Avg("rating"), total=Count("id"))
    profile = _profile_required(request) if request.user.is_student else None
    page_obj = paginate(reviews, request.GET.get("page"), page_size=10)
    return render(
        request,
        "student/organization_profile.html",
        {
            "organization": organization,
            "reviews": page_obj,
            "page_obj": page_obj,
            "querystring": "",
            "average": round(summary["average"], 1) if summary["average"] else None,
            "review_total": summary["total"],
            "open_slots": dash.decorate_slots(
                dash.open_slots().filter(organization=organization).order_by("-created_at")[:6], profile
            ),
            "profile": profile,
            "can_apply": request.user.is_student and student_can_apply(request.user),
        },
    )


def _verify_new_phone(request, phone):
    """The profile form got a different phone number: send the code to it; it is saved once entered."""
    from apps.accounts import otp
    from apps.accounts.models import OtpChannel, OtpPurpose

    result = otp.send_otp(request.user, OtpPurpose.CHANGE_PHONE, OtpChannel.SMS, destination=phone)
    if result.sent:
        messages.info(
            request,
            gettext('Your profile was saved. Enter the code we sent to %(phone)s to finish changing your phone number.') % {"phone": phone},
        )
        return redirect("verify-contact", kind="phone")
    if result.retry_after:
        messages.error(
            request,
            gettext('Your profile was saved. Please wait %(v1)s seconds, then change your phone number again from Account & security.') % {"v1": result.retry_after},
        )
    else:
        messages.error(
            request,
            gettext("Your profile was saved, but we could not send the code. Try again from Account & security."),
        )
    return redirect(reverse("student-profile") + "#security")


@login_required
def profile(request):
    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    instance = _profile_required(request)
    if request.method == "POST":
        form = StudentProfileForm(
            request.POST, request.FILES, instance=instance, user=request.user, verify_phone_change=True
        )
        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user
            profile.save()
            if form.cleaned_data.get("university_not_listed") and profile.university_id:
                if not profile.university.is_approved:
                    messages.info(
                        request,
                        gettext("Your university suggestion was sent to the platform admins for review. "
                        "It is saved on your profile in the meantime."),
                    )
            messages.success(request, gettext("Profile saved."))
            if form.pending_phone:
                return _verify_new_phone(request, form.pending_phone)
            return redirect("student-marketplace")
    else:
        form = StudentProfileForm(instance=instance, user=request.user, verify_phone_change=True)
    regions = get_regions()
    from apps.accounts.views_account import security_context

    return render(
        request,
        "student/profile.html",
        {
            "form": form,
            "profile": instance,
            "completeness": _completeness(request.user, instance),
            "regions_json": safe_json_dumps(regions),
            "institutions_json": safe_json_dumps(get_institutions()),
            **security_context(request),
        },
    )


@login_required
@token_bucket(capacity=10, refill_per_second=1 / 30, scope="student-doc-upload", methods=["POST"])
def documents(request):
    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            file = request.FILES["file"]
            try:
                doc = Document.objects.create(
                    owner=request.user,
                    doc_type=form.cleaned_data["doc_type"],
                    file=file,
                    original_name=file.name,
                )
            except ValidationError as exc:
                form.add_error("file", exc.message)
            else:
                from apps.core.cache import enqueue_once

                enqueue_once("documents.scan", [doc.id])
                messages.success(request, gettext("Document uploaded. It will be reviewed by the platform admin."))
                return redirect("student-documents")
    else:
        form = DocumentUploadForm()
    docs = Document.objects.filter(owner=request.user)
    have = set(docs.values_list("doc_type", flat=True))
    # The documents the profile checklist asks for, with whether each one is already uploaded.
    required = [
        {"label": Document.DocType(t).label, "done": t in have}
        for t in (Document.DocType.STUDENT_ID, Document.DocType.RESULTS_MATRIX)
    ]
    return render(request, "student/documents.html", {"form": form, "docs": docs, "required": required})


@login_required
def marketplace(request):
    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    profile = _profile_required(request)
    regions = get_regions()
    from apps.students import dashboard as dash
    from apps.students.models import SavedSlot

    saved_ids = (
        [str(i) for i in SavedSlot.objects.filter(student=profile).values_list("slot_id", flat=True)]
        if profile
        else []
    )
    return render(
        request,
        "student/marketplace.html",
        {
            "profile": profile,
            "next_step": dash.banner_step(request.user, profile),
            "regions": regions,
            "education_levels": education_level_choices(),
            "academic_years": academic_year_choices(),
            # Complete profile + verified phone. Platform-admin approval is no longer required.
            "can_apply": student_can_apply(request.user),
            "saved_ids_json": safe_json_dumps(saved_ids),
            "recommended": _recommended_payload(profile),
            # So a card can say "Requires Level 8" instead of "Apply Now" (the server enforces it too).
            "student_level_json": safe_json_dumps(
                {
                    "level": profile.education_level if profile else None,
                    "year": profile.academic_year if profile else None,
                }
            ),
        },
    )


def _recommended_payload(profile):
    """The top matches for the "Recommended for you" block, in the shape the marketplace script draws."""
    from apps.slots.serializers import SlotSerializer
    from apps.students import dashboard as dash

    slots = dash.recommended_slots(profile, limit=4) if profile else []
    for slot in slots:
        slot._booked = slot.booked
        slot._available = slot.seats_left
    rows = SlotSerializer(slots, many=True).data
    return [dict(row, match=slot.match, reasons=slot.reasons) for row, slot in zip(rows, slots)]


@login_required
def applications(request):
    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    from django.db.models import Count
    from django.utils.translation import gettext as _

    from apps.applications.models import ApplicationStatus
    from apps.core.pagination import paginate
    from urllib.parse import urlencode

    mine = Application.objects.filter(student__user=request.user)
    counts = mine.aggregate(
        all=Count("id"),
        awaiting=Count("id", filter=Q(status=ApplicationStatus.PENDING)),
        paid=Count("id", filter=Q(status=ApplicationStatus.PAID, is_accepted=False)),
        accepted=Count("id", filter=Q(is_accepted=True)),
        closed=Count("id", filter=Q(status__in=[ApplicationStatus.UNPAID, ApplicationStatus.CANCELLED])),
    )
    tab_filters = {
        "awaiting": Q(status=ApplicationStatus.PENDING),
        "paid": Q(status=ApplicationStatus.PAID, is_accepted=False),
        "accepted": Q(is_accepted=True),
        "closed": Q(status__in=[ApplicationStatus.UNPAID, ApplicationStatus.CANCELLED]),
    }
    tab = request.GET.get("tab") or "all"
    if tab not in tab_filters:
        tab = "all"
    qs = (
        mine.filter(tab_filters[tab]) if tab in tab_filters else mine
    ).select_related("slot__organization", "slot__district__region", "payment").order_by("-created_at")
    tabs = [
        {"key": key, "label": label, "count": counts[key]}
        for key, label in (
            ("all", _("All")),
            ("awaiting", _("Awaiting payment")),
            ("paid", _("Paid")),
            ("accepted", _("Accepted")),
            ("closed", _("Expired & cancelled")),
        )
    ]
    page_obj = paginate(qs, request.GET.get("page"))
    return render(
        request,
        "student/applications.html",
        {
            "applications": page_obj,
            "page_obj": page_obj,
            "tabs": tabs,
            "tab": tab,
            "next_step": _banner_step(request.user, "applications"),
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
        },
    )


@login_required
@token_bucket(capacity=6, refill_per_second=1 / 30, scope="student-release", methods=["POST"])
def release_seat(request, app_id):
    """Give back a seat that is reserved but not yet paid for, instead of waiting out the 3 hours.

    The application becomes UNPAID, exactly as when the payment deadline expires, so "Re-book Slot"
    keeps working.
    """
    from django.utils import timezone

    from apps.applications.models import ApplicationStatus
    from apps.payments.models import PaymentStatus

    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    if request.method != "POST":
        return redirect("student-applications")
    application = get_object_or_404(
        Application.objects.select_related("slot", "payment"), id=app_id, student__user=request.user
    )
    payment = getattr(application, "payment", None)
    if application.status != ApplicationStatus.PENDING or (payment is not None and payment.is_paid):
        messages.error(request, gettext("Only a seat you have not paid for yet can be released."))
        return redirect("student-applications")
    with transaction.atomic():
        released = Application.objects.filter(pk=application.pk, status=ApplicationStatus.PENDING).update(
            status=ApplicationStatus.UNPAID, updated_at=timezone.now()
        )
        if released:
            if payment is not None:
                type(payment).objects.filter(pk=payment.pk, status=PaymentStatus.PENDING).update(
                    status=PaymentStatus.FAILED, updated_at=timezone.now()
                )
            application.slot.refresh_status()
    if released:
        messages.success(
            request,
            gettext("Your seat for '%(v1)s' was released. You can re-book it later if it is still open.") % {"v1": application.slot.title},
        )
    return redirect("student-applications")


@login_required
def slot_detail(request, slot_id):
    """Everything about one slot before applying: details, the organization's rating and the rules."""
    from django.db.models import Avg, Count

    from apps.applications.models import PAYMENT_DEADLINE_HOURS, ApplicationStatus, OrganizationReview
    from apps.slots.models import Slot, SlotStatus, SlotView
    from apps.students.models import SavedSlot

    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    slot = get_object_or_404(
        Slot.objects.select_related("organization", "district__region"),
        id=slot_id,
        organization__verification_status=VerificationStatus.APPROVED,
        organization__suspended_at__isnull=True,
    )
    profile = _profile_required(request)
    application = None
    saved = False
    blocker = ""
    if profile is not None:
        SlotView.objects.get_or_create(slot=slot, student=profile)
        application = Application.objects.filter(student=profile, slot=slot).select_related("payment").first()
        saved = SavedSlot.objects.filter(student=profile, slot=slot).exists()
        blocker = eligibility_blocker(profile, slot)
    summary = OrganizationReview.objects.filter(organization=slot.organization).aggregate(
        average=Avg("rating"), total=Count("id")
    )
    if slot.status == SlotStatus.PAUSED:
        closed_reason = "The organization has paused this slot for now."
    elif slot.status == SlotStatus.CLOSED:
        closed_reason = "The organization has closed this slot."
    elif slot.deadline_passed:
        closed_reason = "The application deadline has passed."
    elif slot.is_full:
        closed_reason = "All seats are taken."
    else:
        closed_reason = ""
    holds_seat = bool(application and application.status in (ApplicationStatus.PENDING, ApplicationStatus.PAID))
    return render(
        request,
        "student/slot_detail.html",
        {
            "slot": slot,
            "organization": slot.organization,
            "profile": profile,
            "application": application,
            "holds_seat": holds_seat,
            "saved": saved,
            "blocker": blocker,
            "closed_reason": closed_reason,
            "can_apply": student_can_apply(request.user),
            "fee": get_application_fee(),
            "payment_hours": PAYMENT_DEADLINE_HOURS,
            "average": round(summary["average"], 1) if summary["average"] else None,
            "review_total": summary["total"],
            "seats_left": slot.available_count,
        },
    )


@login_required
@token_bucket(capacity=3, refill_per_second=1 / 60, scope="student-apply", methods=["POST"])
def apply(request, slot_id):
    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    from apps.slots.models import Slot

    profile = _profile_required(request)
    if profile is None or not profile.is_complete:
        messages.error(request, gettext("Complete your profile (course, region and district) before applying."))
        return redirect("student-profile")
    if not request.user.phone_verified:
        messages.warning(
            request,
            gettext("Verify your phone number before applying: add it under Account & security and enter the SMS code."),
        )
        return redirect("account-security")
    slot = get_object_or_404(Slot, id=slot_id)

    # Tell the student up front, before they prepare a letter and pay, that they do not qualify.
    # (create_application enforces the same rule, so this is a courtesy, not the only guard.)
    blocker = eligibility_blocker(profile, slot)
    if blocker and not Application.objects.filter(
        student=profile, slot=slot, status__in=["PENDING", "PAID"]
    ).exists():
        messages.error(request, blocker)
        return redirect("student-marketplace")

    if request.method == "POST":
        form = ApplicationLetterForm(request.POST, request.FILES)
        if form.is_valid():
            letter = form.cleaned_data["application_letter"]
            try:
                with transaction.atomic():
                    application = create_application(profile, slot)
                    is_credit = profile.reapplication_credit
                    application.application_letter = letter
                    application.letter_original_name = letter.name
                    application.letter_sha256 = _file_sha256(letter)
                    if is_credit:
                        application.can_cancel = False
                    application.save(
                        update_fields=[
                            "application_letter",
                            "letter_original_name",
                            "letter_sha256",
                            "can_cancel",
                            "updated_at",
                        ]
                    )

                    fee = get_application_fee()
                    payment = getattr(application, "payment", None)
                    if payment is None:
                        payment = Payment.objects.create(
                            student=profile, application=application, amount=fee
                        )
                    elif not payment.is_paid:
                        payment.status = "PENDING"
                        payment.save(update_fields=["status", "updated_at"])

                    if is_credit:
                        # Same path as a real payment, so the application only becomes PAID once a
                        # verified Payment exists (database trigger), the organization is told about
                        # the new applicant and the slot status is refreshed.
                        finalize_verified_payment(
                            payment, gateway_txn_id="CREDIT-REAPPLICATION", payload={"credit": True}
                        )
                        profile.reapplication_credit = False
                        profile.save(update_fields=["reapplication_credit", "updated_at"])
            except SlotFullError as exc:
                messages.error(request, str(exc))
                return redirect("student-marketplace")
            if is_credit:
                messages.success(request, gettext("Application submitted successfully using your cancellation credit."))
                return redirect("student-applications")
            messages.success(request, gettext("Application submitted with your letter. Complete payment to finalize."))
            return redirect("student-payment", payment_id=application.payment.id)
    else:
        form = ApplicationLetterForm()

    from apps.applications.models import PAYMENT_DEADLINE_HOURS

    return render(
        request,
        "student/apply.html",
        {
            "form": form,
            "slot": slot,
            "profile": profile,
            "fee": get_application_fee(),
            "payment_hours": PAYMENT_DEADLINE_HOURS,
            "has_credit": profile.reapplication_credit,
        },
    )


@login_required
def payment(request, payment_id):
    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    payment = get_object_or_404(
        Payment, id=payment_id, student__user=request.user
    )
    return render(request, "student/payment.html", {"payment": payment})


@login_required
def view_document(request, doc_id):
    from apps.documents.validators import build_safe_file_response

    doc = get_object_or_404(Document, id=doc_id)
    user = request.user
    is_owner = doc.owner_id == user.id
    is_admin = user.is_staff or user.is_platform_admin
    organization_can_view = False
    viewer_org = None
    if user.is_organization:
        from apps.companies.permissions import APPLICANTS_VIEW, ORG_VIEW, org_has_perm
        from apps.companies.services import get_user_organization

        viewer_org = get_user_organization(user)
        if viewer_org is not None:
            # A student's documents: only for a paid application to *this* organization,
            # and only for members whose role may see applicants.
            if org_has_perm(user, APPLICANTS_VIEW, viewer_org):
                organization_can_view = Application.objects.filter(
                    student__user=doc.owner_id,
                    slot__organization=viewer_org,
                    status="PAID",
                ).exists()
            # The organization's own documents: any active member, including documents
            # uploaded by people who have since left. Never another organization's.
            if not organization_can_view and org_has_perm(user, ORG_VIEW, viewer_org):
                organization_can_view = doc.owner_id in viewer_org.historical_member_user_ids()
    if not (is_owner or is_admin or organization_can_view):
        raise Http404
    if not is_owner:
        from apps.core.audit import audit_event

        audit_event(
            request,
            action="Document Accessed",
            module="Documents",
            organization=viewer_org,
            target=doc,
            target_label=f"{doc.get_doc_type_display()} ({doc.original_name})",
            description=f"Viewed {doc.get_doc_type_display()} owned by {doc.owner.email}",
        )
    if doc.scan_status == "ERROR":
        return HttpResponseGone(gettext("This document failed security scanning and is unavailable."))
    if doc.sha256:
        doc.file.open("rb")
        try:
            actual = sha256_bytes(doc.file.read())
        finally:
            doc.file.close()
        if actual != doc.sha256:
            return HttpResponseGone(gettext("This document failed its integrity check and is unavailable."))
    return build_safe_file_response(
        doc.file,
        filename=doc.original_name or "document",
        declared_mime=doc.mime_type,
    )


@login_required
@token_bucket(capacity=3, refill_per_second=1 / 60, scope="student-cancel", methods=["POST"])
def cancel_application(request, app_id):
    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    
    from django.utils import timezone
    from datetime import timedelta
    
    application = get_object_or_404(
        Application.objects.select_related("payment", "slot", "student"),
        id=app_id, student__user=request.user
    )
    
    if request.method == "POST":
        if not application.can_cancel:
            messages.error(request, gettext("This application cannot be cancelled. You can only cancel once per payment."))
            return redirect("student-applications")
            
        if not application.is_paid:
            messages.error(request, gettext("Only paid applications can be cancelled."))
            return redirect("student-applications")
            
        if not application.payment or not application.payment.paid_at:
            messages.error(request, gettext("Payment information is missing."))
            return redirect("student-applications")
            
        # 24 hour check
        if timezone.now() > application.payment.paid_at + timedelta(hours=24):
            messages.error(request, gettext("The 24-hour cancellation window has passed."))
            return redirect("student-applications")
            
        if application.is_accepted:
             messages.error(request, gettext("Application has already been accepted by the organization."))
             return redirect("student-applications")
             
        # Execute cancellation
        application.status = "CANCELLED"
        application.can_cancel = False
        application.save(update_fields=["status", "can_cancel", "updated_at"])
        
        # Free up slot
        application.slot.refresh_status()
        
        # Give credit
        profile = application.student
        profile.reapplication_credit = True
        profile.save(update_fields=["reapplication_credit", "updated_at"])
        
        # Tell the organization: the applicant leaves their list and the seat is free again.
        from apps.companies.permissions import APPLICANTS_VIEW
        from apps.core.audit import audit_event
        from apps.notifications.inapp import NotificationType, notify_many, organization_recipients

        organization = application.slot.organization
        notify_many(
            organization_recipients(organization, APPLICANTS_VIEW),
            NotificationType.APPLICATION,
            "Applicant cancelled",
            f"{request.user.full_name or 'A student'} cancelled their application for "
            f"'{application.slot.title}'. The seat is available again.",
            link=f"/organization/slots/{application.slot_id}/applicants/",
            actor=request.user,
        )
        audit_event(
            request, action="Application Cancelled", module="Applications", actor=request.user,
            organization=organization, target=application,
            target_label=f"{request.user.email} -> {application.slot.title}",
            description="Student cancelled a paid application within the 24-hour window",
        )

        messages.success(request, gettext("Application cancelled successfully. Your slot was returned and you now have a credit to apply for a new slot without paying."))
        
    return redirect("student-applications")
@login_required
def view_application_letter(request, app_id):
    from apps.documents.validators import build_safe_file_response

    if not request.user.is_student:
        return HttpResponseForbidden(gettext("Student account required."))
    app = get_object_or_404(
        Application, id=app_id, student__user=request.user
    )
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
