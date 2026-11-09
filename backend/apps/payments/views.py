import json
import logging
from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.applications.models import Application
from apps.core.audit import client_ip
from apps.core.rate_limit import TokenBucketThrottle

from .models import Payment, PaymentGateway, PaymentStatus
from .serializers import InitiatePaymentSerializer
from .utils import (
    get_application_fee,
    send_selcom_wallet_pull,
    verify_selcom_webhook_signature,
)

logger = logging.getLogger("security")


class PaymentInitiateThrottle(TokenBucketThrottle):
    scope = "payment-initiate"
    capacity = 5
    refill_per_second = 1 / 60
    fail_closed = True


class InitiateSTKPushView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [PaymentInitiateThrottle]

    def post(self, request):
        serializer = InitiatePaymentSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            data = serializer.validated_data

            application = Application.objects.get(id=data["application_id"])
            amount = get_application_fee()
            phone_number = data["phone_number"]

            # Step 1: Record or update pending payment in our database
            payment, created = Payment.objects.get_or_create(
                application=application,
                defaults={
                    "student": application.student,
                    "amount": amount,
                    "currency": "TZS",
                    "method": data["network"],
                    "gateway": PaymentGateway.SELCOM,
                    "status": PaymentStatus.PENDING,
                    "phone_number": phone_number,
                },
            )

            if not created:
                payment.phone_number = phone_number
                payment.method = data["network"]
                payment.status = PaymentStatus.PENDING
                payment.gateway = PaymentGateway.SELCOM
                payment.save(
                    update_fields=["phone_number", "method", "status", "gateway", "updated_at"]
                )

            # Step 2: Trigger request straight to Selcom Gateway
            http_code, selcom_response = send_selcom_wallet_pull(
                payment.reference_id, phone_number, payment.amount
            )

            if http_code == 200 and selcom_response.get("result") == "SUCCESS":
                payment.gateway_txn_id = selcom_response.get("transid", "")
                payment.save(update_fields=["gateway_txn_id", "updated_at"])

                return Response(
                    {
                        "message": "USSD PIN prompt triggered on client handset.",
                        "reference_id": payment.reference_id,
                        "transaction_id": payment.reference_id,
                    },
                    status=status.HTTP_200_OK,
                )

            # Mark failed internally if gateway rejects push instantly
            payment.status = PaymentStatus.FAILED
            payment.save(update_fields=["status", "updated_at"])

            # Also update application and free up slot
            app = payment.application
            from apps.applications.models import ApplicationStatus

            app.status = ApplicationStatus.UNPAID
            app.save(update_fields=["status", "updated_at"])
            app.slot.refresh_status()

            return Response(
                {
                    "error": "Selcom gateway could not initiate push.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PaymentWebhookView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        # 1. Grab raw incoming request body before it goes through serialization
        raw_body = request._request.body

        # 2. Extract verification headers sent by Selcom's servers
        incoming_digest = request.headers.get("Digest") or request.headers.get(
            "X-Selcom-Signature"
        )
        incoming_timestamp = request.headers.get("Timestamp") or request.headers.get(
            "X-Selcom-Timestamp"
        )

        # 3. Execute signature & timestamp freshness validation check
        if not verify_selcom_webhook_signature(raw_body, incoming_digest, incoming_timestamp):
            logger.warning(
                "Invalid or expired signature received on Selcom webhook endpoint from ip=%s",
                client_ip(request),
            )
            return Response(
                {"result": "FAIL", "message": "Unauthorized signature mismatch"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # 4. Process safely now that authentication is verified
        try:
            data = json.loads(raw_body.decode("utf-8"))
            order_id = data.get("order_id")
            result_code = data.get("resultcode")

            if not order_id:
                return Response(
                    {"result": "FAIL", "message": "order_id is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            payment = get_object_or_404(Payment, reference_id=order_id)

            # Idempotency safety: Prevent reprocessing if already updated
            if payment.status in [PaymentStatus.PAID, PaymentStatus.FAILED]:
                return Response(
                    {"result": "SUCCESS", "message": "Already processed"},
                    status=status.HTTP_200_OK,
                )

            if result_code == "000":
                # Verify amount if present in webhook payload
                if "amount" in data and data["amount"] not in (None, ""):
                    try:
                        paid_amount = Decimal(str(data["amount"]))
                    except (InvalidOperation, ValueError):
                        return Response(
                            {"result": "FAIL", "message": "Invalid amount"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    if paid_amount != Decimal(str(payment.amount)):
                        logger.warning(
                            "Selcom webhook amount mismatch for order_id=%s: expected=%s got=%s",
                            order_id,
                            payment.amount,
                            paid_amount,
                        )
                        return Response(
                            {"result": "FAIL", "message": "Amount mismatch"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                # Verify currency if present in webhook payload
                if "currency" in data and data["currency"] not in (None, ""):
                    if str(data["currency"]).upper() != str(payment.currency).upper():
                        return Response(
                            {"result": "FAIL", "message": "Currency mismatch"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                # Prevent transaction ID replay across distinct orders
                transid = str(data.get("transid") or "").strip()
                if transid and Payment.objects.filter(gateway_txn_id=transid).exclude(
                    pk=payment.pk
                ).exists():
                    logger.warning(
                        "Selcom webhook duplicate transid=%s on order_id=%s",
                        transid,
                        order_id,
                    )
                    return Response(
                        {"result": "FAIL", "message": "Duplicate transaction ID"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                payment.mark_paid(gateway_txn_id=transid, payload=data)

                app = payment.application
                from apps.applications.models import ApplicationStatus

                app.status = ApplicationStatus.PAID
                app.save(update_fields=["status", "updated_at"])

                try:
                    from apps.payments.services import build_payment_receipt

                    build_payment_receipt(payment)
                except Exception:  # noqa: BLE001
                    pass
            else:
                payment.status = PaymentStatus.FAILED
                payment.save(update_fields=["status", "updated_at"])

                # Update application and free up slot
                app = payment.application
                from apps.applications.models import ApplicationStatus

                app.status = ApplicationStatus.UNPAID
                app.save(update_fields=["status", "updated_at"])
                app.slot.refresh_status()

            return Response(
                {"result": "SUCCESS", "message": "Acknowledged"}, status=status.HTTP_200_OK
            )

        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            return Response(
                {"result": "FAIL", "message": "Malformed body data"},
                status=status.HTTP_400_BAD_REQUEST,
            )


class PaymentStatusCheckView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, transaction_id):
        txn = get_object_or_404(Payment, reference_id=transaction_id, student__user=request.user)
        return Response(
            {"transaction_id": txn.reference_id, "status": txn.status},
            status=status.HTTP_200_OK,
        )
