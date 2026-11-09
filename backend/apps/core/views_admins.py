"""Platform console: administrator accounts, recovery approvals and break-glass accounts."""

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts import admins, breakglass, mfa, recovery
from apps.accounts.decorators import hardware_reverify
from apps.accounts.models import (
    AdminRecoveryRequest,
    AdminRole,
    BreakGlassAccount,
    RecoveryStatus,
    User,
    UserRole,
)
from apps.accounts.forms import FIELD_CLASS, validate_tz_phone

ADMIN_ROLES = [UserRole.ADMIN, UserRole.SUPER_ADMIN]


def _forbidden(request):
    return render(request, "core/forbidden.html", status=403)


def _is_super(request):
    return request.user.is_authenticated and request.user.is_super_admin


class AdminCreateForm(forms.Form):
    first_name = forms.CharField(max_length=150, label="First name", widget=forms.TextInput(attrs={"class": FIELD_CLASS}))
    last_name = forms.CharField(max_length=150, label="Last name", widget=forms.TextInput(attrs={"class": FIELD_CLASS}))
    email = forms.EmailField(label="Official login email", widget=forms.EmailInput(attrs={"class": FIELD_CLASS}))
    phone = forms.CharField(
        label="Phone", validators=[validate_tz_phone], widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "2557XXXXXXXX"})
    )
    job_title = forms.CharField(
        max_length=120, required=False, label="Job title / department",
        widget=forms.TextInput(attrs={"class": FIELD_CLASS}),
    )
    admin_role = forms.ModelChoiceField(
        queryset=AdminRole.objects.all(), required=False, label="Administrative role",
        widget=forms.Select(attrs={"class": "ipt-select"}),
    )
    super_admin = forms.BooleanField(required=False, label="Make this a Super Admin (overall platform control)")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("super_admin") and not cleaned.get("admin_role"):
            self.add_error("admin_role", "Choose an administrative role.")
        return cleaned


@login_required
def admin_list(request):
    if not _is_super(request):
        return _forbidden(request)
    people = (
        User.objects.filter(role__in=ADMIN_ROLES)
        .select_related("admin_role")
        .order_by("-is_active", "email")
    )
    rows = [
        {
            "user": u,
            "mfa": mfa.mfa_enrolled(u),
            "key": mfa.has_fido(u),
            "totp": mfa.has_totp(u),
            "break_glass": mfa.is_break_glass(u),
        }
        for u in people
    ]
    return render(request, "core/admin_list.html", {"rows": rows})


@login_required
@hardware_reverify
def admin_create(request):
    if not _is_super(request):
        return _forbidden(request)
    form = AdminCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = admins.create_administrator(
                request.user,
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                email=form.cleaned_data["email"],
                phone=form.cleaned_data["phone"],
                admin_role=form.cleaned_data["admin_role"],
                super_admin=form.cleaned_data["super_admin"],
                job_title=form.cleaned_data["job_title"],
                login_url=request.build_absolute_uri(reverse("login")),
                request=request,
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(
                request,
                f"Administrator created. A temporary password was emailed to {user.email}; "
                "it expires and must be replaced at first sign-in.",
            )
            return redirect("platform-admin-detail", pk=user.pk)
    return render(request, "core/admin_form.html", {"form": form})


@login_required
@hardware_reverify
def admin_detail(request, pk):
    if not _is_super(request):
        return _forbidden(request)
    person = get_object_or_404(User, pk=pk, role__in=ADMIN_ROLES)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "role":
                admins.change_admin_role(
                    request.user, person, get_object_or_404(AdminRole, pk=request.POST.get("admin_role")), request=request
                )
                messages.success(request, "Role updated; the administrator must sign in again.")
            elif action == "suspend":
                admins.set_admin_suspended(request.user, person, True, request.POST.get("reason", "").strip(), request=request)
                messages.success(request, "Administrator suspended and signed out.")
            elif action == "reactivate":
                admins.set_admin_suspended(request.user, person, False, request=request)
                messages.success(request, "Administrator reactivated.")
            elif action == "approver":
                admins.set_recovery_approver(request.user, person, request.POST.get("value") == "1", request=request)
                messages.success(request, "Recovery approver designation updated.")
            elif action == "reissue":
                admins.reissue_temp_password(
                    request.user, person, login_url=request.build_absolute_uri(reverse("login")), request=request
                )
                messages.success(request, "A new temporary password was emailed.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("platform-admin-detail", pk=person.pk)
    recent = person.audit_logs.order_by("-created_at")[:10]
    return render(
        request,
        "core/admin_detail.html",
        {
            "person": person,
            "roles": AdminRole.objects.all(),
            "mfa_enrolled": mfa.mfa_enrolled(person),
            "has_key": mfa.has_fido(person),
            "has_totp": mfa.has_totp(person),
            "codes_left": mfa.remaining_recovery_codes(person),
            "recent": recent,
            "permissions": (person.admin_role.permissions if person.admin_role else {}),
        },
    )


# ── recovery approvals ──────────────────────────────────────────────────────

@login_required
@hardware_reverify
def recovery_queue(request):
    user = request.user
    if not (user.is_super_admin or user.is_recovery_approver):
        return _forbidden(request)
    recovery.expire_stale()
    if request.method == "POST":
        req = get_object_or_404(AdminRecoveryRequest, pk=request.POST.get("request_id"))
        try:
            recovery.decide(
                req, user, request.POST.get("decision") == "approve", request=request,
                note=request.POST.get("note", ""),
            )
            messages.success(request, "Your decision was recorded.")
        except PermissionDenied as exc:
            messages.error(request, str(exc))
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("platform-recovery")
    requests_ = AdminRecoveryRequest.objects.select_related("target").prefetch_related("decisions__approver")[:50]
    rows = []
    for req in requests_:
        decided = {d.approver_id for d in req.decisions.all()}
        rows.append(
            {
                "req": req,
                "approvals": sum(1 for d in req.decisions.all() if d.approve),
                "rejections": sum(1 for d in req.decisions.all() if not d.approve),
                "can_vote": (
                    req.status == RecoveryStatus.PENDING
                    and user.is_recovery_approver
                    and user.pk != req.target_id
                    and user.pk not in decided
                ),
                "own": user.pk == req.target_id,
            }
        )
    return render(
        request,
        "core/recovery_queue.html",
        {
            "rows": rows,
            "approvers": recovery.eligible_approvers(),
            "is_approver": user.is_recovery_approver,
            "needed": settings.ADMIN_RECOVERY_APPROVALS,
        },
    )


# ── break-glass ─────────────────────────────────────────────────────────────

@login_required
@hardware_reverify
def break_glass(request):
    if not _is_super(request):
        return _forbidden(request)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            try:
                account, password = breakglass.create_break_glass(
                    request.user, request.POST.get("email", ""), request=request
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("platform-break-glass")
            return _show_secret_once(request, account, password, "Emergency account created")
        account = get_object_or_404(BreakGlassAccount, pk=request.POST.get("account_id"))
        if action == "rotate":
            password = breakglass.rotate_break_glass(account, request.user, request=request)
            return _show_secret_once(request, account, password, "Credentials rotated")
        if action == "reviewed":
            breakglass.mark_reviewed(account, request.user, request=request)
            messages.success(request, "Review recorded.")
        return redirect("platform-break-glass")
    accounts = BreakGlassAccount.objects.select_related("user")
    return render(
        request,
        "core/break_glass.html",
        {"accounts": [{"a": a, "key": mfa.has_fido(a.user)} for a in accounts]},
    )


def _show_secret_once(request, account, password, heading):
    response = render(
        request,
        "core/one_time_secret.html",
        {"account": account, "password": password, "heading": heading},
    )
    response["Cache-Control"] = "no-store"
    return response
