# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

IPT Matching Marketplace: a two-sided marketplace connecting Tanzanian university students with companies for Industrial Practical Training. Students pay an application fee via mobile money before an application becomes visible to a company. `plan.md` is the long, authoritative design/build log (decisions, invariants, per-feature history); `frontend-pages.md` describes the UI pages. Read the relevant section of `plan.md` before changing payment, capacity, immutability, caching or rate-limit behaviour.

## Architecture

Monorepo with two Python services sharing one PostgreSQL DB and one Redis:

- `backend/` — Django 6.1 + DRF monolith (server-rendered templates, Tailwind via CDN, vanilla JS). Settings are split in `config/settings/{base,dev,prod}.py`; `DJANGO_ENV` (default `dev`) selects which. Custom user model `accounts.User` (email login, role STUDENT/COMPANY/ADMIN). Apps in `backend/apps/`: `core` (celery app, Redis client, cache, rate limiting, audit/integrity ledger, admin console), `accounts`, `students`, `companies`, `documents`, `locations`, `slots`, `applications`, `payments`, `notifications`.
- `realtime/` — FastAPI app (port 8001): WebSocket push (`/ws/notifications/{user_id}`), payment webhook receiver (HMAC + idempotency) and the mock payment gateway. It reads/writes the **same DB tables** as Django (asyncpg/SQLAlchemy, no Django ORM), so logic like payment finalization exists in both `backend/apps/payments/services.py` and `realtime/app/webhooks/router.py`; `realtime/app/core/{rate_limit,immutability,cache}.py` mirror their Django counterparts. Change both sides together. WebSocket auth uses single-use 60s tickets issued by Django (`apps/core/ws_tickets.py`, Redis key `ipt:wsticket:*`) and redeemed with GETDEL in `realtime/app/core/auth.py`; there are no permanent API tokens.
- Celery (broker Redis db 1, results db 2) runs email/SMS/PDF/notification fan-out; Redis db 0 is cache + pub/sub channel `ipt:notify` (Celery → FastAPI → browser WebSocket). Beat schedule lives in `CELERY_BEAT_SCHEDULE` in `base.py` (e.g. `applications.expire_unpaid` releases unpaid seats after the 3h deadline).
- External providers (payments, SMS, email) sit behind interfaces (`apps/core/providers.py`); mock adapters are the default.

### Invariants enforced in the database, not just code

Postgres triggers (created in migrations) enforce: an application cannot become `PAID` without a verified payment; slot capacity (PENDING + PAID count) cannot be exceeded; and audit/payment/application/verified-document rows are write-protected (immutability layer, `apps/core/immutability.py`, plus a hash-chained `IntegrityRecord` ledger sealed from `apps/core/signals.py`). Seat booking must go through the service layer (`apps/applications/services.py`) which uses `select_for_update` on the slot. Don't bypass these with raw updates; migrations `0002/0003` named `*_immutable` / `*_append_only` define them.

### Redis-backed infrastructure (`apps/core/`)

`redis_client.py` (distributed locks), `cache.py` (singleflight hot-key cache, versioned slot-search keys, `enqueue_once` job dedupe), `rate_limit.py` (Lua token bucket, decorator-based). All fail open when Redis is down. Toggle with env `CACHE_ENABLED` / `RATE_LIMIT_ENABLED`.

## Commands

Run Django commands from `backend/` using the venv at `backend/.venv` (Python 3.14). Dependencies: `pip install -r requirements-dev.txt` (backend) and `realtime/requirements.txt`.

```powershell
# Everything (Django :8000, FastAPI :8001, Celery worker+beat, Redis if on PATH)
scripts\dev.ps1                 # flags: -SkipDjango -SkipFastAPI -SkipCelery -SkipBeat -SkipRedis
scripts\create_db.ps1           # create Postgres role `ipt` + DB `ipt_marketplace`

# Individually (from backend/)
python manage.py migrate
python manage.py seed_locations ; python manage.py seed_wards
python manage.py runserver 127.0.0.1:8000
celery -A config worker --loglevel=info --pool=solo    # --pool=solo on Windows
celery -A config beat --loglevel=info
# from realtime/
uvicorn main:app --port 8001 --reload

# Docker alternative: docker compose up   (postgres, redis, web, realtime, celery_worker, celery_beat)
```

Tests (from `backend/`; pytest-django, `pytest.ini` sets the settings module):

```powershell
pytest                                            # all
pytest apps/payments/tests.py                     # one file
pytest apps/core/test_locks.py::test_name -x      # one test
```

`backend/conftest.py` disables cache/rate limiting, runs Celery eagerly, and swaps `get_redis` for in-process `fakeredis` — tests never touch a real Redis, but they do need the Postgres DB (triggers are Postgres-specific). Utility scripts: `backend/e2e_phase3.py` (live end-to-end against running services), `backend/simulate_webhook.py`.

### Identity & access (Feature 2)

Sessions are 10 minutes, one per account, enforced by `apps/accounts/sessions.py` (`SessionPolicyMiddleware`); `apps/accounts/gates.py` blocks users who must change a temporary password or (admins) finish MFA. Use `revoke_user_session(user, reason)` after role changes, removal or suspension. Audit with `apps.core.audit.audit_event` (organization, actor role, target, outcome, context). High-risk admin POSTs use `@hardware_reverify`. OTPs live in `accounts/otp.py` (never queue a code or password on Celery). Admin MFA/recovery/break-glass: `accounts/mfa.py`, `recovery.py`, `breakglass.py`. Tests run with `ADMIN_MFA_ENFORCED=False` and blank SMS keys (`conftest.py`). Details: `plan.md`, section "Feature 2".

## Gotchas

- The Windows dev environment is the primary target (`scripts/*.ps1`); `Dockerfile`/`docker-compose.yml` are a secondary path.
- Long-running Django/uvicorn processes do not always reload; `plan.md` records bugs that were just a stale server — restart before assuming a code bug.
- Uploads: <2 MB, extension allowlist and magic-byte check via `apps/documents/validators.py` (`validate_upload`); documents are served only through permission-checked views, and stored SHA-256 is re-verified on view (tamper → 410).
- `.claudeignore` excludes migrations (`**/migrations/[0-9]*.py`) from Claude's view; read them explicitly when working on triggers or schema.
- `plan.md` must not contain real credentials (the Postgres superuser password was removed; rotate it). Keep secrets in `backend/.env` (gitignored).
