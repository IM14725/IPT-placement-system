from django.contrib import admin
from django import forms as django_forms

from apps.slots.models import Slot
from apps.core.skill_fields import normalize_skills, skills_to_text


class SlotAdminForm(django_forms.ModelForm):
    skills_required = django_forms.CharField(
        required=False,
        label="Required skills (comma separated)",
        widget=django_forms.TextInput(attrs={"size": 80}),
    )

    class Meta:
        model = Slot
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["skills_required"].initial = skills_to_text(self.instance.skills_required)
            self.initial["skills_required"] = skills_to_text(self.instance.skills_required)

    def clean_skills_required(self):
        return normalize_skills(self.cleaned_data.get("skills_required"))


@admin.register(Slot)
class SlotAdmin(admin.ModelAdmin):
    form = SlotAdminForm
    list_display = (
        "title",
        "company",
        "industry",
        "role_type",
        "district",
        "education_level",
        "capacity",
        "available_count",
        "stipend_available",
        "status",
    )
    list_filter = ("status", "industry", "education_level", "stipend_available", "company__verification_status")
    search_fields = ("title", "company__name")
    readonly_fields = ("available_count", "created_at", "updated_at")