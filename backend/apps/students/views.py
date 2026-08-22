import json
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseForbidden, HttpResponseGone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.applications.forms import ApplicationLetterForm
from apps.applications.models import Application
from apps.applications.services import SlotFullError, create_application
from apps.core.cache import get_institutions, get_regions, get_verification_status
from apps.core.education import education_level_choices
from apps.core.immutability import sha256_bytes
from apps.core.rate_limit import token_bucket
from apps.documents.models import Document
from apps.payments.models import Payment
from apps.students.forms import DocumentUploadForm, StudentProfileForm
from apps.students.models import StudentProfile, VerificationStatus

_APPLICATION_FEE = 15000


def _file_sha256(uploaded):
    uploaded.seek(0)
    data = uploaded.read()
    uploaded.seek(0)
    return sha256_bytes(data)


def _profile_required(request):
    return StudentProfile.objects.filter(user=request.user).first()


@login_required
def dashboard(request):
    if not request.user.is_student:
        return HttpResponseForbidden("Student account required.")
    profile = _profile_required(request)
    applications = (
        Application.objects.filter(student__user=request.user)
        .select_related("slot__company", "slot__district__region", "payment")
        .order_by("-created_at")[:10]
    )
    return render(
        request,
        "student/dashboard.html",
        {"profile": profile, "applications": applications},
    )


@login_required
def profile(request):
    if not request.user.is_student:
        return HttpResponseForbidden("Student account required.")
    instance = _profile_required(request)
    if request.method == "POST":
        form = StudentProfileForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user
            profile.save()
            messages.success(request, "Profile saved.")
            return redirect("student-dashboard")
    else:
        form = StudentProfileForm(instance=instance)
    regions = get_regions()
    return render(
        request,
        "student/profile.html",
        {
            "form": form,
            "profile": instance,
            "regions_json": json.dumps(regions),
            "institutions_json": json.dumps(get_institutions()),
        },
    )


@login_required
def documents(request):
    if not request.user.is_student:
        return HttpResponseForbidden("Student account required.")
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
                messages.success(request, "Document uploaded. It will be reviewed by the platform admin.")
                return redirect("student-documents")
    else:
        form = DocumentUploadForm()
    docs = Document.objects.filter(owner=request.user)
    return render(request, "student/documents.html", {"form": form, "docs": docs})


@login_required
def marketplace(request):
    if not request.user.is_student:
        return HttpResponseForbidden("Student account required.")
    profile = _profile_required(request)
    regions = get_regions()
    can_apply = get_verification_status(
        request.user.id,
        producer=lambda: bool(
            StudentProfile.objects.filter(
                user_id=request.user.id, verification_status="APPROVED"
            ).exists()
        ),
    )
    return render(
        request,
        "student/marketplace.html",
        {
            "profile": profile,
            "regions": regions,
            "education_levels": education_level_choices(),
            "can_apply": can_apply,
        },
    )


@login_required
def applications(request):
    if not request.user.is_student:
        return HttpResponseForbidden("Student account required.")
    from apps.core.pagination import paginate
    from urllib.parse import urlencode

    qs = (
        Application.objects.filter(student__user=request.user)
        .select_related("slot__company", "slot__district__region", "payment")
        .order_by("-created_at")
    )
    page_obj = paginate(qs, request.GET.get("page"))
    return render(
        request,
        "student/applications.html",
        {
            "applications": page_obj,
            "page_obj": page_obj,
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
        },
    )


@login_required
@token_bucket(capacity=3, refill_per_second=1 / 60, scope="student-apply", methods=["POST"])
def apply(request, slot_id):
    if not request.user.is_student:
        return HttpResponseForbidden("Student account required.")
    from apps.slots.models import Slot

    profile = _profile_required(request)
    if profile is None or not profile.is_verified:
        messages.error(request, "Your profile must be verified before applying.")
        return redirect("student-documents")
    slot = get_object_or_404(Slot, id=slot_id)

    if request.method == "POST":
        form = ApplicationLetterForm(request.POST, request.FILES)
        if form.is_valid():
            letter = form.cleaned_data["application_letter"]
            try:
                application = create_application(profile, slot)
                application.application_letter = letter
                application.letter_original_name = letter.name
                application.letter_sha256 = _file_sha256(letter)
                application.save(
                    update_fields=[
                        "application_letter",
                        "letter_original_name",
                        "letter_sha256",
                        "updated_at",
                    ]
                )
                payment = getattr(application, "payment", None)
                if payment is None:
                    payment = Payment.objects.create(
                        student=profile, application=application, amount=_APPLICATION_FEE
                    )
                elif not payment.is_paid:
                    payment.status = "PENDING"
                    payment.save(update_fields=["status", "updated_at"])
            except SlotFullError as exc:
                messages.error(request, str(exc))
                return redirect("student-marketplace")
            messages.success(request, "Application submitted with your letter. Complete payment to finalize.")
            return redirect("student-payment", payment_id=application.payment.id)
    else:
        form = ApplicationLetterForm()

    return render(
        request,
        "student/apply.html",
        {"form": form, "slot": slot, "profile": profile},
    )


@login_required
def payment(request, payment_id):
    if not request.user.is_student:
        return HttpResponseForbidden("Student account required.")
    payment = get_object_or_404(
        Payment, id=payment_id, student__user=request.user
    )
    return render(request, "student/payment.html", {"payment": payment})


@login_required
def view_document(request, doc_id):
    doc = get_object_or_404(Document, id=doc_id)
    user = request.user
    is_owner = doc.owner_id == user.id
    is_admin = user.is_staff or user.is_platform_admin
    company_can_view = False
    if user.is_company:
        company_can_view = Application.objects.filter(
            student__user=doc.owner_id,
            slot__company__user=user,
            status="PAID",
        ).exists()
    if not (is_owner or is_admin or company_can_view):
        raise Http404
    if doc.sha256:
        doc.file.open("rb")
        try:
            actual = sha256_bytes(doc.file.read())
        finally:
            doc.file.close()
        if actual != doc.sha256:
            return HttpResponseGone("This document failed its integrity check and is unavailable.")
    return FileResponse(doc.file.open("rb"), content_type=doc.mime_type or "application/octet-stream")