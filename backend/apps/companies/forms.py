from django.utils.translation import gettext, gettext_lazy, ngettext
from django import forms
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.companies.models import (
    Organization,
    OrganizationMemberRole,
    OrganizationType,
)
from apps.companies.services import get_user_organization
from apps.core.skill_fields import normalize_skills, skills_to_text
from apps.documents.models import Document
from apps.locations.models import District, Ward
from apps.slots.models import Slot
from apps.companies.identifiers import normalize_brela, normalize_tin

FIELD_CLASS = (
    "w-full bg-surface-container-lowest text-on-surface font-body-md text-body-md "
    "px-sm py-2.5 rounded-lg border border-outline-variant "
    "focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent "
    "transition-all placeholder:text-on-surface-variant/50"
)


class OrganizationProfileForm(forms.ModelForm):
    organization_type = forms.ChoiceField(
        choices=OrganizationType.choices,
        required=False,
        label=_("Organization Type"),
        widget=forms.Select(attrs={"class": "ipt-select"}),
    )

    class Meta:
        model = Organization
        fields = (
            "name",
            "organization_type",
            "industry",
            "email",
            "phone",
            "brela_number",
            "tin",
            "description",
            "region",
            "district",
            "ward",
            "street",
        )
        labels = {
            "name": _("Organization Name"),
            "street": _("Address / Street"),
            "brela_number": _("BRELA registration number"),
            "tin": _("TIN (Taxpayer Identification Number)"),
        }
        widgets = {
            "name": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "industry": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "email": forms.EmailInput(attrs={"class": FIELD_CLASS, "placeholder": "info@organization.co.tz"}),
            "phone": forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "2557XXXXXXXX"}),
            "brela_number": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "tin": forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "123-456-789", "inputmode": "numeric"}),
            "description": forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 3}),
            "region": forms.Select(attrs={"class": "ipt-select", "id": "id_region"}),
            "district": forms.Select(attrs={"class": "ipt-select", "id": "id_district"}),
            "ward": forms.Select(attrs={"class": "ipt-select", "id": "id_ward"}),
            "street": forms.TextInput(attrs={"class": FIELD_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["industry"].required = True
        self.fields["email"].required = False
        self.fields["phone"].required = False
        data = self.data if hasattr(self, "data") else None
        region_id = data.get("region") if data else None
        district_id = data.get("district") if data else None
        if region_id:
            self.fields["district"].queryset = District.objects.filter(region_id=region_id)
        elif self.instance and self.instance.pk and self.instance.region_id:
            self.fields["district"].queryset = District.objects.filter(region_id=self.instance.region_id)
        else:
            self.fields["district"].queryset = District.objects.none()
        if district_id:
            self.fields["ward"].queryset = Ward.objects.filter(district_id=district_id)
        elif self.instance and self.instance.pk and self.instance.district_id:
            self.fields["ward"].queryset = Ward.objects.filter(district_id=self.instance.district_id)
        else:
            self.fields["ward"].queryset = Ward.objects.none()

    def clean_brela_number(self):
        return normalize_brela(self.cleaned_data.get("brela_number"))

    def clean_tin(self):
        return normalize_tin(self.cleaned_data.get("tin"))

    def clean_organization_type(self):
        val = self.cleaned_data.get("organization_type")
        if not val:
            if self.instance and self.instance.pk and self.instance.organization_type:
                return self.instance.organization_type
            return OrganizationType.COMPANY
        return val


OrganizationProfileForm = OrganizationProfileForm


class OrganizationMemberCreateForm(forms.Form):
    email = forms.EmailField(
        label=_("Member Email"),
        widget=forms.EmailInput(
            attrs={"class": FIELD_CLASS, "placeholder": "staff@organization.co.tz", "autocomplete": "off"}
        ),
    )
    first_name = forms.CharField(
        label=_("First name"),
        max_length=150,
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": gettext_lazy("First name")}),
    )
    last_name = forms.CharField(
        label=_("Last name"),
        max_length=150,
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": gettext_lazy("Last name")}),
    )
    position = forms.CharField(
        label=_("Position in the organization"),
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": gettext_lazy("e.g. Recruitment Officer")}),
    )
    phone = forms.CharField(
        label=_("Phone"),
        required=False,
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "2557XXXXXXXX", "autocomplete": "off"}),
    )
    role = forms.ChoiceField(
        label=_("Role"),
        choices=[
            (OrganizationMemberRole.RECRUITER, _("Recruiter")),
            (OrganizationMemberRole.MANAGER, _("Deputy Manager")),
        ],
        widget=forms.Select(attrs={"class": "ipt-select"}),
    )
    password = forms.CharField(
        label=_("Temporary Password"),
        widget=forms.PasswordInput(
            attrs={"class": FIELD_CLASS, "placeholder": gettext_lazy("At least 8 characters"), "autocomplete": "new-password"}
        ),
    )

    def clean_password(self):
        pw = self.cleaned_data.get("password")
        if pw:
            validate_password(pw)
        return pw


class OrganizationDocumentUploadForm(forms.Form):
    doc_type = forms.ChoiceField(
        choices=[],
        widget=forms.Select(attrs={"class": "ipt-select"}),
    )
    file = forms.FileField(
        label=gettext_lazy("File (< 2MB)"),
        widget=forms.ClearableFileInput(attrs={"class": "ipt-file-input", "accept": ".pdf,.png,.jpg,.jpeg,.doc,.docx"}),
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        organization = kwargs.pop("organization", None)
        super().__init__(*args, **kwargs)
        all_choices = [
            (Document.DocType.BRELA_CERT, gettext_lazy("BRELA Registration Certificate")),
            (Document.DocType.TIN_CERT, gettext_lazy("TIN Certificate")),
            (Document.DocType.BUSINESS_LICENSE, gettext_lazy("Business License")),
        ]
        org = organization or (get_user_organization(user) if user else None)
        if org is not None:
            existing_types = set(
                Document.objects.filter(owner_id__in=org.historical_member_user_ids()).values_list("doc_type", flat=True)
            )
            self.fields["doc_type"].choices = [
                c for c in all_choices if c[0] not in existing_types
            ]
        elif user:
            existing_types = set(
                Document.objects.filter(owner=user).values_list("doc_type", flat=True)
            )
            self.fields["doc_type"].choices = [
                c for c in all_choices if c[0] not in existing_types
            ]
        else:
            self.fields["doc_type"].choices = all_choices


class SlotForm(forms.ModelForm):
    skills_required = forms.CharField(
        required=False,
        label=_("Required skills (comma separated)"),
        widget=forms.TextInput(attrs={"class": FIELD_CLASS}),
    )

    class Meta:
        model = Slot
        fields = (
            "title",
            "industry",
            "description",
            "role_type",
            "district",
            "department",
            "education_level",
            "academic_year",
            "application_deadline",
            "start_date",
            "duration_weeks",
            "reporting_contact",
            "capacity",
            "stipend_available",
            "stipend_amount",
            "skills_required",
        )
        widgets = {
            "start_date": forms.DateInput(attrs={"class": FIELD_CLASS, "type": "date"}, format="%Y-%m-%d"),
            "duration_weeks": forms.NumberInput(attrs={"class": FIELD_CLASS, "min": 1, "max": 104}),
            "reporting_contact": forms.TextInput(
                attrs={"class": FIELD_CLASS, "placeholder": gettext_lazy("e.g. Ask for Ms. Mushi, HR, 2nd floor")}
            ),
            "application_deadline": forms.DateTimeInput(
                attrs={"class": FIELD_CLASS, "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "title": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "industry": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "description": forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 3}),
            "role_type": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "district": forms.Select(attrs={"class": "ipt-select", "id": "id_district"}),
            "department": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "education_level": forms.Select(attrs={"class": "ipt-select"}),
            "academic_year": forms.Select(attrs={"class": "ipt-select"}),
            "capacity": forms.NumberInput(attrs={"class": FIELD_CLASS, "min": 1}),
            "stipend_available": forms.CheckboxInput(attrs={"class": "rounded border-gray-300"}),
            "stipend_amount": forms.NumberInput(attrs={"class": FIELD_CLASS, "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Optional: when left blank the view fills in the organization's own industry.
        self.fields["industry"].required = False
        self.fields["industry"].label = _("Industry / sector")
        self.fields["industry"].help_text = _("Leave blank to use your organization's industry.")
        region_id = self.data.get("region") if hasattr(self, "data") else None
        if region_id:
            self.fields["district"].queryset = District.objects.filter(region_id=region_id)
        else:
            self.fields["district"].queryset = District.objects.none()
        if self.instance and self.instance.pk:
            self.fields["skills_required"].initial = skills_to_text(self.instance.skills_required)
            self.initial["skills_required"] = skills_to_text(self.instance.skills_required)

    def clean_skills_required(self):
        return normalize_skills(self.cleaned_data.get("skills_required"))

    def clean_capacity(self):
        capacity = self.cleaned_data.get("capacity")
        if capacity is None:
            return capacity
        if capacity < 1:
            raise forms.ValidationError(gettext("A slot needs at least 1 seat."))
        if not self.instance._state.adding:
            booked = self.instance.booked_count
            if capacity < booked:
                raise forms.ValidationError(
                    ngettext(
                        '%(booked)s seat is already booked or paid for, so the capacity cannot be lower than %(booked)s.',
                        '%(booked)s seats are already booked or paid for, so the capacity cannot be lower than %(booked)s.',
                        booked,
                    ) % {"booked": booked}
                )
        return capacity

    def clean_application_deadline(self):
        deadline = self.cleaned_data.get("application_deadline")
        if deadline is None:
            return deadline
        unchanged = self.instance.pk and deadline == self.instance.application_deadline
        if not unchanged and deadline <= timezone.now():
            raise forms.ValidationError(gettext("The application deadline must be in the future."))
        return deadline

    def clean_start_date(self):
        start = self.cleaned_data.get("start_date")
        if start is None:
            return start
        unchanged = not self.instance._state.adding and start == self.instance.start_date
        if not unchanged and start < timezone.localdate():
            raise forms.ValidationError(gettext("The start date cannot be in the past."))
        return start

    def clean(self):
        cleaned = super().clean()
        start, deadline = cleaned.get("start_date"), cleaned.get("application_deadline")
        if start and deadline and start < timezone.localtime(deadline).date():
            self.add_error("start_date", gettext("The placement cannot start before applications close."))
        stipend = cleaned.get("stipend_available")
        amount = cleaned.get("stipend_amount")
        if stipend and not amount:
            self.add_error("stipend_amount", gettext("Enter the monthly stipend amount."))
        elif not stipend:
            cleaned["stipend_amount"] = None

        edu_level = cleaned.get("education_level")
        academic_year = cleaned.get("academic_year")
        if edu_level == 7 and not academic_year:
            self.add_error("academic_year", gettext("Academic year is required for Higher Diploma."))
        elif edu_level != 7:
            cleaned["academic_year"] = None

        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.skills_required = self.cleaned_data["skills_required"]
        if commit:
            instance.save()
        return instance