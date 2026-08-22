from django import forms
from django.contrib.auth.password_validation import validate_password

from apps.accounts.models import User, UserRole

FIELD_CLASS = (
    "w-full bg-surface-container-lowest text-on-surface font-body-md text-body-md "
    "px-sm py-3 rounded-lg border border-outline-variant "
    "focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent "
    "transition-all placeholder:text-on-surface-variant/50"
)


def _normalize_phone(value):
    return "".join(ch for ch in (value or "") if ch.isdigit() or ch == "+")


def validate_tz_phone(value):
    if not value:
        return value
    normalized = _normalize_phone(value)
    digits = normalized.lstrip("+")
    if not digits.startswith("255"):
        raise forms.ValidationError("Phone number must start with 255 (Tanzania country code).")
    if len(digits) != 12:
        raise forms.ValidationError("Phone number must be 12 digits (255 + 9-digit number).")
    if digits[3] not in ("6", "7"):
        raise forms.ValidationError("Phone number must start with 2556 or 2557.")
    return "+" + digits if normalized.startswith("+") else digits


class RegisterForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(
            attrs={"class": FIELD_CLASS, "placeholder": "At least 8 characters", "autocomplete": "new-password"}
        ),
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={"class": FIELD_CLASS, "placeholder": "Repeat your password", "autocomplete": "new-password"}),
    )
    role = forms.ChoiceField(
        choices=[(UserRole.STUDENT, "Student"), (UserRole.COMPANY, "Company")],
        widget=forms.RadioSelect(attrs={"class": "peer sr-only"}),
    )
    phone = forms.CharField(
        required=False,
        validators=[validate_tz_phone],
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "2557XXXXXXXX", "autocomplete": "tel"}),
    )
    company_name = forms.CharField(
        required=False,
        label="Company name",
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "e.g. Amani Logistics Ltd"}),
    )

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "phone", "role", "company_name")
        widgets = {
            "email": forms.EmailInput(attrs={"class": FIELD_CLASS, "placeholder": "you@example.com", "autocomplete": "email"}),
            "first_name": forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "First name"}),
            "last_name": forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "Last name"}),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        if p1:
            try:
                validate_password(p1, user=self.instance)
            except forms.ValidationError as exc:
                self.add_error("password1", exc.messages)
        role = cleaned.get("role")
        if role == UserRole.COMPANY:
            if not (cleaned.get("company_name") or "").strip():
                self.add_error("company_name", "Company name is required.")
        else:
            if not (cleaned.get("first_name") or "").strip():
                self.add_error("first_name", "First name is required.")
            if not (cleaned.get("last_name") or "").strip():
                self.add_error("last_name", "Last name is required.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        user.role = self.cleaned_data["role"]
        if user.role == UserRole.COMPANY:
            user.first_name = (self.cleaned_data.get("company_name") or "").strip()
            user.last_name = ""
        if commit:
            user.save()
        return user


class LoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={"class": FIELD_CLASS, "placeholder": "you@example.com", "autocomplete": "email"}
        )
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": FIELD_CLASS, "placeholder": "Your password", "autocomplete": "current-password"})
    )


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={"class": FIELD_CLASS, "placeholder": "you@example.com", "autocomplete": "email"}
        )
    )
    phone = forms.CharField(
        validators=[validate_tz_phone],
        widget=forms.TextInput(
            attrs={"class": FIELD_CLASS, "placeholder": "2557XXXXXXXX", "autocomplete": "tel"}
        )
    )

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        phone = cleaned.get("phone")
        if email and phone:
            user = User.objects.filter(email=email.lower().strip()).first()
            if user is None:
                self.add_error("email", "No account found with this email address.")
            elif _normalize_phone(user.phone) != _normalize_phone(phone):
                self.add_error("phone", "Phone number does not match this account.")
        return cleaned


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