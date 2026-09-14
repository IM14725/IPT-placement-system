from django.urls import path

from apps.core import views

urlpatterns = [
    path("", views.health, name="health"),
    path("ledger/", views.ledger, name="platform-ledger"),
    path("metrics/", views.metrics, name="platform-metrics"),
    # Phase 5 — Admin console
    path("verifications/", views.verification_queue, name="platform-verifications"),
    path("directory/", views.directory, name="platform-directory"),
    path("slots/", views.admin_slots, name="platform-slots"),
    path(
        "verifications/students/<uuid:pk>/",
        views.student_verification,
        name="platform-student-verification",
    ),
    path(
        "verifications/students/<uuid:pk>/edit/",
        views.platform_student_edit,
        name="platform-student-edit",
    ),
    path(
        "verifications/companies/<uuid:pk>/",
        views.company_verification,
        name="platform-company-verification",
    ),
    path(
        "verifications/companies/<uuid:pk>/edit/",
        views.platform_company_edit,
        name="platform-company-edit",
    ),
    path("templates/", views.notification_templates, name="platform-templates"),
    path("audit-logs/", views.audit_logs, name="platform-audit-logs"),
    path("roles/", views.role_management, name="platform-roles"),
    path("settings/", views.admin_settings, name="platform-settings"),
    path("integrity/", views.integrity, name="platform-integrity"),
    # Legal pages — public
    path("legal/terms/", views.legal_terms, name="legal-terms"),
    path("legal/privacy/", views.legal_privacy, name="legal-privacy"),
    path("legal/pdpa-request/", views.legal_pdpa_request, name="legal-pdpa-request"),
]
