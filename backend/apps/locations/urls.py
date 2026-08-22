from django.urls import path

from apps.locations import views

urlpatterns = [
    path("regions/", views.regions, name="api-locations-regions"),
    path("districts/", views.districts, name="api-locations-districts"),
    path("wards/", views.wards, name="api-locations-wards"),
]