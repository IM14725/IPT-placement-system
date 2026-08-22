from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken import views as drf_views

from apps.core.views import django_admin_redirect

urlpatterns = [
    path("admin/", django_admin_redirect, name="django-admin"),
    path("", include("apps.accounts.urls")),
    path("student/", include("apps.students.urls")),
    path("company/", include("apps.companies.urls")),
    path("platform/", include("apps.core.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("api/locations/", include("apps.locations.urls")),
    path("api/auth/token/", drf_views.obtain_auth_token, name="api_token"),
    path("api/slots/", include("apps.slots.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)