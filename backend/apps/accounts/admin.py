from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.accounts.models import AdminRole, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "role", "phone", "email_verified", "phone_verified", "is_staff")
    list_filter = ("role", "is_staff", "is_active", "email_verified", "phone_verified")
    search_fields = ("email", "phone")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Identity", {"fields": ("first_name", "last_name", "phone", "role")}),
        ("Verification", {"fields": ("email_verified", "phone_verified")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "role", "phone", "password1", "password2")}),
    )


@admin.register(AdminRole)
class AdminRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "is_system", "updated_at")
    search_fields = ("name",)