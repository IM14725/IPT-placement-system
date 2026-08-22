import json
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.cache import bump_slot_version
from app.core.celery_client import enqueue
from app.core.db import SessionLocal
from app.core.rate_limit import enforce as enforce_rate_limit
from app.core.redis import acquire_lock, publish_notification, release_lock
from app.core.security import canonical, verify

router = APIRouter()

PAYMENT_LOCK_PREFIX = "ipt:lock:pay:"


class CallbackBody(BaseModel):
    reference_id: str
    status: str = Field(pattern="^(PAID|FAILED)$")
    gateway_txn_id: str = ""
    amount: str = ""


class MockInitiateBody(BaseModel):
    reference_id: str
    amount: str
    method: str = "M_PESA"


async def _process_payment(reference_id, status, gateway_txn_id, amount_str):
    from app.core.security import sign

    lock_key = f"{PAYMENT_LOCK_PREFIX}{reference_id}"
    acquired = await acquire_lock(lock_key, ttl_ms=30000)
    if not acquired:
        return {
            "status": "ok",
            "reference_id": reference_id,
            "duplicate": True,
            "busy": True,
        }
    try:
        return await _process_payment_locked(
            reference_id, status, gateway_txn_id, amount_str
        )
    finally:
        await release_lock(lock_key)


async def _process_payment_locked(reference_id, status, gateway_txn_id, amount_str):
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        async with session.begin():
            pay = (
                await session.execute(
                    text(
                        "SELECT p.id, p.application_id, p.student_id, p.status, "
                        "p.gateway_txn_id, p.reference_id, p.amount::text, "
                        "p.currency, p.method, p.gateway, p.is_paid, p.paid_at, "
                        "s.user_id AS student_user_id "
                        "FROM payments_payment p "
                        "JOIN students_studentprofile s ON s.id = p.student_id "
                        "WHERE p.reference_id = :r FOR UPDATE"
                    ),
                    {"r": reference_id},
                )
            ).first()
            if pay is None:
                raise HTTPException(status_code=404, detail="Payment not found")
            if pay.status == "PAID":
                return {"status": "ok", "duplicate": True}

            app_row = (
                await session.execute(
                    text(
                        "SELECT a.id AS application_id, a.slot_id, s.title AS slot_title, "
                        "a.student_id, a.status AS app_status, a.is_accepted, "
                        "a.company_message, a.student_message, a.payment_deadline, "
                        "a.letter_original_name, a.letter_sha256, "
                        "c.name AS company_name "
                        "FROM applications_application a "
                        "JOIN slots_slot s ON s.id = a.slot_id "
                        "JOIN companies_companyprofile c ON c.id = s.company_id "
                        "WHERE a.id = :aid"
                    ),
                    {"aid": pay.application_id},
                )
            ).first()

            if status != "PAID":
                await session.execute(
                    text(
                        "UPDATE payments_payment SET status='FAILED', "
                        "callback_payload=:p, updated_at=:n WHERE id=:i"
                    ),
                    {
                        "p": json.dumps({"status": "FAILED"}),
                        "n": now,
                        "i": pay.id,
                    },
                )
                return {"status": "failed", "reference_id": reference_id}

            await session.execute(
                text(
                    "UPDATE payments_payment SET status='PAID', is_paid=TRUE, "
                    "gateway_txn_id=:t, paid_at=:n, callback_payload=:p, "
                    "updated_at=:n WHERE id=:i"
                ),
                {
                    "t": gateway_txn_id,
                    "n": now,
                    "p": json.dumps({"status": "PAID", "gateway_txn_id": gateway_txn_id}),
                    "i": pay.id,
                },
            )

            # PENDING -> PAID (no auto-accept; the company confirms after
            # reviewing the student's application letter).
            # DB triggers enforce paid-visibility and slot capacity atomically.
            await session.execute(
                text(
                    "UPDATE applications_application SET status='PAID', "
                    "updated_at=:n WHERE id=:a AND status <> 'PAID'"
                ),
                {"n": now, "a": pay.application_id},
            )

            # Seal the finalized records into the immutable integrity ledger.
            from app.core.immutability import seal

            await seal(
                session,
                record_type="PAYMENT",
                record_id=pay.id,
                fields={
                    "reference_id": pay.reference_id,
                    "student_id": pay.student_id,
                    "application_id": pay.application_id,
                    "amount": pay.amount,
                    "currency": pay.currency,
                    "method": pay.method,
                    "gateway": pay.gateway,
                    "gateway_txn_id": gateway_txn_id,
                    "status": "PAID",
                    "is_paid": True,
                    "paid_at": now,
                },
            )
            await seal(
                session,
                record_type="APPLICATION",
                record_id=pay.application_id,
                fields={
                    "student_id": app_row.student_id,
                    "slot_id": app_row.slot_id,
                    "status": "PAID",
                    "is_accepted": app_row.is_accepted,
                    "company_message": app_row.company_message or "",
                    "student_message": app_row.student_message or "",
                    "payment_deadline": app_row.payment_deadline,
                    "letter_original_name": app_row.letter_original_name or "",
                    "letter_sha256": app_row.letter_sha256 or "",
                },
            )

            student_body = (
                f"Your application for '{app_row.slot_title}' at {app_row.company_name} "
                f"has been submitted. Payment verified ({reference_id}). The company "
                f"will review your application letter."
            )
            student_notif = (
                await session.execute(
                    text(
                        "INSERT INTO notifications_notification "
                        "(user_id, actor_id, type, title, body, link, is_read, "
                        "created_at, updated_at) "
                        "VALUES (:uid, NULL, 'APPLICATION', 'Application Submitted', "
                        ":body, '', FALSE, :n, :n) RETURNING id"
                    ),
                    {"uid": pay.student_user_id, "body": student_body, "n": now},
                )
            ).scalar()

            company_user = (
                await session.execute(
                    text(
                        "SELECT c.user_id FROM companies_companyprofile c "
                        "WHERE c.id = (SELECT company_id FROM slots_slot WHERE id = :sid)"
                    ),
                    {"sid": app_row.slot_id},
                )
            ).scalar()

            company_notif = None
            if company_user:
                company_body = (
                    f"New paid applicant (#{pay.application_id}) for '{app_row.slot_title}'."
                )
                company_notif = (
                    await session.execute(
                        text(
                            "INSERT INTO notifications_notification "
                            "(user_id, actor_id, type, title, body, link, is_read, "
                            "created_at, updated_at) "
                            "VALUES (:uid, NULL, 'APPLICATION', 'New Applicant', "
                            ":body, '', FALSE, :n, :n) RETURNING id"
                        ),
                        {"uid": company_user, "body": company_body, "n": now},
                    )
                ).scalar()

    # After commit: enqueue background work and publish live notifications.
    await publish_notification(
        {
            "user_id": pay.student_user_id,
            "notification_id": student_notif,
            "type": "APPLICATION",
            "title": "Application Submitted",
            "body": student_body,
        }
    )
    if company_user and company_notif:
        await publish_notification(
            {
                "user_id": company_user,
                "notification_id": company_notif,
                "type": "APPLICATION",
                "title": "New Applicant",
                "body": f"New paid applicant (#{pay.application_id}) for '{app_row.slot_title}'.",
            }
        )

    enqueue("documents.receipt_pdf", pay.id)
    enqueue("payments.receipt_email", pay.id)
    enqueue("applications.submitted_sms", pay.application_id)
    enqueue("notifications.fanout", [student_notif])
    if company_notif:
        enqueue("notifications.fanout", [company_notif])
    enqueue("slots.refresh_status", app_row.slot_id)

    # Slot bookings changed -> invalidate the cached slot listings (Django keys
    # its hot-key cache by this generation).
    await bump_slot_version()

    return {
        "status": "ok",
        "reference_id": reference_id,
        "application_id": pay.application_id,
        "slot_id": app_row.slot_id,
    }


@router.post("/api/v1/payments/mock/initiate")
async def mock_initiate(body: MockInitiateBody, request: Request):
    """Dev-only: stand in for a real mobile-money gateway request."""
    from app.core.security import sign

    await enforce_rate_limit(request, "mock-initiate", capacity=30, refill_per_second=1.0)

    async with SessionLocal() as session:
        pay = (
            await session.execute(
                text(
                    "SELECT id, amount::text FROM payments_payment "
                    "WHERE reference_id = :r"
                ),
                {"r": body.reference_id},
            )
        ).first()
    if pay is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    gateway_txn_id = f"MOCK-{body.reference_id}"
    payload_str = canonical(body.reference_id, "PAID", gateway_txn_id, body.amount)
    return {
        "gateway": "MOCK",
        "gateway_txn_id": gateway_txn_id,
        "mock_callback_url": "/api/v1/payments/mock/callback",
        "signature": sign(payload_str),
    }


@router.post("/api/v1/payments/mock/callback")
async def mock_callback(
    body: CallbackBody,
    request: Request,
    x_gateway_signature: str | None = Header(default=None, alias="X-Gateway-Signature"),
):
    await enforce_rate_limit(request, "mock-callback", capacity=60, refill_per_second=10.0)
    return await _process_payment(
        body.reference_id, body.status, body.gateway_txn_id, body.amount
    )


@router.post("/api/v1/payments/callback")
async def payment_callback(
    body: CallbackBody,
    request: Request,
    x_gateway_signature: str | None = Header(default=None, alias="X-Gateway-Signature"),
):
    await enforce_rate_limit(request, "payment-callback", capacity=30, refill_per_second=5.0)
    payload_str = canonical(
        body.reference_id, body.status, body.gateway_txn_id, body.amount
    )
    if not verify(payload_str, x_gateway_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        return await _process_payment(
            body.reference_id, body.status, body.gateway_txn_id, body.amount
        )
    except Exception as exc:  # noqa: BLE001
        if "full capacity" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Slot is at full capacity")
        raise