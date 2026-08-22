from rest_framework import serializers

from apps.core.education import education_level_label
from apps.slots.models import Slot


class SlotSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    company_approved = serializers.BooleanField(
        source="company.is_approved", read_only=True
    )
    region = serializers.SerializerMethodField()
    district_name = serializers.CharField(source="district.name", read_only=True)
    available_count = serializers.SerializerMethodField()
    booked_count = serializers.SerializerMethodField()
    education_level_display = serializers.SerializerMethodField()

    class Meta:
        model = Slot
        fields = (
            "id",
            "title",
            "description",
            "industry",
            "role_type",
            "company",
            "company_name",
            "company_approved",
            "region",
            "district",
            "district_name",
            "street",
            "department",
            "level",
            "education_level",
            "education_level_display",
            "capacity",
            "booked_count",
            "available_count",
            "stipend_available",
            "stipend_amount",
            "skills_required",
            "status",
        )

    def get_region(self, obj):
        return {
            "id": obj.district.region_id,
            "name": obj.district.region.name,
        }

    def get_available_count(self, obj):
        if hasattr(obj, "_available"):
            return obj._available
        return obj.available_count

    def get_booked_count(self, obj):
        if hasattr(obj, "_booked"):
            return obj._booked
        return obj.booked_count

    def get_education_level_display(self, obj):
        return education_level_label(obj.education_level)