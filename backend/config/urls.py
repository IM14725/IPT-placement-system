from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core.views import django_admin_redirect
from apps.core.views_governance import submit_report

urlpatterns = [
    path("admin/", django_admin_redirect, name="django-admin"),
    path("report/", submit_report, name="report-submit"),
    path("", include("apps.accounts.urls")),
    path("student/", include("apps.students.urls")),
    path("organization/", include("apps.companies.urls")),
    path("platform/", include("apps.core.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("api/locations/", include("apps.locations.urls")),
    path("api/slots/", include("apps.slots.urls")),
    path("api/payments/", include("apps.payments.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)