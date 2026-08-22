from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.skill_fields import normalize_skills, skills_to_text
from apps.documents.models import Document
from apps.locations.models import District, Ward
from apps.students.models import StudentProfile

FIELD_CLASS = (
    "mt-1 block w-full rounded-md border-gray-300 shadow-sm "
    "focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm"
)


class StudentProfileForm(forms.ModelForm):
    skills = forms.CharField(
        required=False,
        label=_("Skills (comma separated)"),
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "Python, SQL, Data Analysis"}),
        help_text=_("Separate skills with commas."),
    )
    profile_photo = forms.ImageField(
        required=False,
        label=_("Profile photo"),
        help_text=_("A clear headshot (max 2MB)."),
        widget=forms.FileInput(attrs={"class": "ipt-file-input", "accept": "image/*"}),
    )
    id_card_photo = forms.ImageField(
        required=False,
        label=_("ID card photo"),
        help_text=_("A clear photo of your student ID card (max 2MB)."),
        widget=forms.FileInput(attrs={"class": "ipt-file-input", "accept": "image/*"}),
    )

    class Meta:
        model = StudentProfile
        fields = (
            "student_id",
            "gender",
            "profile_photo",
            "id_card_photo",
            "university",
            "course",
            "current_year",
            "education_level",
            "gpa",
            "region",
            "district",
            "ward",
            "skills",
        )
        widgets = {
            "student_id": forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "e.g. 2022-04-01234"}),
            "gender": forms.Select(attrs={"class": "ipt-select"}),
            "university": forms.TextInput(attrs={"class": FIELD_CLASS, "autocomplete": "off"}),
            "course": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "current_year": forms.NumberInput(attrs={"class": FIELD_CLASS, "min": 1, "max": 8}),
            "education_level": forms.Select(attrs={"class": "ipt-select"}),
            "gpa": forms.NumberInput(attrs={"class": FIELD_CLASS, "step": "0.01", "min": 0, "max": 5}),
            "region": forms.Select(attrs={"class": "ipt-select", "id": "id_region"}),
            "district": forms.Select(attrs={"class": "ipt-select", "id": "id_district"}),
            "ward": forms.Select(attrs={"class": "ipt-select", "id": "id_ward"}),
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
        if self.instance and self.instance.pk:
            self.fields["skills"].initial = skills_to_text(self.instance.skills)
            self.initial["skills"] = skills_to_text(self.instance.skills)

    def clean_profile_photo(self):
        photo = self.cleaned_data.get("profile_photo")
        if not photo:
            return photo
        if photo.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Profile photo must be under 2MB.")
        return photo

    def clean_id_card_photo(self):
        photo = self.cleaned_data.get("id_card_photo")
        if not photo:
            return photo
        if photo.size > 2 * 1024 * 1024:
            raise forms.ValidationError("ID card photo must be under 2MB.")
        return photo

    def clean_skills(self):
        return normalize_skills(self.cleaned_data.get("skills"))

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.skills = self.cleaned_data["skills"]
        if commit:
            instance.save()
        return instance


class DocumentUploadForm(forms.Form):
    doc_type = forms.ChoiceField(
        choices=[
            (Document.DocType.STUDENT_ID, "Student ID Card"),
            (Document.DocType.RESULTS_MATRIX, "Semester Results Matrix"),
            (Document.DocType.CV, "Curriculum Vitae (CV)"),
            (Document.DocType.INTRO_LETTER, "University Introduction Letter"),
        ],
        widget=forms.Select(attrs={"class": "ipt-select"}),
    )
    file = forms.FileField(
        label="File (< 2MB)",
        widget=forms.ClearableFileInput(attrs={"class": "ipt-file-input", "accept": ".pdf,.png,.jpg,.jpeg,.doc,.docx"}),
    )