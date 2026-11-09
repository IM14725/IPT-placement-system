"""Platform governance pages: university records, account suspension, reports/complaints."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.models import User
from apps.companies.models import Organization
from apps.core.models import Report
from apps.core.pagination import paginate
from apps.core.views import _require_admin, _write_audit
from apps.locations.models import Region
from apps.students.models import StudentProfile, University, VerificationStatus

FIELD_CLASS = (
    "w-full bg-surface-container-lowest text-on-surface font-body-md text-body-md "
    "px-sm py-2 rounded-lg border border-outline-variant "
    "focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
)


def _forbidden(request):
    return render(request, "core/forbidden.html", status=403)


# ── University records ───────────────────────────────────────────────────────


class UniversityForm(forms.ModelForm):
    class Meta:
        model = University
        fields = ("name", "abbreviation", "institution_type", "region", "website")
        widgets = {
            "name": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "abbreviation": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "institution_type": forms.Select(attrs={"class": FIELD_CLASS}),
            "region": forms.Select(attrs={"class": FIELD_CLASS}),
            "website": forms.URLInput(attrs={"class": FIELD_CLASS, "placeholder": "https://"}),
        }

    def clean_name(self):
        name = " ".join((self.cleaned_data["name"] or "").split())
        clash = University.objects.filter(name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A university with this name already exists.")
        return name


@login_required
def universities(request):
    """List, add, approve/reject (suggestions) and merge university records."""
    if not _require_admin(request, module="user_management"):
        return _forbidden(request)

    form = UniversityForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            form = UniversityForm(request.POST)
            if form.is_valid():
                uni = form.save(commit=False)
                uni.verification_status = VerificationStatus.APPROVED
                uni.save()
                _write_audit(request, "University Added", "Universities", f"Added university {uni.name}.")
                messages.success(request, f"{uni.name} added.")
                return redirect("platform-universities")
        else:
            uni = get_object_or_404(University, pk=request.POST.get("id"))
            if action == "approve":
                uni.verification_status = VerificationStatus.APPROVED
                uni.save(update_fields=["verification_status", "updated_at"])
                _write_audit(request, "University Approved", "Universities", f"Approved university {uni.name}.")
                messages.success(request, f"{uni.name} approved and now selectable by all students.")
            elif action == "reject":
                unlinked = StudentProfile.objects.filter(university=uni).update(university=None)
                uni.verification_status = VerificationStatus.REJECTED
                uni.save(update_fields=["verification_status", "updated_at"])
                _write_audit(
                    request,
                    "University Rejected",
                    "Universities",
                    f"Rejected university suggestion {uni.name}; {unlinked} student(s) unlinked.",
                )
                messages.success(request, f"{uni.name} rejected.")
            elif action == "merge":
                target = University.objects.filter(
                    pk=request.POST.get("target"), verification_status=VerificationStatus.APPROVED
                ).exclude(pk=uni.pk).first()
                if target is None:
                    messages.error(request, "Choose an approved university to merge into.")
                else:
                    moved = StudentProfile.objects.filter(university=uni).update(university=target)
                    name = uni.name
                    uni.delete()
                    _write_audit(
                        request,
                        "University Merged",
                        "Universities",
                        f"Merged {name} into {target.name}; {moved} student(s) moved.",
                    )
                    messages.success(request, f"{name} merged into {target.name}.")
            return redirect(request.get_full_path())

    status = request.GET.get("status") or VerificationStatus.PENDING
    q = (request.GET.get("q") or "").strip()
    qs = University.objects.annotate(student_count=Count("students")).select_related("suggested_by", "region")
    if status in dict(VerificationStatus.choices):
        qs = qs.filter(verification_status=status)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(abbreviation__icontains=q))
    counts = dict(
        University.objects.values_list("verification_status").annotate(n=Count("id")).values_list("verification_status", "n")
    )
    page_obj = paginate(qs.order_by("name"), request.GET.get("page"), page_size=25)
    return render(
        request,
        "core/universities.html",
        {
            "rows": page_obj,
            "page_obj": page_obj,
            "status": status,
            "q": q,
            "tabs": [(value, label, counts.get(value, 0)) for value, label in VerificationStatus.choices],
            "form": form,
            "approved": University.objects.filter(verification_status=VerificationStatus.APPROVED).order_by("name"),
        },
    )


# ── Suspend / reactivate ─────────────────────────────────────────────────────


@login_required
def account_status(request):
    """Suspend or reactivate a student account or a whole organization (POST only)."""
    if request.method != "POST":
        return redirect("platform-directory")
    if not _require_admin(request, module="user_management", cap="edit"):
        return _forbidden(request)

    kind = request.POST.get("kind")
    action = request.POST.get("action")
    reason = (request.POST.get("reason") or "").strip()
    now = timezone.now()
    next_url = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "platform-directory"

    if action == "suspend" and not reason:
        messages.error(request, "A reason is required to suspend an account.")
        return redirect(next_url)

    if kind == "student":
        profile = get_object_or_404(StudentProfile, pk=request.POST.get("id"))
        users = [profile.user]
        label = f"student {profile.user.email}"
    elif kind == "organization":
        organization = get_object_or_404(Organization, pk=request.POST.get("id"))
        users = list(User.objects.filter(id__in=organization.member_user_ids()))
        label = f"organization {organization.name}"
    else:
        messages.error(request, "Unknown account type.")
        return redirect(next_url)

    if action == "suspend":
        for user in users:
            user.is_active = False
            user.suspended_at = now
            user.suspension_reason = reason
            user.save(update_fields=["is_active", "suspended_at", "suspension_reason", "updated_at"])
        if kind == "organization":
            organization.suspended_at = now
            organization.suspension_reason = reason
            organization.save(update_fields=["suspended_at", "suspension_reason", "updated_at"])
        _write_audit(request, "Account Suspended", "Account Status", f"Suspended {label}: {reason}")
        messages.success(request, f"Suspended {label}.")
    elif action == "reactivate":
        for user in users:
            # Accounts that asked to be deleted stay deactivated.
            user.is_active = user.deletion_requested_at is None
            user.suspended_at = None
            user.suspension_reason = ""
            user.save(update_fields=["is_active", "suspended_at", "suspension_reason", "updated_at"])
        if kind == "organization":
            organization.suspended_at = None
            organization.suspension_reason = ""
            organization.save(update_fields=["suspended_at", "suspension_reason", "updated_at"])
        _write_audit(request, "Account Reactivated", "Account Status", f"Reactivated {label}.")
        messages.success(request, f"Reactivated {label}.")
    else:
        messages.error(request, "Unknown action.")
    return redirect(next_url)


# ── Reports and complaints ───────────────────────────────────────────────────


class ReportForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = ("category", "organization", "subject", "description")
        widgets = {
            "category": forms.Select(attrs={"class": FIELD_CLASS}),
            "organization": forms.Select(attrs={"class": FIELD_CLASS}),
            "subject": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "description": forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 5}),
        }
        labels = {"organization": "Organization involved (optional)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organization"].required = False
        self.fields["organization"].queryset = Organization.objects.filter(
            verification_status=VerificationStatus.APPROVED
        ).order_by("name")


@login_required
def submit_report(request):
    """Any signed-in user can file a complaint for platform admins to review."""
    if request.method == "POST":
        form = ReportForm(request.POST)
        if form.is_valid():
            report = form.save(commit=False)
            report.reporter = request.user
            report.save()
            messages.success(request, "Thank you. Your report was sent to the platform admins.")
            return redirect("report-submit")
    else:
        form = ReportForm()
    return render(request, "core/report_form.html", {"form": form})


@login_required
def reports(request):
    if not _require_admin(request, module="user_management"):
        return _forbidden(request)

    if request.method == "POST":
        if not _require_admin(request, module="user_management", cap="edit"):
            return _forbidden(request)
        report = get_object_or_404(Report, pk=request.POST.get("id"))
        new_status = request.POST.get("status")
        if new_status in Report.Status.values:
            report.status = new_status
            report.admin_notes = (request.POST.get("admin_notes") or "").strip()
            report.handled_by = request.user
            report.handled_at = timezone.now()
            report.save(update_fields=["status", "admin_notes", "handled_by", "handled_at", "updated_at"])
            _write_audit(
                request,
                "Report Updated",
                "Reports",
                f"Report '{report.subject}' marked {report.get_status_display()}.",
            )
            messages.success(request, "Report updated.")
        return redirect(request.get_full_path())

    status = request.GET.get("status") or Report.Status.OPEN
    qs = Report.objects.select_related("reporter", "organization", "handled_by")
    if status in Report.Status.values:
        qs = qs.filter(status=status)
    counts = dict(Report.objects.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
    page_obj = paginate(qs, request.GET.get("page"), page_size=20)
    return render(
        request,
        "core/reports.html",
        {
            "rows": page_obj,
            "page_obj": page_obj,
            "status": status,
            "tabs": [(value, label, counts.get(value, 0)) for value, label in Report.Status.choices],
        },
    )
