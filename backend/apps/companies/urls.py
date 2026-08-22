from django.urls import path

from apps.companies import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="company-dashboard"),
    path("profile/", views.profile, name="company-profile"),
    path("profile/upload/", views.upload_document, name="company-upload-doc"),
    path("slots/", views.slot_list, name="company-slots"),
    path("slots/new/", views.slot_create, name="company-slot-create"),
    path("slots/<int:slot_id>/edit/", views.slot_edit, name="company-slot-edit"),
    path("slots/<int:slot_id>/toggle/", views.slot_toggle, name="company-slot-toggle"),
    path("slots/<int:slot_id>/delete/", views.slot_delete, name="company-slot-delete"),
    path("slots/<int:slot_id>/applicants/", views.applicants, name="company-applicants"),
    path("applicants/<int:app_id>/documents/", views.applicant_documents, name="company-applicant-documents"),
    path("applicants/<int:app_id>/letter/", views.view_application_letter, name="company-applicant-letter"),
    path("applicants/<int:app_id>/accept-sms/", views.send_acceptance_sms, name="company-acceptance-sms"),
]