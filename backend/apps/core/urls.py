from django.urls import path

from apps.core import views

urlpatterns = [
    path("", views.health, name="health"),
    path("ledger/", views.ledger, name="platform-ledger"),
    path("metrics/", views.metrics, name="platform-metrics"),
    # Phase 5 — Admin console
    path("verifications/", views.verification_queue, name="platform-verifications"),
    path("directory/", views.directory, name="platform-directory"),
    path(
        "verifications/students/<int:pk>/",
        views.student_verification,
        name="platform-student-verification",
    ),
    path(
        "verifications/companies/<int:pk>/",
        views.company_verification,
        name="platform-company-verification",
    ),
    path("templates/", views.notification_templates, name="platform-templates"),
    path("audit-logs/", views.audit_logs, name="platform-audit-logs"),
    path("roles/", views.role_management, name="platform-roles"),
    path("settings/", views.admin_settings, name="platform-settings"),
    path("integrity/", views.integrity, name="platform-integrity"),
]