import asyncio
import os
import time

os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.dev"
import django

django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
import httpx
import websockets

from django.test import Client
from apps.accounts.models import User
from apps.notifications.models import Message, MessageChannel, Notification, NotificationType
from apps.payments.models import Payment
from apps.slots.models import Slot

STUDENT_EMAIL = "student2@x.tz"
COMPANY_EMAIL = "company2@x.tz"
REALTIME = "http://127.0.0.1:8001"
WS = "ws://127.0.0.1:8001"

student_user = User.objects.get(email=STUDENT_EMAIL)
company_user = User.objects.get(email=COMPANY_EMAIL)


def _setup():
    from apps.companies.models import CompanyProfile

    company = CompanyProfile.objects.get(user=company_user)
    first = Slot.objects.order_by("id").first()
    slot = Slot.objects.create(
        company=company,
        title=f"E2E Slot {int(time.time())}",
        description="Automated Phase 3 smoke test",
        industry="Software",
        role_type="Intern",
        district_id=first.district_id,
        capacity=1,
        stipend_available=True,
        stipend_amount="100000.00",
        skills_required=["Python", "Git"],
    )
    client = Client()
    client.force_login(student_user)
    letter = SimpleUploadedFile(
        "intro_letter.pdf", b"%PDF-1.4 fake pdf content", content_type="application/pdf"
    )
    r = client.post(
        f"/student/apply/{slot.id}/",
        {"application_letter": letter},
        follow=False,
    )
    assert r.status_code == 302 and "/student/payments/" in r.get("Location"), r.status_code
    payment_id = int(r.get("Location").split("/")[-2])
    payment = Payment.objects.get(id=payment_id)
    token = client.get("/api/auth/my-token/").json()["token"]
    return client, slot, payment, token


async def ws_listener(token, user_id, out):
    async with websockets.connect(f"{WS}/ws/notifications/{user_id}?token={token}") as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=6)
        print("ws first:", first)
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=12)
            print("ws notification:", msg)
            out["msg"] = msg
        except asyncio.TimeoutError:
            print("ws: no push")


async def main():
    client, slot, payment, token = await asyncio.to_thread(_setup)
    ref = payment.reference_id
    app_id = payment.application_id
    print(f"application created: id={app_id}, payment={ref}, amount={payment.amount}")

    out = {}
    listener = asyncio.create_task(ws_listener(token, student_user.id, out))

    async with httpx.AsyncClient() as h:
        init = (
            await h.post(
                f"{REALTIME}/api/v1/payments/mock/initiate",
                json={"reference_id": ref, "amount": str(payment.amount), "method": "M_PESA"},
            )
        ).json()
        print("initiate ok, sig len:", len(init["signature"]))
        await asyncio.sleep(0.3)
        cb = (
            await h.post(
                f"{REALTIME}/api/v1/payments/mock/callback",
                headers={"X-Gateway-Signature": init["signature"]},
                json={
                    "reference_id": ref,
                    "status": "PAID",
                    "gateway_txn_id": init["gateway_txn_id"],
                    "amount": str(payment.amount),
                },
            )
        ).json()
        print("callback:", cb)

    await asyncio.sleep(6)  # let Celery finish
    await listener

    def _verify():
        from apps.applications.models import Application, ApplicationStatus

        payment.refresh_from_db()
        app = Application.objects.get(id=app_id)
        print("payment:", payment.status, payment.is_paid)
        print("application:", app.status)
        assert payment.is_paid and payment.status == "PAID"
        assert app.status == ApplicationStatus.PAID
        n = Notification.objects.filter(user=student_user, type=NotificationType.APPLICATION).order_by("-id").first()
        print("notification:", bool(n), n.title if n else None)
        sms = Message.objects.filter(user=student_user, application=app, channel=MessageChannel.SMS).order_by("-id").first()
        print("submitted SMS:", bool(sms), (sms.body[:70] if sms else ""))
        assert sms is not None
        slot.refresh_from_db()
        print("slot:", slot.status, "available:", slot.available_count)
        assert slot.status == "FULL" and slot.available_count == 0

        admin = User.objects.get(email="admin@ipt.local")
        c2 = Client()
        c2.force_login(admin)
        ledger = c2.get("/platform/ledger/").content.decode()
        assert ref in ledger, "ledger missing new payment"
        print("ledger ok")

        c3 = Client()
        c3.force_login(student_user)
        apps_page = c3.get("/student/applications/").content.decode()
        assert "Paid" in apps_page
        pay_page = c3.get(f"/student/payments/{payment.id}/").content.decode()
        assert "Pay Now" in pay_page
        mkt = c3.get("/student/marketplace/").content.decode()
        assert "slot-results" in mkt
        print("student pages ok")

    await asyncio.to_thread(_verify)
    print("E2E PASSED")


if __name__ == "__main__":
    asyncio.run(main())