from django import forms
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _

from apps.accounts.antibot import AntiBotMixin
from apps.accounts.models import User, UserRole, normalize_phone
from apps.companies.identifiers import normalize_brela, normalize_tin

FIELD_CLASS = (
    "w-full bg-surface-container-lowest text-on-surface font-body-md text-body-md "
    "px-sm py-3 rounded-lg border border-outline-variant "
    "focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent "
    "transition-all placeholder:text-on-surface-variant/50"
)


# Same wording whether the number is taken or otherwise unusable, so it never says why.
PHONE_UNAVAILABLE_MESSAGE = _("This phone number can't be used. Try another number or contact support.")


def _normalize_phone(value):
    return "".join(ch for ch in (value or "") if ch.isdigit() or ch == "+")


def validate_tz_phone(value):
    if not value:
        return value
    normalized = _normalize_phone(value)
    digits = normalized.lstrip("+")
    if not digits.startswith("255"):
        raise forms.ValidationError(_("Phone number must start with 255 (Tanzania country code)."))
    if len(digits) != 12:
        raise forms.ValidationError(_("Phone number must be 12 digits (255 + 9-digit number)."))
    if digits[3] not in ("6", "7"):
        raise forms.ValidationError(_("Phone number must start with 2556 or 2557."))
    return "+" + digits if normalized.startswith("+") else digits


def validate_tz_any_phone(value):
    """Organization lines may be landlines: 255 followed by 9 digits, any network."""
    if not value:
        return value
    normalized = _normalize_phone(value)
    digits = normalized.lstrip("+")
    if not digits.startswith("255") or len(digits) != 12 or not digits.isdigit():
        raise forms.ValidationError(_("Phone number must be 255 followed by 9 digits."))
    return value


class RegisterForm(AntiBotMixin, forms.ModelForm):
    antibot_min_seconds = 5  # three wizard steps cannot be completed faster than this

    password1 = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(
            attrs={"class": FIELD_CLASS, "placeholder": _("At least 8 characters"), "autocomplete": "new-password"}
        ),
    )
    password2 = forms.CharField(
        label=_("Confirm password"),
        widget=forms.PasswordInput(attrs={"class": FIELD_CLASS, "placeholder": _("Repeat your password"), "autocomplete": "new-password"}),
    )
    role = forms.ChoiceField(
        choices=[(UserRole.STUDENT, _("Student")), (UserRole.ORGANIZATION, _("Organization"))],
        widget=forms.RadioSelect(attrs={"class": "peer sr-only"}),
    )
    phone = forms.CharField(
        required=False,
        validators=[validate_tz_phone],
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "2557XXXXXXXX", "autocomplete": "tel"}),
    )
    organization_name = forms.CharField(
        required=False,
        label=_("Organization name"),
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": _("e.g. Amani Logistics Ltd")}),
    )
    # Organization contact identity (separate from the first member's own login details).
    organization_email = forms.EmailField(
        required=False,
        label=_("Official organization email"),
        widget=forms.EmailInput(attrs={"class": FIELD_CLASS, "placeholder": "info@organization.co.tz"}),
    )
    organization_phone = forms.CharField(
        required=False,
        label=_("Organization phone number"),
        validators=[validate_tz_any_phone],
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "2557XXXXXXXX"}),
    )
    brela_number = forms.CharField(
        required=False,
        label=_("BRELA registration number"),
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "e.g. 123456789"}),
    )
    tin = forms.CharField(
        required=False,
        label=_("TIN (Taxpayer Identification Number)"),
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "123-456-789", "inputmode": "numeric"}),
    )
    position = forms.CharField(
        required=False,
        max_length=120,
        label=_("Your position in the organization"),
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": _("e.g. Human Resources Manager")}),
    )

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "phone", "role", "organization_name")  # + extras above
        widgets = {
            "email": forms.EmailInput(attrs={"class": FIELD_CLASS, "placeholder": "you@example.com", "autocomplete": "email"}),
            "first_name": forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": _("First name")}),
            "last_name": forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": _("Last name")}),
        }

    def clean_email(self):
        # Deliberately no "already registered" check: the form must not reveal which emails have
        # accounts. An existing address is handled in process_registration_task (neutral outcome).
        return self.cleaned_data["email"].strip().lower()

    def validate_unique(self):
        # Django's model-level "User with this Email already exists" would reveal registered emails.
        # Duplicates are resolved in process_registration_task (decoy outcome / generic phone message).
        pass

    def clean_brela_number(self):
        return normalize_brela(self.cleaned_data.get("brela_number"))

    def clean_tin(self):
        return normalize_tin(self.cleaned_data.get("tin"))

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data.get("phone"))
        if phone and User.objects.filter(phone=phone).exists():
            raise forms.ValidationError(PHONE_UNAVAILABLE_MESSAGE)
        return self.cleaned_data.get("phone")

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", _("Passwords do not match."))
        if p1:
            try:
                validate_password(p1, user=self.instance)
            except forms.ValidationError as exc:
                self.add_error("password1", exc.messages)
        role = cleaned.get("role")
        # Students and the first organization member both register as a named person.
        if not (cleaned.get("first_name") or "").strip():
            self.add_error("first_name", _("First name is required."))
        if not (cleaned.get("last_name") or "").strip():
            self.add_error("last_name", _("Last name is required."))
        if role == UserRole.ORGANIZATION:
            from apps.companies.services import organization_email_taken, organization_name_taken

            organization_name = (cleaned.get("organization_name") or "").strip()
            if not organization_name:
                self.add_error("organization_name", _("Organization name is required."))
            elif organization_name_taken(organization_name):
                self.add_error("organization_name", _("An organization with this name is already registered."))
            org_email = (cleaned.get("organization_email") or "").strip().lower()
            if not org_email:
                self.add_error("organization_email", _("The official organization email is required."))
            elif organization_email_taken(org_email):
                self.add_error(
                    "organization_email", _("An organization with this official email is already registered.")
                )
            if not cleaned.get("organization_phone"):
                self.add_error("organization_phone", _("The organization phone number is required."))
            if not (cleaned.get("position") or "").strip():
                self.add_error("position", _("Your position in the organization is required."))
            if not cleaned.get("phone"):
                self.add_error("phone", _("Your own phone number is required."))
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        user.role = self.cleaned_data["role"]
        if commit:
            user.save()
        return user


class LoginForm(AntiBotMixin, forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={"class": FIELD_CLASS, "placeholder": "you@example.com", "autocomplete": "email"}
        )
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": FIELD_CLASS, "placeholder": _("Your password"), "autocomplete": "current-password"})
    )


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={"class": FIELD_CLASS, "placeholder": "you@example.com", "autocomplete": "email"}
        )
    )


class RecoveryMethodForm(forms.Form):
    channel = forms.ChoiceField(
        choices=[("EMAIL", "Email"), ("SMS", "SMS to my phone")],
        initial="EMAIL",
        widget=forms.RadioSelect,
    )


class OtpCodeForm(forms.Form):
    code = forms.CharField(
        min_length=6,
        max_length=6,
        widget=forms.TextInput(
            attrs={
                "class": FIELD_CLASS,
                "placeholder": "6-digit code",
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
            }
        ),
    )


class ResetPasswordForm(forms.Form):
    password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(
            attrs={"class": FIELD_CLASS, "placeholder": "At least 8 characters", "autocomplete": "new-password"}
        ),
    )
    password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={"class": FIELD_CLASS, "placeholder": "Repeat your new password", "autocomplete": "new-password"}),
    )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned

class _CurrentPasswordMixin:
    """Contact changes and password changes re-ask for the current password."""

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        pw = self.cleaned_data.get("current_password") or ""
        if self.user is None or not self.user.check_password(pw):
            raise forms.ValidationError("Your current password is incorrect.")
        return pw


_CURRENT_PW = forms.PasswordInput(
    attrs={"class": FIELD_CLASS, "placeholder": "Current password", "autocomplete": "current-password"}
)


class ChangeEmailForm(_CurrentPasswordMixin, forms.Form):
    email = forms.EmailField(
        label="New email address",
        widget=forms.EmailInput(attrs={"class": FIELD_CLASS, "placeholder": "new@example.com", "autocomplete": "email"}),
    )
    current_password = forms.CharField(label="Current password", widget=_CURRENT_PW)

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class ChangePhoneForm(_CurrentPasswordMixin, forms.Form):
    phone = forms.CharField(
        label="Phone number",
        validators=[validate_tz_phone],
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "2557XXXXXXXX", "autocomplete": "tel"}),
    )
    current_password = forms.CharField(label="Current password", widget=_CURRENT_PW)


class ChangePasswordForm(_CurrentPasswordMixin, forms.Form):
    current_password = forms.CharField(label="Current (or temporary) password", widget=_CURRENT_PW)
    password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(attrs={"class": FIELD_CLASS, "autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={"class": FIELD_CLASS, "autocomplete": "new-password"}),
    )

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        elif p1:
            if cleaned.get("current_password") == p1:
                self.add_error("password1", "Choose a password different from the current one.")
            try:
                validate_password(p1, user=self.user)
            except forms.ValidationError as exc:
                self.add_error("password1", exc.messages)
        return cleaned
