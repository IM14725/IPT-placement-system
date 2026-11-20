from django.urls import path

from .views_verify import verify_letter

urlpatterns = [
    path("<uuid:letter_id>/<str:signature>/", verify_letter, name="letter-verify"),
]
