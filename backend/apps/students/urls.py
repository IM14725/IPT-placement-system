from django.urls import path

from apps.students import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="student-dashboard"),
    path("profile/", views.profile, name="student-profile"),
    path("documents/", views.documents, name="student-documents"),
    path("marketplace/", views.marketplace, name="student-marketplace"),
    path("applications/", views.applications, name="student-applications"),
    path("slots/<uuid:slot_id>/", views.slot_detail, name="student-slot-detail"),
    path("apply/<uuid:slot_id>/", views.apply, name="student-apply"),
    path("applications/<uuid:app_id>/release/", views.release_seat, name="student-release-seat"),
    path("applications/<uuid:app_id>/cancel/", views.cancel_application, name="student-cancel-application"),
    path("applications/<uuid:app_id>/letter/", views.view_application_letter, name="student-view-application-letter"),
    path("payments/<uuid:payment_id>/", views.payment, name="student-payment"),
    path("documents/<uuid:doc_id>/view/", views.view_document, name="document-view"),
    path("recommended/", views.recommended, name="student-recommended"),
    path("discover/", views.discover, name="student-discover"),
    path("saved/", views.saved_slots, name="student-saved-slots"),
    path("slots/<uuid:slot_id>/save/", views.toggle_saved_slot, name="student-toggle-saved-slot"),
    path("alerts/", views.alerts, name="student-alerts"),
    path("applications/<uuid:app_id>/acceptance-letter/", views.acceptance_letter, name="student-acceptance-letter"),
    path("applications/<uuid:app_id>/next-steps/", views.checklist, name="student-checklist"),
    path("applications/<uuid:app_id>/review/", views.review, name="student-review"),
    path("organizations/<uuid:org_id>/", views.organization_public_profile, name="organization-public-profile"),
]