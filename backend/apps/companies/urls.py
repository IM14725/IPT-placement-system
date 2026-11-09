from django.urls import path

from apps.companies import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="organization-dashboard"),
    path("profile/", views.profile, name="organization-profile"),
    path("profile/members/add/", views.add_member, name="organization-add-member"),
    path("profile/members/<uuid:member_id>/remove/", views.remove_member, name="organization-remove-member"),
    path("profile/upload/", views.upload_document, name="organization-upload-doc"),
    path("profile/upload/<uuid:doc_id>/delete/", views.delete_document, name="organization-delete-doc"),
    path("slots/", views.slot_list, name="organization-slots"),
    path("slots/new/", views.slot_create, name="organization-slot-create"),
    path("slots/<uuid:slot_id>/edit/", views.slot_edit, name="organization-slot-edit"),
    path("slots/<uuid:slot_id>/toggle/", views.slot_toggle, name="organization-slot-toggle"),
    path("slots/<uuid:slot_id>/delete/", views.slot_delete, name="organization-slot-delete"),
    path("slots/<uuid:slot_id>/applicants/", views.applicants, name="organization-applicants"),
    path("applicants/<uuid:app_id>/documents/", views.applicant_documents, name="organization-applicant-documents"),
    path("applicants/<uuid:app_id>/letter/", views.view_application_letter, name="organization-applicant-letter"),
    path("applicants/<uuid:app_id>/accept-sms/", views.send_acceptance_sms, name="organization-acceptance-sms"),
]