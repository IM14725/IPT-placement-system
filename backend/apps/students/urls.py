from django.urls import path

from apps.students import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="student-dashboard"),
    path("profile/", views.profile, name="student-profile"),
    path("documents/", views.documents, name="student-documents"),
    path("marketplace/", views.marketplace, name="student-marketplace"),
    path("applications/", views.applications, name="student-applications"),
    path("apply/<int:slot_id>/", views.apply, name="student-apply"),
    path("payments/<int:payment_id>/", views.payment, name="student-payment"),
    path("documents/<int:doc_id>/view/", views.view_document, name="document-view"),
]