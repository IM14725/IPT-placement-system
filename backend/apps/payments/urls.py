from django.urls import path
from .views import InitiateSTKPushView, PaymentWebhookView, PaymentStatusCheckView

urlpatterns = [
    path('initiate/', InitiateSTKPushView.as_view(), name='initiate-stk'),
    path('webhook/', PaymentWebhookView.as_view(), name='gateway-webhook'),
    path('status/<str:transaction_id>/', PaymentStatusCheckView.as_view(), name='payment-status'),
]
