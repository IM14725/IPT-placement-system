from django.contrib import admin
from django import forms as django_forms
from django.urls import reverse
from django.utils.html import format_html

from apps.students.models import StudentProfile
from apps.core.admin_mixins import VerificationAdminMixin
from apps.core.skill_fields import normalize_skills, skills_to_text


class StudentProfileAdminForm(django_forms.ModelForm):
    skills = django_forms.CharField(
        required=False,
        label="Skills (comma separated)",
        widget=django_forms.TextInput(attrs={"size": 80}),
    )

    class Meta:
        model = StudentProfile
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["skills"].initial = skills_to_text(self.instance.skills)
            self.initial["skills"] = skills_to_text(self.instance.skills)

    def clean_skills(self):
        return normalize_skills(self.cleaned_data.get("skills"))


@admin.register(StudentProfile)
class StudentProfileAdmin(VerificationAdminMixin, admin.ModelAdmin):
    form = StudentProfileAdminForm
    # Student ID is captured on the profile (id_card_photo + student_id);
    # the results matrix is optional — no document uploads are required.
    REQUIRED_DOC_TYPES = ()
    list_display = (
        "user",
        "university",
        "course",
        "current_year",
        "education_level",
        "gpa",
        "view_documents",
        "verification_status",
        "reviewed_at",
    )
    list_filter = ("verification_status", "university", "course", "education_level")
    search_fields = ("user__email", "university", "course")
    list_editable = ("verification_status",)
    readonly_fields = ("created_at", "updated_at", "reviewed_at")
    fieldsets = (
        (None, {"fields": ("user", "university", "course", "current_year", "education_level", "gpa", "skills")}),
        ("Location", {"fields": ("region", "district")}),
        ("Verification", {"fields": ("verification_status", "rejection_reason", "reviewed_by", "reviewed_at")}),
    )

    @admin.display(description="Documents")
    def view_documents(self, obj):
        url = reverse("admin:documents_document_changelist") + f"?owner__id__exact={obj.user_id}"
        return format_html('<a href="{}">View</a>', url)