from django import forms
from django.utils.translation import gettext_lazy as _

from apps.accounts.forms import validate_tz_phone
from apps.core.skill_fields import normalize_skills, skills_to_text
from apps.documents.models import Document
from apps.locations.models import District, Ward
from apps.students.models import StudentProfile, VerificationStatus
from apps.students.universities import (
    SuggestionError,
    check_suggestion,
    selectable_universities,
    suggest_university,
)


def _university_label(university):
    if university.verification_status == VerificationStatus.PENDING:
        return f"{university.name} (awaiting review)"
    return university.name

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

    phone = forms.CharField(
        label=_("Phone number"),
        validators=[validate_tz_phone],
        help_text=_("Used by organizations and the platform to reach you, e.g. 2557XXXXXXXX."),
        widget=forms.TextInput(
            attrs={"class": FIELD_CLASS, "placeholder": "2557XXXXXXXX", "autocomplete": "tel"}
        ),
    )
    university_not_listed = forms.BooleanField(
        required=False,
        label=_("My university is not listed"),
    )
    university_suggestion = forms.CharField(
        required=False,
        max_length=200,
        label=_("Your university's full name"),
        widget=forms.TextInput(
            attrs={"class": FIELD_CLASS, "placeholder": "e.g. Example University of Tanzania", "autocomplete": "off"}
        ),
    )

    class Meta:
        model = StudentProfile
        fields = (
            "phone",
            "student_id",
            "gender",
            "profile_photo",
            "id_card_photo",
            "university",
            "university_not_listed",
            "university_suggestion",
            "course",
            "academic_year",
            "graduation_year",
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
            "university": forms.Select(attrs={"class": "ipt-select", "id": "id_university"}),
            "course": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "academic_year": forms.Select(attrs={"class": "ipt-select"}),
            "graduation_year": forms.NumberInput(
                attrs={"class": FIELD_CLASS, "min": 2000, "max": 2100, "placeholder": "e.g. 2027"}
            ),
            "education_level": forms.Select(attrs={"class": "ipt-select"}),
            "gpa": forms.NumberInput(attrs={"class": FIELD_CLASS, "step": "0.01", "min": 0, "max": 5}),
            "region": forms.Select(attrs={"class": "ipt-select", "id": "id_region"}),
            "district": forms.Select(attrs={"class": "ipt-select", "id": "id_district"}),
            "ward": forms.Select(attrs={"class": "ipt-select", "id": "id_ward"}),
        }

    def __init__(self, *args, user=None, require_phone=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["phone"].required = require_phone
        self._suggested_name = None
        self.fields["university"].queryset = selectable_universities(user)
        self.fields["university"].empty_label = _("Select your university (optional)")
        self.fields["university"].label_from_instance = _university_label
        if user is not None and not self.is_bound:
            self.initial.setdefault("phone", user.phone)
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

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("university_not_listed"):
            try:
                name, existing = check_suggestion(self.user, cleaned_data.get("university_suggestion"))
            except SuggestionError as exc:
                self.add_error("university_suggestion", str(exc))
            else:
                self._suggested_name = name
                cleaned_data["university"] = existing
        edu_level = cleaned_data.get("education_level")
        academic_year = cleaned_data.get("academic_year")
        if edu_level == 7 and not academic_year:
            self.add_error("academic_year", "Academic year is required for Higher Diploma.")
        elif edu_level != 7:
            cleaned_data["academic_year"] = None
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.skills = self.cleaned_data["skills"]
        if self._suggested_name:
            instance.university = suggest_university(self.user, self._suggested_name)
        if self.user is not None:
            phone = self.cleaned_data.get("phone")
            if phone and phone != self.user.phone:
                self.user.phone = phone
                self.user.save(update_fields=["phone", "updated_at"])
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