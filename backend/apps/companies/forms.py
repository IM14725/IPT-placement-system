from django import forms
from django.utils.translation import gettext_lazy as _

from apps.companies.models import CompanyProfile
from apps.core.skill_fields import normalize_skills, skills_to_text
from apps.documents.models import Document
from apps.locations.models import District, Ward
from apps.slots.models import Slot

FIELD_CLASS = (
    "w-full bg-surface-container-lowest text-on-surface font-body-md text-body-md "
    "px-sm py-2.5 rounded-lg border border-outline-variant "
    "focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent "
    "transition-all placeholder:text-on-surface-variant/50"
)


class CompanyProfileForm(forms.ModelForm):
    class Meta:
        model = CompanyProfile
        fields = ("name", "industry", "description", "region", "district", "ward", "street")
        widgets = {
            "name": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "industry": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "description": forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 3}),
            "region": forms.Select(attrs={"class": "ipt-select", "id": "id_region"}),
            "district": forms.Select(attrs={"class": "ipt-select", "id": "id_district"}),
            "ward": forms.Select(attrs={"class": "ipt-select", "id": "id_ward"}),
            "street": forms.TextInput(attrs={"class": FIELD_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        data = self.data if hasattr(self, "data") else None
        region_id = data.get("region") if data else None
        district_id = data.get("district") if data else None
        if region_id:
            self.fields["district"].queryset = District.objects.filter(region_id=region_id)
        else:
            self.fields["district"].queryset = District.objects.none()
        if district_id:
            self.fields["ward"].queryset = Ward.objects.filter(district_id=district_id)
        else:
            self.fields["ward"].queryset = Ward.objects.none()


class CompanyDocumentUploadForm(forms.Form):
    doc_type = forms.ChoiceField(
        choices=[
            (Document.DocType.BRELA_CERT, "BRELA Registration Certificate"),
            (Document.DocType.TIN_CERT, "TIN Certificate"),
            (Document.DocType.BUSINESS_LICENSE, "Business License"),
        ],
        widget=forms.Select(attrs={"class": "ipt-select"}),
    )
    file = forms.FileField(
        label="File (< 2MB)",
        widget=forms.ClearableFileInput(attrs={"class": "ipt-file-input", "accept": ".pdf,.png,.jpg,.jpeg,.doc,.docx"}),
    )


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
            "description",
            "industry",
            "role_type",
            "district",
            "street",
            "department",
            "level",
            "education_level",
            "capacity",
            "stipend_available",
            "stipend_amount",
            "skills_required",
        )
        widgets = {
            "title": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "description": forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 3}),
            "industry": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "role_type": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "district": forms.Select(attrs={"class": "ipt-select", "id": "id_district"}),
            "street": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "department": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "level": forms.NumberInput(attrs={"class": FIELD_CLASS, "min": 1, "max": 8}),
            "education_level": forms.Select(attrs={"class": "ipt-select"}),
            "capacity": forms.NumberInput(attrs={"class": FIELD_CLASS, "min": 1}),
            "stipend_available": forms.CheckboxInput(attrs={"class": "rounded border-gray-300"}),
            "stipend_amount": forms.NumberInput(attrs={"class": FIELD_CLASS, "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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

    def clean(self):
        cleaned = super().clean()
        stipend = cleaned.get("stipend_available")
        amount = cleaned.get("stipend_amount")
        if stipend and not amount:
            self.add_error("stipend_amount", "Enter the monthly stipend amount.")
        elif not stipend:
            cleaned["stipend_amount"] = None
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.skills_required = self.cleaned_data["skills_required"]
        if commit:
            instance.save()
        return instance