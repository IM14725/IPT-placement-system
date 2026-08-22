from django.contrib import admin

from apps.locations.models import District, Region, Ward


class DistrictInline(admin.TabularInline):
    model = District
    extra = 0
    fields = ("name",)


class WardInline(admin.TabularInline):
    model = Ward
    extra = 0
    fields = ("name",)


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "district_count")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    inlines = (DistrictInline,)

    def district_count(self, obj):
        return obj.districts.count()

    district_count.short_description = "Districts"


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    list_display = ("name", "region")
    list_filter = ("region",)
    search_fields = ("name",)
    inlines = (WardInline,)


@admin.register(Ward)
class WardAdmin(admin.ModelAdmin):
    list_display = ("name", "district", "region")
    list_filter = ("district__region",)
    search_fields = ("name",)

    def region(self, obj):
        return obj.district.region

    region.short_description = "Region"