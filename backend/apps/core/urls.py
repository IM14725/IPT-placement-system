from django.urls import path

from apps.core import views, views_admins, views_governance

urlpatterns = [
    path("", views.health, name="health"),
    path("ledger/", views.ledger, name="platform-ledger"),
    path("metrics/", views.metrics, name="platform-metrics"),
    # Phase 5 — Admin console
    path("verifications/", views.verification_queue, name="platform-verifications"),
    path("directory/", views.directory, name="platform-directory"),
    path("slots/", views.admin_slots, name="platform-slots"),
    path("universities/", views_governance.universities, name="platform-universities"),
    path("account-status/", views_governance.account_status, name="platform-account-status"),
    path("reports/", views_governance.reports, name="platform-reports"),
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
        "verifications/organizations/<uuid:pk>/",
        views.organization_verification,
        name="platform-organization-verification",
    ),
    path(
        "verifications/organizations/<uuid:pk>/edit/",
        views.platform_organization_edit,
        name="platform-organization-edit",
    ),
    path("templates/", views.notification_templates, name="platform-templates"),
    path("audit-logs/", views.audit_logs, name="platform-audit-logs"),
    path("audit-logs/<uuid:log_id>/", views.audit_detail, name="platform-audit-detail"),
    path("roles/", views.role_management, name="platform-roles"),
    path("settings/", views.admin_settings, name="platform-settings"),
    path("integrity/", views.integrity, name="platform-integrity"),
    path("admins/", views_admins.admin_list, name="platform-admins"),
    path("admins/new/", views_admins.admin_create, name="platform-admin-new"),
    path("admins/<uuid:pk>/", views_admins.admin_detail, name="platform-admin-detail"),
    path("recovery/", views_admins.recovery_queue, name="platform-recovery"),
    path("break-glass/", views_admins.break_glass, name="platform-break-glass"),
    # Legal pages — public
    path("legal/terms/", views.legal_terms, name="legal-terms"),
    path("legal/privacy/", views.legal_privacy, name="legal-privacy"),
    path("legal/pdpa-request/", views.legal_pdpa_request, name="legal-pdpa-request"),
]
