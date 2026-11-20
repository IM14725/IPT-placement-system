import uuid
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from apps.vouchers.models import CodeMode, DiscountType, FeeVoucher, VoucherBatch
from apps.vouchers.services import MAX_CODES_PER_REQUEST, normalize_code

SELECT = {"class": "ipt-select"}
def multi(empty):
    """A multi-select drawn by static/js/multi-picker.js (chips + search); a plain list without JavaScript."""
    return forms.SelectMultiple(attrs={"data-multi-picker": "", "data-empty": empty, "size": 6})


DATETIME_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"]


def _lines(value):
    """Split a textarea on new lines and commas, dropping blanks and repeats (order kept)."""
    seen, out = set(), []
    for chunk in (value or "").replace(",", "\n").splitlines():
        item = chunk.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


class VoucherBatchForm(forms.Form):
    """Everything an admin sets when issuing a batch: the terms, the codes and who may use them."""

    # ---- Terms
    name = forms.CharField(max_length=160, label="Batch name", widget=forms.TextInput(attrs={"placeholder": "e.g. NGO sponsorship, Mwanza 2026"}))
    description = forms.CharField(
        required=False, label="Internal note", widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Who it is for and why. Only admins see this.",
    )
    discount_type = forms.ChoiceField(choices=DiscountType.choices, label="Discount type", widget=forms.Select(attrs=SELECT))
    discount_value = forms.DecimalField(
        required=False, min_value=Decimal("0"), max_digits=12, decimal_places=2, label="Discount value",
        help_text="Percent (1-100) or TZS amount. Ignored for a full waiver.",
        widget=forms.NumberInput(attrs={"placeholder": "e.g. 50"}),
    )
    starts_at = forms.DateTimeField(
        required=False, label="Valid from", input_formats=DATETIME_FORMATS,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    expires_at = forms.DateTimeField(
        required=False, label="Expires", input_formats=DATETIME_FORMATS,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        help_text="Leave empty for no expiry.",
    )
    per_student_limit = forms.IntegerField(
        min_value=1, max_value=50, initial=1, label="Uses per student",
        help_text="How many times one student can use codes from this batch.",
    )
    budget_tzs = forms.DecimalField(
        required=False, min_value=Decimal("0"), max_digits=14, decimal_places=2, label="Budget cap (TZS)",
        help_text="Stop the batch once this much discount has been given. Empty means no cap.",
    )

    # ---- Codes
    code_mode = forms.ChoiceField(choices=CodeMode.choices, label="Codes", widget=forms.Select(attrs=SELECT))
    quantity = forms.IntegerField(
        required=False, min_value=1, max_value=MAX_CODES_PER_REQUEST, initial=50, label="How many unique codes",
        help_text=f"Single-use codes, up to {MAX_CODES_PER_REQUEST} at a time.",
    )
    shared_code = forms.CharField(
        required=False, max_length=30, label="Campaign code (optional)",
        help_text="Letters and digits, e.g. WELCOME2026. Empty generates one.",
        widget=forms.TextInput(attrs={"placeholder": "WELCOME2026"}),
    )
    shared_max_uses = forms.IntegerField(
        required=False, min_value=1, max_value=1_000_000, initial=100, label="Campaign code: total uses",
        help_text="How many students can use the shared code in total.",
    )

    # ---- Who may use it (empty = no limit; every filled restriction must match)
    universities = forms.ModelMultipleChoiceField(
        queryset=None, required=False, label="Universities", widget=multi("Any university"),
        help_text="Type to search and click to add. Leave empty for any university.",
    )
    courses = forms.CharField(
        required=False, label="Courses", widget=forms.Textarea(attrs={"rows": 3}),
        help_text="One per line, matched to the student's course ignoring capital letters.",
    )
    regions = forms.ModelMultipleChoiceField(queryset=None, required=False, label="Student's region", widget=multi("Any region"))
    districts = forms.ModelMultipleChoiceField(queryset=None, required=False, label="Student's district", widget=multi("Any district"))
    signup_from = forms.DateTimeField(
        required=False, label="Registered from", input_formats=DATETIME_FORMATS,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        help_text="Early-signup campaigns: only accounts created in this window.",
    )
    signup_to = forms.DateTimeField(
        required=False, label="Registered until", input_formats=DATETIME_FORMATS,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    allowed_emails = forms.CharField(
        required=False, label="Named students (emails)", widget=forms.Textarea(attrs={"rows": 3}),
        help_text="One email per line. Only these accounts can use the codes, so a sponsor's cohort cannot share them.",
    )

    # ---- Which slots it applies to
    organizations = forms.ModelMultipleChoiceField(
        queryset=None, required=False, label="Only these organizations", widget=multi("Any organization"),
        help_text="For example a company funding its own intake.",
    )
    slot_ids = forms.CharField(
        required=False, label="Only these slots (IDs)", widget=forms.Textarea(attrs={"rows": 2}),
        help_text="One slot ID per line (from the slot's page address).",
    )
    slot_districts = forms.ModelMultipleChoiceField(queryset=None, required=False, label="Slot is in district", widget=multi("Any district"))
    slot_start_from = forms.DateField(required=False, label="Slot starts from", widget=forms.DateInput(attrs={"type": "date"}))
    slot_start_to = forms.DateField(required=False, label="Slot starts until", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.companies.models import Organization
        from apps.locations.models import District, Region
        from apps.students.models import University, VerificationStatus

        self.fields["universities"].queryset = University.objects.filter(verification_status=VerificationStatus.APPROVED)
        self.fields["regions"].queryset = Region.objects.order_by("name")
        districts = District.objects.select_related("region").order_by("region__name", "name")
        self.fields["districts"].queryset = districts
        self.fields["slot_districts"].queryset = districts
        self.fields["organizations"].queryset = Organization.objects.filter(
            verification_status=VerificationStatus.APPROVED
        ).order_by("name")

    # ---- cleaning
    def clean_courses(self):
        return _lines(self.cleaned_data.get("courses"))

    def clean_allowed_emails(self):
        emails = [e.lower() for e in _lines(self.cleaned_data.get("allowed_emails"))]
        for email in emails:
            try:
                validate_email(email)
            except ValidationError:
                raise forms.ValidationError(f"'{email}' is not a valid email address.")
        return emails

    def clean_slot_ids(self):
        from apps.slots.models import Slot

        ids = []
        for raw in _lines(self.cleaned_data.get("slot_ids")):
            try:
                ids.append(uuid.UUID(raw))
            except ValueError:
                raise forms.ValidationError(f"'{raw}' is not a slot ID.")
        found = set(Slot.objects.filter(pk__in=ids).values_list("pk", flat=True))
        missing = [str(i) for i in ids if i not in found]
        if missing:
            raise forms.ValidationError(f"No slot with ID {missing[0]}.")
        return list(found)

    def clean_shared_code(self):
        return normalize_code(self.cleaned_data.get("shared_code"))

    def clean(self):
        cleaned = super().clean()
        dtype = cleaned.get("discount_type")
        value = cleaned.get("discount_value")
        if dtype == DiscountType.PERCENT:
            if value is None or not (Decimal("1") <= value <= Decimal("100")):
                self.add_error("discount_value", "Enter a percentage between 1 and 100.")
        elif dtype == DiscountType.FIXED:
            if value is None or value <= 0:
                self.add_error("discount_value", "Enter the amount in TZS.")
        elif dtype == DiscountType.WAIVER:
            cleaned["discount_value"] = Decimal("0")
        starts, expires = cleaned.get("starts_at"), cleaned.get("expires_at")
        if starts and expires and expires <= starts:
            self.add_error("expires_at", "The expiry must be after the start.")
        s_from, s_to = cleaned.get("signup_from"), cleaned.get("signup_to")
        if s_from and s_to and s_to < s_from:
            self.add_error("signup_to", "This must not be before 'Registered from'.")
        d_from, d_to = cleaned.get("slot_start_from"), cleaned.get("slot_start_to")
        if d_from and d_to and d_to < d_from:
            self.add_error("slot_start_to", "This must not be before 'Slot starts from'.")
        mode = cleaned.get("code_mode")
        if mode == CodeMode.UNIQUE:
            if not cleaned.get("quantity"):
                self.add_error("quantity", "Enter how many codes to create.")
        elif mode == CodeMode.SHARED:
            if not cleaned.get("shared_max_uses"):
                self.add_error("shared_max_uses", "Enter how many students may use the code.")
            code = cleaned.get("shared_code")
            if code:
                if len(code) < 4:
                    self.add_error("shared_code", "Use at least 4 letters or digits.")
                elif FeeVoucher.objects.filter(code=code).exists():
                    self.add_error("shared_code", "That code already exists.")
        return cleaned

    def save(self, *, issued_by, issuer_label) -> VoucherBatch:
        """Create the batch and its codes. Returns the batch (the codes hang off ``batch.codes``)."""
        from apps.vouchers.services import create_codes, generate_code

        data = self.cleaned_data
        batch = VoucherBatch.objects.create(
            name=data["name"].strip(),
            description=data.get("description", "").strip(),
            discount_type=data["discount_type"],
            discount_value=data.get("discount_value") or Decimal("0"),
            code_mode=data["code_mode"],
            issued_by=issued_by,
            issuer_label=issuer_label,
            starts_at=data.get("starts_at"),
            expires_at=data.get("expires_at"),
            per_student_limit=data["per_student_limit"],
            budget_tzs=data.get("budget_tzs"),
            courses=data.get("courses", []),
            signup_from=data.get("signup_from"),
            signup_to=data.get("signup_to"),
            allowed_emails=data.get("allowed_emails", []),
            slot_start_from=data.get("slot_start_from"),
            slot_start_to=data.get("slot_start_to"),
        )
        batch.universities.set(data.get("universities") or [])
        batch.regions.set(data.get("regions") or [])
        batch.districts.set(data.get("districts") or [])
        batch.organizations.set(data.get("organizations") or [])
        batch.slot_districts.set(data.get("slot_districts") or [])
        batch.slots.set(data.get("slot_ids") or [])
        if data["code_mode"] == CodeMode.UNIQUE:
            create_codes(batch, data["quantity"], max_uses=1)
        else:
            code = data.get("shared_code") or generate_code()
            FeeVoucher.objects.create(batch=batch, code=code, max_uses=data["shared_max_uses"])
        return batch
