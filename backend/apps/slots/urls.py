from django.urls import path

from apps.slots import views

urlpatterns = [
    path("search/", views.SlotSearchView.as_view(), name="slots-search"),
]