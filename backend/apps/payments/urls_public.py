from django.urls import path

from .views_verify import verify_receipt

urlpatterns = [
    path("<str:reference>/<str:signature>/", verify_receipt, name="receipt-verify"),
]
