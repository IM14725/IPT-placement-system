# IPT Matching Marketplace — Plan

An independent, two-sided marketplace connecting Tanzanian university students with **organizations** (companies and institutions) for **Industrial Practical Training (IPT)**. Runs autonomously (no university integrations): students register on their own and universities are only an optional affiliation record. Students pay an application fee via Mobile Money before an application is unlocked and submitted to an organization.

> **Terminology (read this first).** The code, URLs and UI now say **Organization**, not Company. Older dated entries in section 12 predate the rename and still say `CompanyProfile` / `company-*` / `/company/`; the mapping is in the "Rename glossary" at the top of section 12. The `apps/companies/` package, the `companies` app label and `Application.company_message` kept their old names on purpose.

---

## 1. Core Decisions (agreed)

| Area | Decision |
|---|---|
| Backend / Admin | Django 6.x (6.1) + Django REST Framework on **Python 3.14** |
| Async / Realtime | FastAPI (WebSockets + payment webhook receiver) sharing the same PostgreSQL DB |
| Workers | **Celery + Redis** for SMS/Email/PDF/notifications/slot-metrics (reduces DB hits, prevents request timeouts) |
| Database | PostgreSQL 17 (native Windows service) — `ipt_marketplace` DB, role `ipt` |
| Redis | Redis 7 via Docker Desktop (WSL2); broker + pub/sub channel `ipt:notify` |
| Frontend | Server-rendered Django templates + Tailwind CSS + Vanilla JS (Fetch API + native WebSockets) |
| Payments | Mock gateway simulator first (M-Pesa / Tigo Pesa / Airtel Money); real Selcom/Pesapal behind the same interface later |
| SMS / Email | Mock console adapters first behind provider interface (Beem/NextSMS, SendGrid later) |
| Locations | Bundled curated dataset — 31 regions + all districts of Tanzania, seeded from `data/tanzania_locations.json`, served via cascading AJAX endpoints |

## 2. Repository Layout (monorepo)

```
IPT-system/
├── plan.md
├── data/tanzania_locations.json      # 31 regions + districts seed
├── scripts/
│   ├── create_db.ps1                 # create role + DB
│   └── dev.ps1                       # start all services (Django, FastAPI, Celery, Redis)
├── backend/                          # Django monolith
│   ├── manage.py
│   ├── config/                       # settings (base/dev/prod), urls, asgi/wsgi
│   ├── apps/
│   │   ├── core/                     # celery app, redis/cache/rate-limit, audit + integrity ledger, admin console,
│   │   │                             #   views_governance.py (universities, suspend/reactivate, reports)
│   │   ├── accounts/                 # User + roles (STUDENT/ORGANIZATION/ADMIN/SUPER_ADMIN), AdminRole matrix
│   │   ├── students/                 # StudentProfile, University records + suggestions, verification
│   │   ├── companies/                # Organization (+ members, sessions, RBAC) - package name kept, see terminology note
│   │   ├── documents/                # document vault (<2MB validation)
│   │   ├── locations/                # Region / District reference data
│   │   ├── slots/                    # IPT slot CRUD + capacity
│   │   ├── applications/             # application lifecycle
│   │   ├── payments/                 # payments, receipts, ledger
│   │   └── notifications/            # typed notifications + messages
│   ├── templates/  static/
│   ├── data/                         # generated data
│   └── requirements{,-dev}.txt
└── realtime/                         # FastAPI async core
    ├── main.py
    └── app/{ws,webhooks,core}
```

## 3. Security & Authorization Protocol

- **Students**: upload scanned Student ID + official Semester Results Matrix → account stays `PENDING_VERIFICATION` until Platform Admin approves.
- **Organizations**: upload BRELA certificate, TIN certificate, Business License → cannot post slots or view applicants until profile is `APPROVED`.
- **Platform roles**: `ADMIN` (capability matrix from `AdminRole`: modules `user_management`, `slot_verification`, `financial_ledger`, `system_metrics` x `view/create/edit/delete`) and `SUPER_ADMIN` (everything; roles/settings/templates pages are super-admin only). Console gate is `core.views._require_admin`.
- **Organization RBAC** (`apps/companies/permissions.py`, enforced by `@org_permission_required(...)` on every organization view, never only in templates): `OWNER` ⊃ `MANAGER` ⊃ `RECRUITER`. Recruiter: view org, view/manage slots, view applicants. Manager adds edit profile, manage documents, accept applicants. Owner adds manage members. `UNIVERSITY_COORDINATOR` / `UNIVERSITY_SUPERVISOR` exist but are read-only placeholders until university workspaces are built. All queries are scoped by `slot__organization=` / `organization=` so one organization can never read another's applicants or documents.
- **Multi-account organizations**: an organization has up to **5 active members** (`OrganizationMember`, `MAX_ORGANIZATION_ACCOUNTS`) and up to **5 concurrent logins** (`OrganizationSession`, `MAX_ORGANIZATION_CONCURRENT_LOGINS`; tracked from `user_logged_in/out` signals in `companies/apps.py`; a 6th login is refused until a session ends or expires). Owners/managers add and remove staff from the organization profile page.
- **Suspension**: platform admins can suspend/reactivate a student or a whole organization (sets `is_active=False` + `suspended_at`); suspended organizations are hidden from search and take no new applications. Every admin action is written to the hash-chained audit log.
- DRF permission classes: `IsStudent / IsOrganization / IsPlatformAdmin / IsPlatformSuperAdmin / IsVerifiedStudent / IsApprovedOrganization / IsOrganizationOwner`.
- Cross-service auth: **no permanent API tokens** (removed 2026-11-09). The browser fetches a single-use, 60 s WebSocket ticket from `/api/auth/ws-ticket/` (Redis key `ipt:wsticket:*`, `apps/core/ws_tickets.py`); FastAPI redeems it with an atomic `GETDEL` (`realtime/app/core/auth.py`). DRF uses `SessionAuthentication` only. The `authtoken_token` table is now unused.
- **Identity & access management (Feature 2)**: OTP registration/recovery, 5-minute login lockout, 10-minute one-device sessions, verified email/phone changes, administrator accounts + MFA (TOTP / FIDO2), M-of-N recovery, break-glass accounts, structured org audit trail, soft member removal. Full detail in the section **"Feature 2 — Identity & Access Management (IAM) — DONE"** below.
- Webhook security: HMAC-SHA256 signature + idempotency by `gateway_txn_id` + replay rejection.
- Upload validation: **< 2 MB** for all documents and images, magic-byte MIME check, extension allowlist, SHA-256 checksum.
- Documents served only through permission-checked views (never bare MEDIA_URL).

## 4. Database Schema (PostgreSQL)

- **accounts_user** — `role` (STUDENT/ORGANIZATION/ADMIN/SUPER_ADMIN), `admin_role` FK (capability matrix), `email` unique (USERNAME_FIELD), `phone`, `email_verified`, `phone_verified`, `suspended_at` + `suspension_reason`, `deletion_requested_at`, `promo_sms_optout`. Organization users have no first/last name (`full_name` falls back to the organization name).
- **accounts_adminrole** — name, permissions (module x capability JSON), is_system.
- **students_university** — name (unique, case-insensitive), abbreviation, institution_type (UNIVERSITY/COLLEGE/INSTITUTE), region FK, website, `verification_status` (APPROVED = selectable by everyone; PENDING = a student's "not listed" suggestion; REJECTED), `suggested_by` FK. Seeded (131) from `data/tanzania_institutions.json` by migration `students.0005` and `manage.py seed_universities`.
- **students_studentprofile** — `university` FK (nullable, optional), `graduation_year`, `student_id` (registration number), course, `academic_year`, `education_level` (TCU 4-10), gpa, skills (JSON), gender, photos, region/district/ward FKs, `reapplication_credit`, `verification_status` (PENDING/APPROVED/REJECTED), rejection_reason.
- **companies_organization** (model `Organization`) — optional one-to-one `user` (the creating owner), name (unique), `organization_type` (COMPANY/UNIVERSITY/INSTITUTION; only COMPANY can be registered today), industry, description, email, phone, address/street, region/district/ward FKs, `verification_status`, rejection_reason, `suspended_at` + `suspension_reason`.
- **companies_organizationmember** — organization FK, user FK, `role` (OWNER/MANAGER/RECRUITER/UNIVERSITY_COORDINATOR/UNIVERSITY_SUPERVISOR), `membership_status` (ACTIVE/INVITED/SUSPENDED), `UNIQUE(organization, user)`, max 5 ACTIVE per organization.
- **companies_organizationsession** — organization FK, user FK, `session_key` (unique); drives the 5-concurrent-logins cap.
- **core_report** — reporter FK, optional organization FK, category (FRAUD/MISLEADING/PAYMENT/ABUSE/OTHER), subject, description, status (OPEN/IN_REVIEW/RESOLVED/DISMISSED), admin_notes, handled_by/handled_at.
- **documents_document** — owner, `doc_type` (STUDENT_ID, RESULTS_MATRIX, CV, INTRO_LETTER, BRELA_CERT, TIN_CERT, BUSINESS_LICENSE), file, mime_type, size, sha256.
- **locations_region / locations_district** — seeded reference data (31 regions, ~184 districts).
- **slots_slot** — `organization` FK (APPROVED, not suspended), title, industry, role_type, district FK, street, department/course, `academic_year`, `education_level`, **`application_deadline` (optional)**, capacity, stipend, skills_required (JSON), status `OPEN | FULL | PAUSED | CLOSED`, derived `available_count`, `deadline_passed`.
- **payments_payment** — reference_id (unique), student FK, application FK, amount (TZS), method (M_PESA/TIGO_PESA/AIRTEL_MONEY), gateway (MOCK/SELCOM/PESAPAL), gateway_txn_id, callback_payload (JSON), status (PENDING/PAID/FAILED), `is_paid`, paid_at, receipt_pdf.
- **applications_application** — student FK, slot FK, payment FK (nullable 1:1), status `PENDING → PAID → UNPAID` (payment-status model), `payment_deadline` (created + 3h), `is_accepted` (bool, company acceptance/SMS flag), `UNIQUE(student, slot)`.

### Enforced invariants
1. **Paid visibility** — company-facing queryset filters `status='PAID'`; a PostgreSQL `BEFORE UPDATE` trigger rejects any transition to `PAID` without a verified payment.
2. **Capacity lock** — service layer uses `select_for_update` on the slot; `Slot.booked_count` counts `PENDING + PAID` (a pending application reserves a seat). A trigger rejects inserts/updates that exceed `capacity` (counts PENDING + PAID).
3. **Unpaid expiry** — Celery beat `applications.expire_unpaid` (every 10 min) marks PENDING applications past `payment_deadline` as `UNPAID`, fails their pending payments, and releases the seat; the student can re-book to restart.

## 5. Business Flow (revised — no interview/decline)

1. **Apply Now** → lock slot → reject if full → create `Application` `PENDING` (invisible to company; reserves a seat) with a 3-hour `payment_deadline`.
2. Payment overlay (mock M-Pesa/Tigo/Airtel) → gateway callback → FastAPI validates HMAC + idempotency → `Payment PAID` → application `PAID` → Celery: receipt email, submitted SMS, notification fan-out, slot capacity recompute. Company acceptance (optional) sets `is_accepted`.
3. Company sees **all PAID applicants** on the slot (in-browser doc viewer) and can send the **acceptance SMS**.
4. Slot shows `FULL` when capacity reached (pending + paid); further applies blocked. Unpaid pending applications expire after 3h → `UNPAID` → seat released.

## 6. Two Notification Types

- **Type A — Application**: (1) **Email** receipt (PDF, reference ID, amount, company, slot); (2) **SMS** "application sent" with Application ID, Company Name, Slot selected.
- **Type B — Acceptance**: company-triggered **SMS** → "check your email for the acceptance letter / further instructions".

## 7. Student Filters (live reduction)

Marketplace fetches `/api/slots/search/?region=&district=&department=&academic_year=&education_level=&organization=` on every filter change (cascading region→district loads from the locations API; `department` is the academic-discipline search and `organization` is a name search). Results are cached in Redis under a versioned key that includes every parameter. Each card shows capacity, stipend and the application deadline; once the deadline passes it reads "Applications closed" and applying is refused server-side.

## 8. Celery + Redis Task Inventory

| Task | Queue | Trigger |
|---|---|---|
| `payments.receipt_email` (+PDF) | email | webhook verified |
| `applications.submitted_sms` | sms | webhook verified |
| `applications.acceptance_sms` | sms | company action |
| `documents.receipt_pdf` | documents | webhook verified |
| `notifications.fanout` (Redis pub → WS) | notify | any state change |
| `slots.refresh_status` (FULL/AVAILABLE) | slots | post-accept + periodic |
| `ledger.aggregate` (admin metrics) | metrics | scheduled/on-demand |

## 9. FastAPI Async Layer

- `GET /ws/notifications/{user_id}?ticket=` — persistent WebSocket (`user_id` is a UUID string); authenticated with a single-use ticket; the Redis pub/sub subscriber pushes notifications and `session_revoked` events (the socket is then closed with 4401).
- `POST /api/v1/payments/callback` — async: HMAC validate + idempotency → update DB → enqueue Celery tasks.
- `POST /api/v1/payments/mock/initiate` + `/mock/callback` — mock gateway simulator (dev-only).

## 10. Milestones

- **Phase 1 — DB & Auth**: scaffold, venv, settings split, DB creation, models, migrations, locations seed, admin verification queues, RBAC, upload validation, tests.
- **Phase 2 — Celery/Redis/FastAPI/Payments**: Redis provisioning, Celery app + tasks, Slot/Application/Payment/Notification models + triggers, FastAPI (WS + webhook + mock gateway), receipt PDF, integration tests.
- **Phase 3 — Frontend & UX**: Tailwind base + all panels, marketplace filters, payment overlay, simplified timeline, FULL/AVAILABLE badges, WS toast feed, doc viewer, acceptance-SMS dispatcher, ledger + metrics dashboards, end-to-end smoke test.
- **Phase 4 — Hardening (optional)**: real Selcom/Pesapal, Beem/SendGrid, S3 storage, gunicorn/uvicorn + nginx, Tailwind CLI build.
- **Phase 5 — Admin Console — DONE**: built the 7 admin pages from the design guide (verification queues, notification templates, audit logs, role/permissions, settings); admin workflows are now available off Django-Admin.

## 11. Prerequisites / Credentials

- PostgreSQL superuser: `postgres` / `<superuser password: keep it in your own secrets store, not in this file>`.
- App DB role: `ipt` / `ipt_dev_password` (dev).
- Redis: Docker Desktop + `redis:7-alpine` on `localhost:6379`.

## 12. Build Status (updated)

**Rename glossary** (older entries below use the old names):

| Old name (entries before 2026-11-07) | Current name |
|---|---|
| `CompanyProfile`, table `companies_companyprofile` | `Organization`, table `companies_organization` |
| `Slot.company` / column `company_id` | `Slot.organization` / `organization_id` |
| `UserRole.COMPANY` (stored `COMPANY`) | `UserRole.ORGANIZATION` (stored `ORGANIZATION`) |
| `User.is_company`, `User.company_profile` | `User.is_organization`, `User.owned_organization` |
| URL names `company-*`, paths `/company/...` | `organization-*`, `/organization/...` |
| `templates/company/`, `core/company_verification.html` | `templates/organization/`, `core/organization_verification.html` |
| `IsCompany`, `IsApprovedCompany`, `IsCompanyOwner` | `IsOrganization`, `IsApprovedOrganization`, `IsOrganizationOwner` |
| *(kept)* `apps.companies`, `Application.company_message`, `OrganizationType.COMPANY` | unchanged on purpose |

### Phase 1 — Database & Auth — DONE
- Monorepo scaffold; venv `backend/.venv` (Python 3.14, Django 6.1, DRF 3.18, psycopg3, celery 5.6, fastapi 0.141).
- Settings split (`config/settings/{base,dev,prod}.py`), env-driven, PostgreSQL pooling (`psycopg_pool`).
- Models: `User` (role/email-auth), `StudentProfile`, `CompanyProfile`, `Document` (<2MB + magic-byte validation), `Region`/`District`.
- DB `ipt_marketplace` + role `ipt` created; migrations applied; **31 regions / 188 districts seeded**.
- Admin verification queues (students/companies) with approve/reject(reason) actions requiring the mandated documents.
- DRF role permissions (`IsStudent/IsCompany/IsVerifiedStudent/IsApprovedCompany/IsCompanyOwner`).
- Superuser: `admin@ipt.local` / `AdminPass!2026`.
- Tests: RBAC, PENDING gating, upload size/MIME rejection — **passing**.

### Phase 2 — Celery/Redis, FastAPI, Payments — DONE
- **Redis 7** running via Docker (`ipt-redis` on `:6379`; broker `:6379/1`, result `:6379/2`, pub/sub `:6379/0` channel `ipt:notify`).
- **Celery** app (`-A config`) + task inventory: `documents.receipt_pdf`, `payments.receipt_email`, `applications.submitted_sms`, `applications.acceptance_sms`, `notifications.fanout`, `slots.refresh_status`, `ledger.aggregate`.
- **FastAPI** (`realtime/`, port 8001): WS `/ws/notifications/{user_id}` (DRF-token auth + Redis subscriber push), webhook `/api/v1/payments/callback` (HMAC + idempotency), mock gateway `/api/v1/payments/mock/initiate` + `/mock/callback`.
- Models: `Slot`, `Application`, `Payment`, `Notification`, `Message` + **paid-visibility trigger** and **capacity trigger** (fixed in `0003`).
- Receipt PDF generation (reportlab) attached to Payment.
- E2E verified live: mock initiate→callback → payment PAID, application **auto-ACCEPTED**, receipt email + submitted SMS SENT, student & company notifications, WS live push received, bad-signature → 401, replay → duplicate.
- Tests: 17 passing (paid visibility, capacity overflow rejection, auto-accept, upload rules, RBAC).

### Phase 3 — Frontend & UX — DONE
- Tailwind CDN base template with role-aware nav, Django-messages banners, JS toast container, WS client (`static/js/app.js`, `static/js/ws.js`), CSRF/current-user injected globals.
- **Accounts**: home, register, login, logout, my-token; `registration/*` templates.
- **Student panel** (all 6 templates + JS): dashboard, profile (region→district cascade via `location-cascade.js` + `/api/locations/districts/`), documents vault, marketplace (live filters via `/api/slots/search/` + `/api/locations/`), applications (simplified Paid→Accepted timeline), payment overlay (`payments.js` hits FastAPI mock gateway), document viewer.
- **Company panel**: profile + corporate-doc vault, verification gate before slot CRUD, slot list/create/edit/pause/delete, applicant matrix (paid only), applicant document viewer, acceptance-SMS dispatcher (queues `applications.acceptance_sms`).
- **Admin platform pages**: `platform/ledger/` (paid transactions + total fees) and `platform/metrics/` (aggregate counters) under `core/`.
- Fixed realtime webhook bug: notification/WS-push `user_id` now resolves to the actual `User` (joined `students_studentprofile.user_id`) instead of the `StudentProfile` id.
- Verified: `manage.py check` clean; all panels smoke-tested 200; full live E2E (`backend/e2e_phase3.py`) — apply → mock pay → PAID → auto-ACCEPTED → submitted SMS + receipt email → WS push received on the correct user → slot FULL → ledger reflects payment.
- **Official design restyle**: all existing templates restyled to the design guide (`stitch_ipt_marketplace_frontend_guide`) — Material 3 tokens + Inter + Material Symbols in `base.html` tailwind config; home, auth, all 6 student panels, all 6 company panels (incl. applicant matrix + doc viewer), ledger + metrics + 403. Slots/applicants views extended with aggregate context (`total_capacity`, `fill_percent`, `accepted_count`, etc.).
- Tests: 17 passing.

### Phase 5 — Admin Console — DONE
- **New models**: `AuditLog` + `PlatformSetting` (core), `NotificationTemplate` (notifications), `AdminRole` (accounts) + migration `core.0003_seed_admin_console` seeding 5 default templates, 10 platform settings, 4 system roles.
- **7 styled pages** (mirror `stitch_ipt_marketplace_frontend_guide` admin screens) under `/platform/`:
  - `verifications/` — queue dashboard (stat cards + Students/Companies tabs + live search) → `platform-verifications`.
  - `verifications/students/<pk>/` — profile overview, doc vault, approve/reject with reason (missing-doc gate) → `platform-student-verification`.
  - `verifications/companies/<pk>/` — company profile, doc vault, approve/reject → `platform-company-verification`.
  - `templates/` — Email/SMS template list + editor with variable insert + live preview → `platform-templates`.
  - `audit-logs/` — immutable admin-activity log with search + module filter → `platform-audit-logs`.
  - `roles/` — role list + capability matrix (View/Create/Edit/Delete per module) + special-privilege toggles → `platform-roles`.
  - `settings/` — fee, verification requirements, capacity limits, SMS gateway, maintenance mode → `platform-settings`.
- **Template wiring**: `applications.submitted_sms`, `applications.acceptance_sms`, `payments.receipt_email` now render through `notifications.services.render_template` (editable templates with `{var}` placeholders, safe fallback to legacy text). Seeded bodies match the previous output — E2E SMS/email flow unchanged.
- **Audit trail**: every approve/reject, template save, role change, and settings save writes an `AuditLog` (actor, action, module, description, IP).
- **Auth**: all pages gated by the existing admin check (`is_staff or is_platform_admin`); non-admins get the styled 403 page. Admin nav in `base.html` extended (Queue/Ledger/Metrics/Templates/Audit/Roles/Settings).
- Verified: `manage.py check` clean; all 7 admin pages 200; approve/reject/settings/role POSTs work; missing-doc gate blocks approval; 17 pytest passing; live E2E PASSED.

### Status model rework — PENDING/PAID/UNPAID — DONE
- Application status refactored to the payment-status model: `PENDING → PAID → UNPAID`; `is_accepted` is now a separate boolean preserved for the company acceptance/SMS flow; added `payment_deadline` (created + 3h).
- `Slot.booked_count` = PENDING + PAID (a pending application reserves a seat); `UNPAID` releases the seat; re-booking restarts a PENDING reservation with a fresh deadline.
- Migrations `0004_status_rework_pending_paid_unpaid` applied (drops old triggers → data-maps legacy statuses → recreates triggers); live data migrated correctly.
- Celery beat `applications.expire_unpaid` every 600s; verified end-to-end: apply → PENDING (slot FULL) → deadline passes → UNPAID + payment FAILED + slot OPEN → re-book restarts.
- Student & company profile pages restyled to the design guide; account-status card removed (account always shows **Active**); status badges Pending/Paid/Unpaid across student dashboard, applications, company applicants.
- 18 pytest passing; live E2E PASSED (apply → mock pay → PAID → slot FULL → ledger).

### UX/Admin round — role-based register, logout, Verified, Directory, slot form — DONE
- **Register page role-aware**: company role shows **Company name / Company email / Company phone** (no first/last name); student role shows First/Last name. JS toggles the name fields on role selection; `RegisterForm.company_name` required when `role=COMPANY` (saved into `user.first_name`); company profile prefills the name from registration.
- **Logout fixed**: `LOGIN_URL = "/auth/login/"` (was defaulting to missing `/accounts/login/`, so logged-out users hitting protected pages got a 404 redirect loop); `user_logout` no longer requires login and redirects home for anonymous visitors.
- **Company shows Verified**: company dashboard + profile now display **Verified / Pending Verification / Rejected** instead of the static "Active" label.
- **Admin Directory** (`platform/directory/` → `platform-directory`): lists all students and companies with filters for region, district, university, course, year of study, and verification status; region→district cascade via `location-cascade.js`; nav link "Directory" added.
- **Company slot form restyled**: inputs now use the `.ipt-field-box` component style (icons, error/desc text); stipend checkbox rendered as a proper toggle card; `stipend_amount` required when stipend is enabled (hidden otherwise via JS).
- Verified: 18 pytest passing; live smoke tests — company register w/ company-name (302 → dashboard, name prefilled), student register still requires first/last, logout(anon) → home, directory tabs/filters 200, slot create valid → 302, stipend-missing-amount shows inline error.

### Directory region filter + marketplace "show all" — DONE
- **Admin directory region filter**: `location-cascade.js` gained a `cascadeMode='filter'` mode — region placeholder "All regions", district placeholder "All districts" (stays enabled when no region selected so the filter can be cleared); directory page sets the mode and passes the selected region/district. Verified: region/district filters reduce the list correctly.
- **Clear button**: restyled as an outlined button (`restart_alt` icon) matching the Filter button height/placement, instead of a bare text link.
- **Marketplace shows all slots**: fixed `SlotSerializer.get_available_count` which referenced a non-existent `accepted_count` (threw `AttributeError` → `/api/slots/search/` 500, so the marketplace never listed anything). Now uses the model `booked_count`/`available_count`; API returns ALL non-CLOSED slots from approved companies (including FULL ones, rendered as "Slot Full"), and region/district/department/level filters narrow the list. District resets to "All districts" when region is cleared.

### Logout + company registration — root-caused to STALE SERVER (FIXED)
- Both had appeared fixed before but "still failed" because an old Django process (started pre-fix) was still bound to :8000 and serving old code/templates.
- Killed both runserver PIDs (6856, 14876) and started one fresh process. Verified live end-to-end:
  - Company register POST (role=COMPANY + company_name) → 302 → /company/dashboard/ renders 200, nav shows company name + Logout.
  - Logout GET → 302 → /, session cookie cleared; /company/dashboard/ after logout → 302 → /auth/login/?next=...
- Bonus bug fixed in register.html: role-toggle JS compared `value === 'company'` (lowercase) against the real `COMPANY`, so the Company-name field never appeared; now `=== 'COMPANY'`.

### Application letter upload + company confirmation — DONE
- **Student must attach a university application letter before submitting.** New `ApplicationLetterForm` (`apps/applications/forms.py`) reuses `validate_upload` (<2MB, extension allowlist, magic-byte check). `apply` view is now GET/POST: GET renders `templates/student/apply.html` (slot summary + letter upload), POST validates the letter then `create_application` + payment creation → redirect to payment. Marketplace "Apply Now" is now a link to `/student/apply/<slot_id>/`; slot-full still redirects to marketplace with an error banner.
- **Letter stored on the application**: `Application.application_letter` FileField (`application_letters/{student_id}/`) + `letter_original_name` (migration `0005`). Student applications page shows "Application letter attached".
- **No more auto-accept on payment.** Both Django `finalize_verified_payment` and FastAPI `_process_payment` transition PENDING→PAID only; `is_accepted` stays False until the company confirms. Student notification now reads "The company will review your application letter." Company sees the paid applicant with a **View Application Letter** link (`company-applicant-letter`, `FileResponse as_attachment`, original filename).
- **Company confirmation = acceptance**: the applicants page button is now **Confirm & Accept** (`accept-sms`) — calls `accept_application(app, company_message=...)` (persists `company_message` via a second save) then queues `applications.acceptance_sms`.
- **Tests**: `test_finalize_verified_payment_marks_paid` now asserts `not is_accepted`; added `test_accept_requires_paid` and `test_accept_application_marks_accepted`. **20 pytest passing.**
- Verified live end-to-end: apply with letter → 302 payment → realtime mock callback → PAID & `is_accepted=False` → company downloads letter (attachment, correct filename) → Confirm & Accept → `is_accepted=True` + `company_message` persisted.

### `/dashboard/` 404 after logout (FIXED)
- A stale/cached `/dashboard/` URL (from an earlier redirect target) 404'd after logout. Added `dashboard_redirect` view + `/dashboard/` route in `apps/accounts` that 302s to the role dashboard: anonymous → `/auth/login/`, company → `/company/dashboard/`, student → `/student/dashboard/`.

### Company confirmation message + inline document preview — DONE
- **One-click confirmation**: the applicant action is now a **Send Confirmation** button (with an optional acceptance note) that in a single click confirms the application and **automatically sends the student both an SMS and an email** telling them to open their email for the acceptance letter. New Celery task `applications.acceptance_email` (renders the seeded `acceptance_email` template, fallback body included); `send_acceptance_sms` queues SMS + email together. Migration `core.0004_seed_acceptance_email` seeds the editable template. Verified live: confirm → app accepted → SMS "Congratulations! … Check your email for the acceptance letter" SENT + EMAIL "Congratulations! You have been accepted — …" SENT.
- **Document preview fixed**: `view_application_letter` served files with `as_attachment=True` + `application/octet-stream`, so clicking "View Application Letter" downloaded the file instead of previewing. It now serves **inline** with the correct content type from the filename extension (`application/pdf`, `image/png`, etc.) so companies can review the letter in the browser (matching the existing `document-view` behavior). Test `test_company_letter_preview_served_inline` asserts `Content-Type` + `Content-Disposition: inline` + streamed bytes.
- **Tests**: 22 pytest passing (added `test_acceptance_email_queues_confirmation` + `test_company_letter_preview_served_inline`).

### Media/file preview before upload — DONE
- **Global media preview**: every upload input (all share the class `ipt-file-input`) now shows a confirm card **before** the form can be submitted — thumbnail for images, PDF/doc icon otherwise, name + size, "Ready to submit" badge, and a remove button. Implemented as a single auto-initializing script `static/js/media-preview.js` (+ `.ipt-media-preview` styles and the global script include in `base.html`), so it applies to all four upload surfaces: student apply letter, student documents vault, student profile, company profile.
- Verified: all four pages render input + preview card; script served (HTTP 200, `node --check` clean); the 14 documents/applications tests still pass.

### Enterprise immutability layer — DONE
- **Hash-chained integrity ledger**: new `IntegrityRecord` model (`core.0005`) holds, per sealed state, `record_hash` = SHA-256 of canonical JSON of the record's immutable fields **plus** the previous row's hash (`prev_hash`) — a tamper-evident chain. `apps/core/immutability.py` implements `canonical/compute_hash/seal/verify_record/verify_chain/sha256_bytes`; datetimes are normalized to ISO-8601 strings and payloads sanitized (`json.dumps(default=_to_str)`) so **Django and FastAPI produce identical hashes**.
- **Auto-sealing**: `apps/core/signals.py` (wired in `core/apps.ready`) seals `AuditLog` (AUDIT), `Payment` (PAYMENT), `Application` (APPLICATION) and `Document` (DOCUMENT) on every post_save; `seal()` dedupes consecutive identical states. The **realtime webhook** (`_process_payment`) seals PAYMENT + APPLICATION inside its transaction using a mirrored `realtime/app/core/immutability.py`.
- **Database-level write-protection** (applied regardless of code path):
  - `core.0006` — `core_auditlog` is append-only (UPDATE/DELETE rejected).
  - `payments.0002` — once `is_paid`, financial fields (reference_id, student_id, application_id, amount, currency, method, gateway, gateway_txn_id, status, is_paid, paid_at) are immutable; auxiliary fields (`receipt_pdf`, `callback_payload`) remain writable; paid rows cannot be deleted.
  - `applications.0007` — once `is_accepted`, the application is immutable and undeletable; a PAID application cannot be deleted; the company's Confirm step was refactored to a single save so the trigger doesn't block it.
  - `documents.0002` — verified documents are immutable and undeletable.
- **Document tamper detection**: `Application.letter_sha256` (`applications.0006`) is computed at upload; `view_document`/`view_application_letter` re-hash the stored file and return **410 Gone** on mismatch.
- **Backfill + dashboard**: `core.0007` backfills the ledger for existing rows; `/platform/integrity/` (`platform-integrity`) verifies every ledger row against its stored payload, each record's latest row against current DB state, and the whole chain — rendering any tampered/missing/diverging record.
- **Tests**: 34 pytest passing (22 prior + 12 new in `apps/core/test_integrity.py` covering audit/payment/application/document immutability, tampered-document 410, ledger sealing, verify_record/verify_chain, apply letter hash).
- Verified live: admin integrity dashboard renders with **zero issues**; a live mock payment (app 13) sealed PAYMENT#13 + APPLICATION#13 PAID rows that are self-consistent, match current state, and leave the chain intact.

### Hot-key caching + validation expiry + token-bucket rate limiting — DONE
- **New settings** (`config/settings/base.py`): `CACHE_ENABLED`, `RATE_LIMIT_ENABLED` (default True), `CACHE_KEY_PREFIX="ipt"`, `CACHE_TTL_DEFAULT=60`, `SLOT_SEARCH_CACHE_TTL=20`, `REGIONS_CACHE_TTL=3600`, `VERIFICATION_CACHE_TTL=300`. `conftest.py` sets `CACHE_ENABLED=0` / `RATE_LIMIT_ENABLED=0` before settings import so the 34 existing tests stay Redis-free.
- **Hot-key cache** (`apps/core/cache.py`): `cache_get/cache_set/cache_get_or_set` with a **singleflight lock** (`{key}:lock`, SET NX PX 3s, waiters poll up to 2.5s then compute) so a 2000-student stampede on the same listing collapses to **one producer per TTL window**. JSON encoder normalizes Decimal→float, datetime→isoformat, else str (no type drift). Every read/write fails open when Redis is down.
- **Versioned slot search**: listings are keyed `ipt:slot:search:v{generation}:{sig}` (sig = sha1 of filters). Any booking change bumps the generation via `INCR ipt:slot:cache:version`, so stale listings are atomically orphaned (no delete sweep needed). Producers invalidate from **both** sides: Django signals (`apps/core/signals.py`: Slot/CompanyProfile/Application/Payment save→`bump_slot_version`, StudentProfile save→`invalidate_user_validation`, Region/District save→`invalidate_locations`) **and** the FastAPI webhook (`realtime/app/core/cache.py::bump_slot_version`, called at the end of `_process_payment` because it writes via raw SQL, bypassing Django signals).
- **Token-bucket rate limiter** (`apps/core/rate_limit.py` + mirrored `realtime/app/core/rate_limit.py`): Lua-atomic draw with **timer refill** (`tokens + elapsed*rate`, capped at capacity; idle buckets grow back to full), `consume(namespace, bucket, capacity, refill_per_second, cost, now=None)` with deterministic `now` for tests; buckets `ipt:rl:{scope}:{bucket}` with TTL = capacity/refill + 5s; fail-open on Redis errors. Exposed as the `@token_bucket(...)` view decorator (with `methods=("POST",)` + `deny_view` for a styled 429) and DRF `TokenBucketThrottle`.
- **Applied limits**: login 5/30s per email+ip **and** 30/30s per ip (denied → login form re-rendered, HTTP 429 + `Retry-After`); slot search 120 / refill 20/s per user; locations 120/20; student apply 3 per minute; company slot create/edit 10/30s; FastAPI mock initiate 30/s per ip, mock callback 60 / refill 10/s, real callback 30 / refill 5/s (429 + `Retry-After`).
- **Slots search optimized**: `SlotSerializer` now prefers precomputed `_booked`/`_available` attrs; the search producer computes counts with a **single `Count` aggregate** (was N per-slot COUNT subqueries); regions/districts/verification served from cache (`get_regions`/`get_districts`/`get_verification_status`).
- **Tests**: 42 pytest passing (34 + 8 new in `apps/core/test_cache.py` + `apps/core/test_rate_limit.py` — cached get_or_set + recompute, version bump, type serialization, bucket capacity/refill/per-bucket isolation/expiry reset; all use the `test-*` key prefix + auto-cleanup, never touching live cache).
- Verified live: search returns cached listings with correct `booked_count`/`available_count`; slot save bumps the generation (158→159); a real mock payment through the FastAPI webhook bumps it again (160→161) and the old versioned key is no longer served; login burst → 429 with `Retry-After: 18`; integrity ledger re-checked clean after verification data was restored.

### Next phase (deferred)
- Replace Django-Admin-only workflows with these styled pages. (The 7 admin guide pages are now implemented; remaining admin workflows — document verification flags, `LedgerSnapshot`/Celery `ledger.aggregate` scheduling — can be surfaced here later.)
- Offload still in progress: real SMS/email provider backends (Beem/SendGrid), gunicorn/uvicorn + nginx, Tailwind CLI build.

### Redis/Celery offload — heavy work, pagination & throttling — DONE
- **Distributed locks** (`apps/core/redis_client.py`): `acquire_lock(name, ttl_ms, blocking=False)` → `SET NX PX` returning an opaque token; `release_lock(name, token)` uses a compare-and-delete Lua script (only deletes if still owned); fail-open on Redis errors. Blocking mode polls at 20 ms until TTL expiry.
- **Enqueue dedupe** (`apps/core/cache.py`): `enqueue_once(task, task_args=None, *, ttl=60)` collapses a burst of identical Celery jobs into one (dedupe key `ipt:jobs:{task}:{sha1}`, `SET NX ex=ttl`), falls back to always-enqueue when Redis is down.
- **Register concurrency**: per-IP (`register-ip`, 10/30s) + per-email (`register-email`, 3/60s) token-bucket decorators on the register POST (`methods=("POST",)`); a per-email Redis mutex (`register:{email}`, 5 s, blocking) serializes the `User.objects.create` race so duplicate-registration IntegrityError stays at zero.
- **Async deep file validation** (`apps/documents/`): new `scan_status` (PENDING/CLEAN/ERROR) + `scan_error` fields; migration `0003` applied. `documents.scan` Celery task re-hashes the stored file, re-checks magic bytes via `filetype`, and validates PNG/JPEG/GIF dimensions (`_validate_image`); upload views (`apps/students/views.py::documents`, `apps/companies/views.py::upload_document`) now `enqueue_once("documents.scan", [doc.id])`. Restored `documents.receipt_pdf` task consumed by `applications.services.finalize_verified_payment` and the FastAPI webhook.
- **List pagination** (`apps/core/pagination.py` + `templates/includes/pagination.html`): `paginate(qs, page, page_size=DEFAULT_PAGE_SIZE=20)` (hard-capped). Wired into: slot search API (10/page, envelope `{count, page, page_size, has_more, results}` + `static/js/marketplace.js` "Load more"); student applications; company applicants; admin ledger (50/page, cached totals `admin:ledger-totals`); audit logs (50/page); directory (25/page). Pagination partial kept to the project's design-system button classes.
- **Cached admin metrics**: `platform/metrics` results wrapped in `cache_get_or_set("admin:metrics", 60s, _compute_metrics)`, cutting ~11 COUNT queries per admin page load.
- **Payment double-process guard** (`realtime/app/webhooks/router.py`): `_process_payment` takes a Redis mutex `ipt:lock:pay:{reference_id}` (SET NX PX 30s); a concurrent duplicate returns `{"status":"ok","duplicate":true,"busy":true}` before touching Postgres. The DB `FOR UPDATE` row lock remains as a second line of defense.
- **Outbound SMS/Email throttling** (`apps/notifications/services.py`): new `OutboundRateLimited` exception + `OUTBOUND_BUDGET` (SMS 20/5s, EMAIL 30/10s) and `throttled_dispatch(message)` that draws a token then delegates to `dispatch_message`. Tasks `payments.receipt_email`, `applications.submitted_sms`, `applications.acceptance_sms`, `applications.acceptance_email` now call `throttled_dispatch` and retry (exponential backoff, max 8, 10-min ceiling) on `OutboundRateLimited`.
- **Bug fixed**: `StudentProfile.is_verified` is a Python property, not a DB column — the marketplace verification producer used to (and the marketplace now filters `verification_status="APPROVED"`.
- **Tests**: 50 pytest passing (was 42). New: `apps/core/test_locks.py` (acquire/release/mutex/block + enqueue_once dedupe), `apps/notifications/test_outbound.py` (budget-exhaustion raises, dispatch-when-allowed), `apps/documents/test_scan.py` (valid PNG → CLEAN, bad-JPEG → ERROR). Caught a regression in this round: rewriting `documents/tasks.py` dropped the `receipt_pdf` task — caught by the existing integrity suite and restored.
- **Verified live** (services restarted, all on 2026-08-16):
  - Slot search `/api/slots/search/?page=1` → `{count:13, page:1, page_size:10, has_more:true, results:[10]}`; page 5 → `has_more:false`;
  - Admin `platform/ledger/`, `audit-logs/`, `directory/`, `metrics` all 200 with pagination partial wired; `ipt:admin:metrics` cache key set;
  - Register burst: first 3 POSTs pass the email-bucket, rest return HTTP 429 with `Retry-After` (~30 s); IP bucket unaffected — no stray users created;
  - Mock payment (payment 18, ref `IPT-AF0667C83C`): mock/callback → 200 `{"status":"ok","reference_id":...,"application_id":17}`; concurrent callback → `"duplicate":true`; payment ended PAID, application PAID, receipt PDF attached, receipt-EMAIL and submitted-SMS `SENT`; Redis lock `ipt:lock:pay:*` released (gone) so it doesn't block later runs; new tasks registered with the live worker (`documents.scan`, `documents.receipt_pdf`, `applications.submitted_sms`, `payments.receipt_email`). Immutability trigger correctly rejected `DELETE` of the PAID payment during cleanup; ledger remains clean (15 PAID, 0 PENDING).

### Metrics trend charts — DONE
- **Daily activity counters** (`apps/core/cache.py`): `incr_daily(prefix)` → atomic `INCR ipt:stats:{prefix}:{YYYY-MM-DD}` with TTL 45 days; `daily_series(prefix, days=30)` reads the last 30 keys zero-filled. Fail-open (no-op when Redis down / `CACHE_ENABLED=0` in tests).
- **Slot-search tracking**: `SlotSearchView.get` calls `incr_daily("searches")` on every hit (after the existing cache/throttle setup). Historical search counts don't exist, so the series fills forward from 2026-08-17.
- **Login trend**: derived from the existing `User.last_login` column (Django updates it on every successful `login()`) — real history without a new table: distinct users whose most recent login fell on each day.
- **Revenue trend**: `_compute_metrics` aggregates `Payment` (`status=PAID`, `paid_at`) per day with `Sum(amount)`, zero-filled across the last 30 days. All three series are emitted from `platform/metrics` (still cached 60s under `admin:metrics`).
- **Charts** (`templates/core/metrics.html`): Chart.js 4.4.1 via jsDelivr CDN (matches the project's CDN approach); revenue = bar chart, logins + searches = line charts with `Chart.defaults` matching the design-system palette; series passed to the page via Django's `json_script` filter (`revenue-data`/`login-data`/`search-data`); safe no-op if the CDN is unreachable.
- **Tests**: 50 pytest passing (unchanged; `_compute_metrics` runs under `CACHE_ENABLED=0` where `daily_series` returns `[]`).
- **Verified live** (restarted Django :8000, uvicorn :8001, celery worker+beat; 2026-08-17): `/platform/metrics/` renders 3 `<canvas>` elements + chart.js + all three JSON series (revenue non-zero on 08-15/16/17 = 95k/135k/15k TZS; logins 3→5 on 08-16/17; searches 0 then 6 after live student search). Live search counter `ipt:stats:searches:2026-08-17` incremented 5→6.

### Official locations + institutions pickers — DONE
- **Wards (kata)** on company AND student profiles — official Tanzania wards from the OpenAdminData API (compiled from NBS admin divisions): 3,624 unique ward names attached to 168 of our 189 districts (99.5% coverage; unmatched = Chunya, Mafinga — not in our districts). Source file `data/tanzania_wards.json` (3,621 seeded).
- **Data + models**: new `Ward(name, district FK)` in `apps/locations` (`0002_ward`), `ward` FK added to `CompanyProfile` + `StudentProfile` (SET_NULL), registered in admin with a per-district inline. District matching normalizes names/aliases (Urban↔City/Municipal, "Mji"↔Town, "Township Authority"↔Town).
- **API + cache**: `get_wards(district_id)` cache helper (3600s TTL) + `/api/locations/wards/?district=X` endpoint (token-bucket limited like regions/districts).
- **Seed**: `manage.py seed_wards` reads `settings.WARDS_DATA_FILE` → idempotent `get_or_create`.
- **Cascade**: `location-cascade.js` now 3-level (region→district→ward), driven by `window.IPT.profileRegion/District/Ward`. Fully tolerant: if the page has no `id_ward` select (admin directory, company slot form) it stays a 2-level cascade — verified live.
- **Institutions** on the student profile: official TCU list of approved university institutions (universities + university colleges, 2025/26 register) in `data/tanzania_institutions.json` (~54 entries with name + abbreviation + aliases). Served via `get_institutions()` (cached) → `window.IPT.institutions`. New `institution-picker.js` turns the `university` text field into a searchable combobox (live filter over name/abbreviation/aliases, arrow keys + Enter, mouse selection). Field stays a CharField so existing data and admin filters are unaffected.
- **Forms**: `CompanyProfileForm` + `StudentProfileForm` gain `ward` (`id_ward`), querysets cascade from region→district on POST; templates render it with the `my_location` icon; `street` remains free text (no official national street list exists).
- **Tests**: 50 pytest passing (unchanged).
- **Verified live** (2026-08-17, Django restarted :8000): `/api/locations/wards/?district=<Meru>` → 17 wards (Akheri, Kikatiti, ...); company profile + student profile both render `id_ward` + ward select; student profile loads `institution-picker.js` + `IPT.institutions` (54 entries); directory + slot form stay 2-level; full save round-trips persisted `profileWard=110` for both a company (region 1 / district 7 / ward 110, Nyerere Rd) and student (UDSM).

### Wikipedia wards + university filter upgrade — DONE
- **University filter becomes a searchable combobox**: the admin Directory's university filter (`#dir-university`) was a plain free-text input with no suggestion list — it now uses the official TCU institution list exactly like the student profile. `institution-picker.js` was generalized to init on **both** `#id_university` (student profile) and `#dir-university` (directory); the directory view now passes `institutions_json` (cached `get_institutions()`) into `window.IPT.institutions`. Because the field keeps `name="university"` + the existing `university__icontains` filter, partial/legacy values (e.g. "DIT", "UDSM") still match.
- **Full 3-level cascade on the Directory**: the directory filter gained a **Ward** select (`id_ward`, `name="ward"`), so region→district→ward now works on every cascade page (directory, student profile, company profile); the slot form stays 2-level (slots are district-granularity only). Directory view filters students AND companies by `ward_id`, `select_related` includes `ward`, the location column shows `region · district · ward`, `filters.ward` is echoed into `window.IPT.profileWard`, and the grid widened to 7 columns.
- **Wards refreshed from Wikipedia** (`Category:Wards of Tanzania`): crawled the full category tree (region → district subcategories → ward pages) + per-article categories + intro extracts via the MediaWiki API (batched, rate-limit backoff). Wikipedia's tree is intentionally incomplete (~1,178 articles), so this is a **merge, not a replace**: 1,003 unique article titles were compared against the NBS dataset — 439 confirmed existing, **22 genuinely-new wards added** with verified district mappings (e.g. Enguserosambu/Enduleni→Ngorongoro, Iloirienito→Longido, Kashashi/Naeny→Siha, Kilwa Kivinje/Kiranjeranje/Pande Mikoma→Kilwa, Kayenze/Kiseke/Mecco→Ilemela, Hombolo Bwawani/Makulu + Matumbulu→Dodoma City). Dubious candidates were rejected (company pages like "Unga Limited"/"Mecco", mislabeled articles like "Bagamoyo Ward"→Isongole, near-duplicate names already in DB like Usariver/Hananasifu/Iringamvumi, and the pre-existing Chunya/Mafinga gap). `data/tanzania_wards.json` now has **3,646 rows**; `seed_wards` added 22 → **3,643 wards across 158 districts**.
- **Tests**: 50 pytest passing (unchanged).
- **Verified live** (2026-08-17, Django restarted :8000): directory renders `id_ward` + combobox `#dir-university` + `institution-picker.js` + `IPT.institutions` (54); directory filtered `?region=1&district=1&ward=89&university=DIT` → 200 with ward+university echoed; companies tab 200; `/api/locations/wards/?district=61` (Kilwa) now returns the 3 new Wikipedia wards; student profile still renders picker + cascade 200; both servers healthy.

### University combobox + ward mapping fixes — DONE
- **Bug 1 — university field looked different & dropdown never opened**: `institution-picker.js` wraps the input in a `.ipt-combobox` div, which broke two things: (a) the `.ipt-field-box > input` CSS selector stopped matching once the input was no longer a direct child, so the wrapped field lost its padding/typography (looked unlike the other directory filters); (b) `.ipt-field-box { overflow: hidden }` clipped the absolutely-positioned dropdown, so no list ever appeared. Fix in `base.html` + `institution-picker.js`: added `.ipt-field-box > .ipt-combobox` + `.ipt-combobox > input` styles mirroring the normal input; the picker now adds `.ipt-has-combobox` to the field box, which sets `overflow: visible` (dropdown can escape) and rounds the left icon chip so the box still looks clean.
- **Bug 2 — ward cascade broken (districts empty / wards in wrong district)**: the original ward build collapsed every `"X Urban"`/`"X Mji"`/`"X Township Authority"` source district onto the base/rural district (e.g. "Moshi Urban"→Moshi instead of Moshi Municipal, "Dodoma Urban"→Dodoma City, "Tabora Urban"→Tabora), so 31 districts showed zero wards and urban wards sat inside rural districts. Rebuilt `data/tanzania_wards.json` from the authoritative source (`ward.json`, OpenAdminData) with a correct urban→Municipal/City/Town mapping table (27 districts corrected) + kept the 22 Wikipedia additions + Chunya/Mafinga still excluded (not in our DB). Added 13 more Wikipedia-confirmed wards for districts the source never covered (Ubungo 5, Kigamboni 3, Busokelo 5). Ward table cleared & reseeded → **3,659 wards across 171 districts**; zero-ward districts dropped **31 → 18** (remainder are genuinely post-2016 districts with no data in any source, plus the leftover `R/D` test district). Leftover test district `R/D` (id 189) kept (a paid slot references it; deletion is blocked by the payment immutability trigger).
- **Tests**: 50 pytest passing (unchanged). **Verified live** (2026-08-17, Django restarted :8000): `/api/locations/wards/?district=52` (Moshi Municipal) → 21 urban wards (Boma Mbuzi, Bondeni, ...); `?district=11` (Ubungo) → 5 (Goba, Kibamba, ...); `?district=12` (Kigamboni) → 3; `?district=83` (Busokelo) → 5; directory page 200 with new CSS; locations cache cleared after reseed.

### Complete ward lists (all split-off districts) + full HEI register — DONE
- **User report**: Ubungo showed only 5 wards (should be 14) and "Kimara is in both Ubungo and Kinondoni". Root cause confirmed: post-2016 split-off councils still had their wards nested inside the parent district (Kinondoni held all 14 Ubungo wards, Temeke held all 9 Kigamboni wards, etc.). Kinondoni never legitimately had a Kimara — the app's own Kinondoni copy was the bug.
- **Ward rebuild** (researched via Wikipedia district articles + Swahili navboxes `Kigezo:Kata za Wilaya ya …`, 2022 structure): moved every misplaced ward from its parent to the correct council and added all genuinely-missing wards. Moves: Kinondoni→Ubungo (14), Temeke→Kigamboni (9, incl. `Pemba Mnazi`→`Pembamnazi`), Bagamoyo→Chalinze (16), Lushoto→Bumbuli (16+2 missing), Manyoni→Itigi (11+2), Geita→Geita Town (7+6), Kilombero→Ifakara Town (9+10), Ulanga→Malinyi (8+2), Sengerema→Buchosa (13+9), Nzega→Nzega Town (6+4, incl. `Nzega Mjini`→`Nzega Mjini Magharibi`+`Mashariki`), Bariadi→Bariadi Town (10), Mbinga→Mbinga Town (10+9), Kahama→Ushetu (19+1) + Kahama→Msalala (16+2), Mpanda→Tanganyika (9+7), Mlele→Nsimbo (8+4, incl. `Urwira`→`Urwila`). Added missing rural wards to Kilombero (6); Dodoma City corrected to 41 (`Dom-Makulu`→`Dodoma Makulu`, dropped spurious `Hombolo`, +`Ihumwa`+`Nkuhungu`); Tabora Municipal 29 (+`Kidongochekundu`/`Mapambano`/`Mpela`/`Mwinyi`); Nyamagana 18, Ilemela 19, Songea Municipal `Mjini`→`Songea Mjini`, Kinondoni `Hananasifu`→`Hananasif`. Zero-ward districts dropped **18 → 0** (only the leftover `R/D` test district id 189, which a paid slot references, remains without wards). `data/tanzania_wards.json` reseeded → **3,733 wards across 183 districts**; 35 spot-checks all match expected counts.
- **HEI register expanded** 54 → **115 institutions**: kept all TCU universities/university colleges, added missing campus colleges (MUDARCo, MUMBCCo, MUST-Rukwa/Mtwara, SUA-Mizengo Pinda, ARU-Mwanza, MWECAU-Hedaru), centres (SAUT-DSM/Arusha, SMMUCo-Mwika), IIT Madras Zanzibar, and the major NACTE/NACTVET technical colleges & institutes (NIT, DMI, CBE, IRDP, LGTI, ISW, IAE, ITA, EASTC, WDMI, Institute of Lands, TPSC, CFR, IJA, Law School, IPS, TIRT, NCT, CAWM Mweka, Pasiansi, ESAMI, IPA, KIST, MATI Uyole, LITA, FTI/FITI, FETA, TTCIH, nursing schools, teachers colleges, private business colleges). Updated names: `Rabininsia Memorial University…`→`Rabininsia University` (RU), `Islamic University of East Africa`→`Hikmah University of East Africa` (HUEA), `Kilimanjaro Christian Medical University College`→`Kilimanjaro Christian Medical University` (KCMU). Old names kept as search aliases.
- **Tests**: 50 pytest passing (unchanged). **Verified live** (2026-08-18, Django restarted :8000): `/api/locations/wards/?district=11` (Ubungo) → **14** (incl. Kimara, Mbezi, Sinza, Ubungo); Kinondoni → 20 (no Kimara); Kigamboni 9, Chalinze 16, Bumbuli 18, Itigi 13, Geita Town 13, Ifakara Town 19, Malinyi 10, Buchosa 22, Nzega Town 10, Bariadi Town 10, Mbinga Town 19, Ushetu 20, Msalala 18, Tanganyika 16, Nsimbo 12, Busokelo 5, Dodoma City 41. Directory (admin login) 200 with `IPT.institutions` = **115** (NIT, CBE, Mizengo Pinda, IIT Madras all present); locations + institutions caches cleared.

### Skills fields show code (e.g. `['["[\'Database\']"]']`) in inputs — DONE
- **User report**: the Skills field ("Skills (comma separated)") rendered the raw Python list repr into the input (e.g. `['["[\'Database\']"]']`) instead of plain text, on student, company and admin forms. Root cause: `StudentProfile.skills` and `Slot.skills_required` are `JSONField` (store a list) but their form fields were plain `CharField`, so Django wrote `str(list)` into the input, and every re-save split that repr on commas and wrapped it further — compounding corruption each save.
- **Fix**: new `apps/core/skill_fields.py` with `normalize_skills()` (recursively unwraps plain text, JSON arrays, Python list reprs, and previously double-encoded values → clean string list) and `skills_to_text()` (list → "Python, SQL"). Student profile form (`StudentProfileForm`), company slot form (`SlotForm`), and Django admin forms for both (`StudentProfileAdminForm`, `SlotAdminForm`) now set the field's `initial`/`form.initial` to the comma-joined text on display and use `normalize_skills` on `clean`, so the input shows `Database` / `Python, SQL` and saves a clean list.
- **Data cleanup**: existing corrupted values normalized in place (e.g. `['["[\'Database\']"]']` → `['Database']`).
- **Tests**: 50 pytest passing (unchanged). **Verified live** (2026-08-18, Django restarted :8000): student profile (student2) renders `value="Python, SQL"` with no code view; company slot edit (slot 8) renders `value="Python, Git"`; Django admin forms render `Database` / `Python, Git` and clean back to lists.

### Education levels (TCU NQF) on profile + filters on all three search pages - DONE
- **Research** (per user request): TCU's University Qualifications Framework places awards on a 10-level NQF scale - L4 Certificate (Basic Technician Cert/NTA 4), L5 Technician Certificate, L6 Ordinary Diploma, L7 Higher Diploma, L8 Bachelor's, L9 Master's (+PG Cert/Dip), **L10 Doctorate (PhD)**. Note: the user asked for "level 4 certificate to level 9 phd", but per TCU PhD is Level 10; implemented the full exact range 4-10 with official labels.
- **Shared choices**: new `apps/core/education.py` - `EducationLevel` (IntegerChoices 4-10), `education_level_choices()` for dropdowns, `education_level_label()` for short card/table labels ("Bachelor's", "PhD", ...).
- **Models**: `StudentProfile.education_level` + `Slot.education_level` (nullable PositiveSmallIntegerField with TCU choices). Migrations: `students.0006_studentprofile_education_level`, `slots.0002_slot_education_level`.
- **Forms/admins**: field added to `StudentProfileForm`, company `SlotForm` (help text "Minimum TCU qualification: Level 4 Certificate to Level 10 PhD"), `StudentProfileAdmin` (column/filter/fieldset), `SlotAdmin` (column/filter).
- **Filters on all three search surfaces**:
  - Admin directory (`/platform/directory/` students tab): "Education Level" select filters `StudentProfile.education_level`; Year column now shows e.g. `3 - Level 8 - Bachelor's Degree`; companies-tab link strips level params.
  - Student marketplace (`/student/marketplace/`): new "Education Level (TCU)" select; `/api/slots/search/?education_level=` added to signature+cache key+query (`slots/views.py`); serializer exposes `education_level_display`; cards show qualification label next to role type.
  - Company applicants page (`/company/slots/<id>/applicants/?education_level=`): filter select + each applicant row shows their level.
- **Test infra fix (critical)**: full-suite runs hung indefinitely because Redis was down - every signal-handler/fail-open call blocked on a long TCP connect timeout against a half-open Docker port proxy bound to 6379. Fixed in `conftest.py`: tests now back `apps.core.redis_client.get_redis()` with a shared in-process **fakeredis** server (added `fakeredis[lua]==2.37.1` to requirements-dev.txt) - keeps lock/cache/rate-limit unit-test semantics while making every Redis touch instant and hermetic. Suite time restored (~3:44 vs hang).
- **Tests**: 50 pytest passing. **Verified live** (2026-08-22, Django restarted :8000): student profile + marketplace render all seven TCU options; `/api/slots/search/?education_level=8` returns tagged slot with `"education_level_display": "Bachelor's"`; bogus level returns count 0; directory filtered page shows Bachelor's rows; company slot form shows field+help; applicants page filter 200 with select present.

### Swahili/English translation (i18n) with language switcher - DONE
- **Setup**: Django i18n enabled - `LocaleMiddleware` added after SessionMiddleware; `LANGUAGES = [en, sw]`; `LOCALE_PATHS = [backend/locale]`; `path("i18n/", include("django.conf.urls.i18n"))` exposes the `set_language` view. A compact **language switcher** (English / Kiswahili) now sits at the left of the navbar on every page: POSTs to `/i18n/setlang/` with `next={{ request.path }}`, persists via the `django_language` cookie, and the `<html lang>` attribute follows the active language.
- **Templates wrapped** (`{% load i18n %}` + `{% trans %}`/`{% blocktrans %}`): base.html (all nav links, switcher, footer), home, login, register, student dashboard/marketplace/applications/profile/documents/apply, company slots/slot form/applicants. Marketplace card strings are rendered client-side by `marketplace.js`, so marketplace.html now injects a translated `window.IPT.i18n` dict (Apply Now, Slot Full, Verification required..., Unpaid, Available, FULL, Dept:, Year, Loading..., No slots match...) that the JS consumes with English fallbacks.
- **Python strings**: form labels/help texts (`StudentProfileForm`, `SlotForm`, `ApplicationLetterForm`) and user-facing model choice labels (VerificationStatus, Gender, SlotStatus, ApplicationStatus, Document.DocType, EducationLevel incl. TCU level names) wrapped with `gettext_lazy` - they translate automatically wherever `get_*_display`/form labels render.
- **Catalog without gettext tooling**: Windows has no xgettext/msgfmt, so new `scripts/build_translations.py` holds the EN-to-Kiswahili catalog (**208 strings**), writes `locale/sw/LC_MESSAGES/django.po`, and compiles `django.mo` directly (pure-Python GNU MO writer). Re-run after adding strings: append to CATALOG then `backend\.venv\Scripts\python scripts\build_translations.py`. Verified loading: `_()` returns "Dashibodi"/"Inasubiri" under sw.
- **Scope note**: admin console interior pages (directory, ledger, metrics, etc.) still show English inside the page body - nav chrome is translated; wrap those templates in a follow-up pass.
- **Tests**: 50 pytest passing. **Verified live** (2026-08-22, Django restarted :8000): anonymous home + login + authenticated student marketplace all render Kiswahili under the sw cookie (Mkoa/Mikoa Yote/Kiwango chochote/Omba Sasa/Nafasi Imejaa); `/i18n/setlang/` POST round-trip sets the cookie and re-renders in Swahili; English default unchanged.

### Phase 1 of the independent-marketplace spec (organizations rename, universities, deadlines, governance) - DONE
- **Company -> Organization rename (code, URLs, DB)**: model `CompanyProfile` -> `Organization` (table `companies_organization`), `Slot.company` -> `Slot.organization` (column `organization_id`), `UserRole.COMPANY` -> `UserRole.ORGANIZATION` (stored value migrated `COMPANY` -> `ORGANIZATION`), `User.is_company` -> `is_organization`, `User.company_profile` -> `owned_organization`, URL names/paths `company-*` / `/company/` -> `organization-*` / `/organization/`, templates `templates/company/` -> `templates/organization/`. Migrations are hand-written (`companies.0003`, `slots.0003`, `accounts.0005`) because the autodetector cannot see an FK-target rename; do not regenerate them.
- **Deliberately NOT renamed**: the `apps.companies` package/app label (tables, migrations and contenttypes depend on it), `Application.company_message` (column on the write-protected applications table and part of the sealed integrity payload), and `OrganizationType.COMPANY` (a real organization type). The realtime service SQL was updated to the new table/column names - **restart `realtime` after migrating**.
- **Universities**: new `students.University` (name unique case-insensitively, abbreviation, institution_type, region, website, verification_status, suggested_by). `StudentProfile.university` is now an optional FK (was required free text) plus `graduation_year`. Migration `students.0005_university` seeds the approved list from `data/tanzania_institutions.json` and converts each existing free-text value: matches a known name/abbreviation/alias -> that record, anything else -> a PENDING record suggested by that student. Student profile page: dropdown + "My university is not listed" -> `apps/students/universities.py` creates/reuses a PENDING record (max 3 pending per student; duplicates and rejected names are refused). Admins review at `/platform/universities/` (approve / reject (unlinks students) / merge into an approved record / add), all audit-logged. Affiliation grants no access to anything.
- **Phone**: still optional at registration; required (Tanzanian format) on the student's own profile page and saved to `User.phone`. The admin edit page passes `require_phone=False`.
- **Application deadline**: `Slot.application_deadline` (optional). `create_application` refuses new or re-booked reservations after it (a student already holding a PENDING/PAID seat can still pay). Shown on marketplace cards and the apply page; shown as "Applications closed" once passed.
- **Marketplace filters**: new `organization` name search (in the cache signature too); "Department / Course" relabelled "Academic Discipline".
- **Governance**: `/platform/account-status/` suspends/reactivates a student or a whole organization (reason required, audit-logged; sets `is_active=False` + `suspended_at`; accounts that requested deletion stay inactive on reactivate). Suspended organizations vanish from search and cannot take new applications. Directory has an Active/Suspended toggle. New `core.Report` + `/report/` (any signed-in user) and `/platform/reports/` (admin triage with status + notes, audit-logged). Both admin pages use the `user_management` permission module.
- **Also fixed**: directory "year" filter used the removed `current_year` field (now `academic_year`).
- **Tests**: `apps/students/test_universities.py`, `apps/slots/test_deadline_and_search.py`, `apps/core/test_governance.py`. Run `pytest apps` (the stray `backend/test_seed.py` is a script, not a test, and breaks collection of a bare `pytest`). Note the test database already contains the 131 seeded universities (the migration creates them), so tests must `get_or_create` rather than `create` well-known names. **99 passing** after this work.
- **Rollout notes (2026-11-08)**: dev database backed up with `pg_dump` before migrating; `accounts.0005/0006`, `companies.0003/0004`, `core.0004`, `slots.0003/0004`, `students.0005` applied; `realtime` and the Celery worker restarted (they held pre-rename SQL/models). Verified on the dev DB: 2 organizations, 4 slots, roles migrated, the one existing student linked to an approved university, all templates compile and every `{% url %}` / `reverse()` name resolves.

### Organization workspaces, RBAC and name-free registration - DONE (2026-11-07)
- **Organization model**: `Organization` is the unified organizations table (company / institution / university types), with an optional owner `user`, contact fields (`email`, `phone`, `address` kept in sync with `street`) and `verification_status`. `OrganizationMember` links users to organizations; creating an organization with an owner auto-creates the OWNER membership.
- **Multi-account + session cap**: up to 5 ACTIVE members and 5 concurrent logins per organization (see section 3). Helpers in `companies/services.py`: `add_organization_member`, `can_organization_login`, `register_organization_session`, `remove_organization_session`, `cleanup_expired_organization_sessions`; enforced in `process_login_task` and `finalize_auth`, tracked from login/logout signals. Team add/remove UI is on `templates/organization/profile.html` (owner only).
- **RBAC**: permission constants and `@org_permission_required` in `companies/permissions.py` (matrix in section 3); covered by `companies/test_rbac.py`.
- **Registration**: choosing "Organization" hides first/last name and asks only for the organization name (+ email, password, optional phone); `process_registration_task` creates the user and organization in one transaction and rejects a duplicate organization name. Students still give first + last name.
- **Platform admin side**: `SUPER_ADMIN` role + `AdminRole` capability matrix (migration `accounts.0004`), role-management page, audit log.

### Login/registration run as Celery tasks; dev runs them eagerly - DONE (2026-11-07) — PARTLY SUPERSEDED 2026-11-09
- **Superseded by Feature 2**: login is now checked **inline** in the view (the raw password never goes on the Celery broker; `process_login_task` was deleted). Registration still runs as a task but receives only a password **hash**. The post-registration hand-off token is bound to the submitting session, single-use and valid 60 s. After registration the account must pass an **email OTP** before it can sign in. The notes below describe the original design.
- `POST /auth/login/` and `/auth/register/` enqueue `process_login_task` / `process_registration_task` and the page polls `/auth/task-status/<id>/`; on success a signed token is posted to `/auth/finalize/` which calls `login()`.
- **Symptom**: with no Celery worker running, the poll never completes and the log fills with `task-status ... 200 21` (status stays `PENDING`). **Fix**: `config/settings/dev.py` sets `CELERY_TASK_ALWAYS_EAGER = True` and `CELERY_TASK_STORE_EAGER_RESULT = True`, so in dev the task runs inside the request and its result is still readable by the poll (needs Redis for the result store). Production (`prod.py`) still needs a real worker. Verified live: a wrong-password login returns `ERROR` on the first poll.
- Side effect: in dev every Celery task (email, SMS, PDF, fan-out) also runs inside the web request.
- Beware stale processes: several old `runserver` / `celery` / `uvicorn` processes can bind the same port on Windows and keep serving old code (this caused "my changes don't show"). Check `Get-NetTCPConnection -LocalPort 8000,8001` before assuming a code bug. Starting a second uvicorn fails with `[Errno 10048]` because the first is already serving.

### Sidebar navigation for all signed-in users - DONE (2026-11-09)
- The long top menu (12 links for admins) is replaced by a **slide-out drawer** in `templates/base.html` for every authenticated role. Top bar: logo + "IPT Marketplace" at the **left**; language picker, profile chip and the **menu toggle at the far right**. The drawer opens from the **left edge** beneath the top bar (so logo/name stay visible), over a dimmed backdrop; it closes via the toggle, a backdrop click or Esc, and starts closed (`inert` while closed, `aria-expanded` on the toggle, reduced-motion respected).
- Contents by role: student (Dashboard, Find a Slot, My Applications, Profile); organization (Dashboard, Slots, Organization profile); admin, grouped under headings: **Review** (Queue, Directory, Slots, Universities, Reports), **Finance & insight** (Ledger, Metrics), **System** (Templates, Audit, Roles, Integrity, Settings). The current page's link is highlighted via the `is_active` tag (`.side-link.active`).
- **Logout** is its own red-outlined button pinned at the bottom of the drawer, outside the scrolling link list. Anonymous visitors get no drawer; Login/Register stay in the top bar.
- Styles live in the `<style>` block of `base.html` (`.side-link`, `.side-heading`); the old `.nav-link` rule is kept for any page that still uses it. Footer gained a "Report a problem" link for signed-in non-admins.
- Verified by rendering `home.html` for anonymous / student / organization / admin: drawer present with the right links and active highlight, Logout outside the link list. **Not yet checked visually in a browser.**

### Feature 2 — Identity & Access Management (IAM) — DONE (2026-11-09)

Built in order (tests: `pytest apps`, 100+ tests; new files `accounts/tests.py`, `accounts/test_contact_and_password.py`, `accounts/test_admin_mfa.py`, `companies/test_iam_org.py`). Migrations: `accounts.0007–0010`, `companies.0005`, `core.0005` (run `python manage.py migrate`; 0007 adds a unique-phone constraint, so duplicate phone numbers must be resolved first).

**Step 1 — closed authentication holes**
- Removed `TokenAuthentication`, `/api/auth/token/`, `/api/auth/my-token/` (permanent, never revoked, bypassed session rules). WebSocket uses single-use tickets (see section 3).
- Passwords no longer travel through Celery; login is inline; registration task takes `password_hash`.
- `/auth/finalize/`: `TimestampSigner` (salt `accounts.finalize-auth`, 60 s), stored in the session that started the task, consumed on use. `/auth/task-status/<id>/` only answers the session that created the task.
- Old "reset password by knowing email + phone" flow removed (it was an account takeover); recovery needs an OTP. Realtime WS path typed `user_id` as `int` although ids are UUIDs: fixed (not tested live).

**Step 2 — OTP, lockout, unique phone** (`accounts/otp.py`, `core/lockout.py`)
- `OneTimeCode` rows: 6 digits, **5 min** expiry, single use, **HMAC-hashed** (never stored plain), 5 guesses then dead, a new code invalidates the previous one, resend limited by the existing token bucket (`consume`, burst 3 then 1/min). Delivered **synchronously** (a Celery task would put the code on the broker).
- **Registration**: account is created first, then an **email-only** OTP (no channel choice) is required before the first sign-in (students and organization members; admins are exempt). **Recovery and later flows offer email or SMS**; a code is only sent to a method already verified on the account; identical response whether or not the account exists. Recovery is three screens: email → choose method (`/auth/reset-password/method/`) → code → new password. A password change signs out all other sessions (Django ties sessions to the password hash).
- **Lockout**: 5 failed logins on an email (`LOGIN_LOCKOUT_ATTEMPTS`) lock sign-in for 5 min (`LOGIN_LOCKOUT_SECONDS`), keyed on the submitted email whether or not an account exists; never disables the account; cleared on success. Works alongside the existing token-bucket limiter (not a second limiter). Trade-off: someone can lock out a known email for 5 minutes.
- **Phones** are stored digits-only and unique (DB partial unique index `uniq_user_phone_nonblank`); emails already unique.

**Step 3 — sessions** (`accounts/sessions.py`, `SessionPolicyMiddleware`)
- **10 min idle** lifetime (`SESSION_COOKIE_AGE=600`), enforced on the server per request (`login_at` / `active_at` in the session) and by the session row expiry, so it holds offline. Real activity restarts it: `session-guard.js` reports mouse/keyboard/touch/scroll to `POST /auth/session-extend/` (at most every 10 s; `sessions.touch`), while `/auth/session-status/` polling never extends. A hard ceiling from login, `SESSION_ABSOLUTE_MAX_SECONDS` (default 3 h), still ends the session however active the user is. HttpOnly, SameSite=Lax, Secure in prod; nothing in localStorage/sessionStorage. Session ids are Django CSPRNG (>=160 bits), rotated at login, MFA elevation, hardware step-up, forced password change.
- **One live session per account**: `User.current_session_key`; a new login destroys the old session row and publishes `session_revoked` on `ipt:notify` (ws.js redirects the old tabs; the realtime service closes the socket). `static/js/session-guard.js` polls `/auth/session-status/` every 20 s and warns 60 s before expiry. The backend alone decides validity.
- The organization cap now counts **people** (max 5 members => 5 logins); a member signing in again replaces their own session. Logout destroys the server session; authenticated responses send `Cache-Control: no-store`.
- Role changes / removal / suspension call `revoke_user_session` so the user must sign in again with a new id. The client-side idle logout in `base.html` now matches the server (10 minutes, popup with a 30 s countdown; `session-guard.js` only warns at 30 s for the 3 h ceiling).

**Step 4 — audit trail** (`core/audit.py`)
- `AuditLog` gained `organization`, `actor_role` (role at the time), `target_type/id/label`, `outcome` (SUCCESS/FAILED/REJECTED), `context` JSON. Actor name+email is copied into `actor_label`, so former members stay attributable and searchable. Sealed into the integrity ledger; new fields join the hash only when set, so old records still verify.
- Org events recorded: login/logout, member add/remove/role change, profile edits, slot create/edit/publish/pause/delete, application accept, applicant-document views, document upload/delete/access, audit searches and exports.
- **Org view** `/organization/audit/` (Manager (Owner) and Deputy Manager only; recruiters and former members get nothing; always scoped to the viewer's organization). **Platform console** `/platform/audit-logs/` with organization / actor / action / date range / target / status / module / keyword filters, event detail, CSV export; searching and exporting are themselves audited. Shared implementation `filter_audit_logs`.

**Step 5 — organization members**
- First registrant is `OWNER`, displayed as **Manager (Owner)**; `MANAGER` is displayed **Deputy Manager** (labels only; RBAC matrix unchanged, plus `audit.view` for both).
- Removal is **soft**: `OrganizationMember.membership_status=REMOVED` with `removed_at/by/reason`, user `is_active=False` (never deleted), sessions destroyed, audited. **Former Members** page `/organization/team/former/` (name, email, previous role, date added/removed, removed by, reason). Role change page action revokes the member's session. Members get their own credentials: Manager sets a **temporary password** that must be replaced at first sign-in (`must_change_password` gate) after the member verifies their own email by OTP.
- Documents are organization-owned: scoped by `Organization.historical_member_user_ids()` so a former member's uploads stay with the organization. `view_document` is tenant- and permission-scoped (student documents only for a paid application to the viewer's organization and `applicants.view`; org documents for any active member) and audits access. Cross-organization direct-URL probes (slots, applicants, letters, documents, audit events, removal) all 404/403 (tested).

**Step 6 — organization registration**
- Organization form collects organization name, official email, organization phone (landline allowed), plus the first member's full name, personal login email, own phone and position. `OrganizationMember.position` holds the real-world title; the role (Manager) sets permissions.

**Step 7 — contact changes** (`accounts/views_account.py`, page `/auth/account/`)
- Change email or phone: current password + OTP sent to the **new** value; nothing is saved until it is verified; wrong code rejects, expired code needs a new one; uniqueness re-checked at save time with the agreed message and no identity leak. The same flow verifies the current phone. Students **cannot apply** until their phone is verified. `/auth/change-password/` also serves the forced first-login change. Sidebar link "Account & security" for everyone.

**Step 8 — administrators and MFA**
- **Creation**: Super Admin only (`/platform/admins/`), no public admin registration (`RegisterForm` offers only Student/Organization). Generates a temporary password (`ADMIN_TEMP_PASSWORD_HOURS`, default 24) emailed to the admin, never shown; must be replaced at first sign-in, then MFA is set up before any admin page works (`accounts/gates.py`). Role change, suspension, approver designation, temporary-password reissue are audited and end the admin's session.
- **MFA** (`accounts/mfa.py`): TOTP (secret Fernet-encrypted, replay-protected) or FIDO2/WebAuthn (`webauthn` lib; RP id derived from the request host, works on `localhost`, not on a bare IP). **Super Admins and break-glass accounts must use a hardware key**; TOTP is refused for them. Sign-in is password -> second factor -> session (no session after the password alone). 5 failed factors lock for 5 min. `static/js/webauthn.js` talks to `/auth/mfa/webauthn/*`.
- **Recovery codes**: 10 one-use codes per set, shown once, stored only as HMAC hashes; required before admin access; using one forces generating a new set (old set invalid).
- **Hardware re-verification** (`@hardware_reverify`, `HARDWARE_REVERIFY_SECONDS=300`) on POSTs for: create/modify admins, role matrix, platform settings, account suspension, recovery decisions, break-glass actions. Step-up page `/auth/mfa/step-up/`; it rotates the session id.
- **M-of-N recovery** (`accounts/recovery.py`): locked-out admin files a request at `/auth/admin-recovery/` (generic answer, rate limited); designated approvers (`User.is_recovery_approver`) vote in `/platform/recovery/`; the requester can never approve their own, only designated approvers vote, one vote each, `ADMIN_RECOVERY_APPROVALS` (default 2) approvals execute (target's keys, TOTP and codes wiped, sessions ended, must re-enroll), quorum becoming impossible rejects; every step audited.
- **Break-glass** (`accounts/breakglass.py`, `/platform/break-glass/`): 256-bit password shown once for offline storage, hardware key mandatory (register it during the setup ceremony), TOTP/recovery codes not accepted, every normal control still applies. Use raises an audit event + email to all other Super Admins, flags `review_pending` + `rotation_required`, shows an emergency banner, and tags its audit actions `break_glass`. Rotation and review are explicit actions.

**Settings / env** (`config/settings/base.py`): `SESSION_COOKIE_AGE`, `LOGIN_LOCKOUT_ATTEMPTS/SECONDS`, `ADMIN_MFA_ENFORCED`, `SUPER_ADMIN_REQUIRE_FIDO2`, `HARDWARE_REVERIFY_SECONDS`, `ADMIN_TEMP_PASSWORD_HOURS`, `ADMIN_RECOVERY_APPROVALS/HOURS`, `WEBAUTHN_RP_ID/NAME`. Tests set `ADMIN_MFA_ENFORCED=False` and blank the Beem SMS keys in `conftest.py` (tests must never send real SMS).

**Rollout notes for the dev database**
- Existing students/organization users have `email_verified=False` and are asked to verify at next login. Existing admins (`admin@admin.com`, `admin@test.com`) must enroll MFA at next login and, as Super Admins, **need a FIDO2 key**. Without one, set `SUPER_ADMIN_REQUIRE_FIDO2=False` (TOTP then works) or `ADMIN_MFA_ENFORCED=False` in `backend/.env` for local work only.
- Real SMS: `BEEM_API_KEY`/`BEEM_SECRET_KEY` in `backend/.env` (gitignored); Beem only delivers from a sender name registered on the account (`BEEM_SENDER_ID`).
- Restart Django, Celery and uvicorn after pulling; old processes keep serving old code.

**Spec section 14 verification checklist (result)**
1. One active session per account, org limit kept: done + tested. 2. Server-side expiry incl. offline: done + tested (time-travel test). 3. Session id regenerated on role change/elevation: role changes destroy the session; MFA/step-up/password change rotate the id: tested. 4. Documents tenant-scoped: fixed + tested. 5. Member removal preserves history: tested. 6. Team changes in the ledger: tested. 7. Former members searchable in audit: tested. 8. Admin creation Super-Admin-only + onboarding: tested. 9. MFA enforced before admin access: tested. 10. Cross-organization direct URL access: tested.

**Known gaps / not verified**
- FIDO2 is verified end-to-end only against a **software authenticator** in tests; no physical key or real browser was used. The new pages (admin console, MFA, audit, former members, account security) were **not checked visually in a browser**.
- Realtime ticket redemption and the `session_revoked` socket close are not covered by automated tests; run `backend/e2e_phase3.py` / a live check.
- Former members cannot be re-added with the same email (their account is kept inactive); email/phone changes do not need hardware keys; temporary passwords go by email only (no SMS); the recovery-request form is public by design (rate limited, generic answers).
- Anonymous-session flows (OTP, recovery) also live under the 10-minute cookie age.
- The previous `plan.md` contained the Postgres superuser password; it was already committed in the initial commit, so **rotate that password** (it is now removed from this file).

### Security Audit & Full Hardening (C1–C4, H1–H6, M1–M10, Low) — DONE (2026-11-09)

Completed all remaining security audit items across `backend/`, `realtime/`, Docker, templates, and static JS:
- **C1 (Realtime payment webhook & mock gateway hardening)**: gated `/api/v1/payments/mock/initiate` and `/api/v1/payments/mock/callback` behind `ENABLE_MOCK_GATEWAY=false` (forbidden when `DJANGO_ENV=prod`). Separated mock HMAC domain (`sign_mock`/`verify_mock` with `"MOCK|"` prefix and `mock_gateway_webhook_secret`) from real webhook verification (`verify`) and required valid signatures on `/mock/callback`. Added Decimal amount verification (`_amounts_equal`) against `payments_payment.amount`, restricted CORS origins in `realtime/main.py`, and bound port `8001` to `127.0.0.1` in `docker-compose.yml`.
- **C2 & C3 (Recovery session revocation & hand-off hardening)**: `reset_password` now calls `revoke_user_session(user, reason="password_reset")` immediately after updating the password. Added rate limits to `/auth/task-status/<id>/` and `/auth/finalize/` and sanitized task exception responses.
- **C4 (Fail-fast production secret validation)**: defaulted `DEBUG = False` in `config/settings/base.py`; added `validate_production_config` in `base.py` / `prod.py` and `Settings.validate_production_settings` in `realtime/app/core/config.py` to fail fast on startup if `SECRET_KEY`, `ALLOWED_HOSTS`, `GATEWAY_WEBHOOK_SECRET`, `SELCOM_API_KEY`, `SELCOM_API_SECRET`, `SELCOM_VENDOR_TILL`, or database credentials are missing or use development defaults.
- **H1 (Uploaded documents XSS & malware hardening)**: `apps/documents/validators.py` derives MIME types strictly from server-side magic bytes (`detect_mime_and_ext`), rejects unknown/HTML/script files and OLE/OOXML macro containers, sanitizes storage filenames, blocks serving documents with `scan_status == "ERROR"`, and serves files via `build_safe_file_response` (`X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox; default-src 'none'`, `Cross-Origin-Resource-Policy: same-origin`, `attachment` for Office documents).
- **H2 (WebSocket transport & ticket safety)**: `static/js/ws.js` automatically selects `wss://` on `https:` origins and URL-encodes user IDs and tickets; `templates/base.html` quotes `window.IPT.currentUserId = "{{ user.id }}"`.
- **H3 (Selcom webhook replay protection, amount verification & unified fee)**: added `_is_timestamp_fresh` (5-minute window) to `verify_selcom_webhook_signature`; verified `amount`, `currency`, and duplicate `transid` in `PaymentWebhookView.post`; unified fee calculation via `get_application_fee()` (`PlatformSetting` `app_fee_amount` → `settings.APPLICATION_FEE` → `15000.00 TZS`) across `students/views.py` and `payments/views.py`; replaced `print()` with `security` logger warnings.
- **H5 (Production security headers, CSP & HSTS)**: added `SecurityHeadersMiddleware` (`apps/core/security.py`) emitting `Content-Security-Policy`, `Permissions-Policy`, `Referrer-Policy`, and `X-Content-Type-Options: nosniff`. Configured `SECURE_HSTS_SECONDS = 31536000`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `SECURE_PROXY_SSL_HEADER`, `X_FRAME_OPTIONS = "DENY"`, `CSRF_COOKIE_SAMESITE = "Lax"`, `CSRF_TRUSTED_ORIGINS`, and `security` logging in `base.py` and `prod.py`.
- **H6 (Docker & infrastructure hardening)**: bound Postgres to `127.0.0.1:5432:5432` in `docker-compose.yml`, created non-root `appuser` in `Dockerfile`, and updated `.dockerignore` to exclude `backend/media/`, `celerybeat-schedule*`, and test/seed scripts.
- **M1–M10 & Low items**:
  - **M1 & M2**: added `fail_closed=True` support in `apps/core/rate_limit.py` for auth and payment initiation endpoints; updated `client_ip` (`apps/core/audit.py`) and `client_bucket` (`realtime/app/core/rate_limit.py`) to only trust `X-Forwarded-For` when `TRUSTED_PROXY_COUNT > 0`; added `@token_bucket` / `PaymentInitiateThrottle` to `check_auth_task`, `finalize_auth`, `students.views.documents`, `companies.views.upload_document`, `submit_report`, `legal_pdpa_request`, and `InitiateSTKPushView`.
  - **M4 & M5**: removed raw `str(e)` leakage; replaced `innerHTML` with `textContent` in `processing.html` and `form-loading.js`; added `safe_json_dumps` (`\u003c`, `\u003e`, `\u0026` escaping) for inline `<script>` JSON data; sanitized CSV export cells in `audit.write_csv` against formula injection.
  - **M6, M8 & M10**: stopped rendering `sms_api_key` into `admin_settings.html` (preserving the stored key when submitted blank); added `MaintenanceModeMiddleware` (`503 Service Unavailable` for non-admins when `maintenance_mode` is enabled); validated numeric inputs in `admin_settings`.
  - **Low**: `User.is_platform_admin` now checks `self.is_active`; `backend/pytest.ini` sets `testpaths = apps`.

## 13. Run Instructions (dev)

```powershell
# 0) Once: create venv + deps
backend\.venv\Scripts\python -m pip install -r backend\requirements-dev.txt

# 1) Database (once)
$env:PG_USER='postgres'; $env:PG_SUPERUSER_PASSWORD='<your postgres superuser password>'
scripts\create_db.ps1

# 2) Redis (once)
docker run -d --name ipt-redis -p 6379:6379 --restart unless-stopped redis:7-alpine

# 3) Everything at once
scripts\dev.ps1

# 4) Tests (run from backend\; target apps\ - a bare `pytest` also collects the seed script backend\test_seed.py and aborts)
cd backend
.venv\Scripts\python -m pytest apps            # add --reuse-db to skip rebuilding the test DB
```

After pulling schema changes: `python manage.py migrate` (from `backend\`), then **restart `realtime` and the Celery worker** - they read the same tables with their own copies of the SQL/models. The migration that seeds universities needs `data\tanzania_institutions.json`; `python manage.py seed_universities` re-creates any that are missing. Django start-up on this machine takes a minute or more, so allow time before concluding something hung. There is no git repository here: take a `pg_dump` (and copy the source) before large changes.

URLs: Django `http://127.0.0.1:8000` · admin `http://127.0.0.1:8000/admin/` · FastAPI `http://127.0.0.1:8001` · health `http://127.0.0.1:8001/health`. New console pages: `/platform/universities/`, `/platform/reports/`, `/platform/account-status/` (POST); user-facing: `/report/`.
## Responsive layout, navigation rail and dark mode

- `backend/static/css/theme.css`: colour tokens as CSS variables (`--c-*`, RGB triplets) for light and `html.dark`; Tailwind colours in `base.html` read them as `rgb(var(--c-x) / <alpha-value>)`. Arbitrary-hex utilities (`bg-[#e8f5e9]` etc.) have generated dark overrides.
- Theme: follows `prefers-color-scheme` by default; topbar button cycles System/Light/Dark (`localStorage ipt-theme`); a head script applies it before first paint; printing always uses light.
- `backend/static/css/responsive.css` + `js/responsive.js`: icon rail sidebar on >=768px (click expands to 256px and pushes `#app-shell`, always starts collapsed), phone drawer (70vw, max 280px, backdrop); every `<table>` gets `data-label`s and becomes stacked cards under 640px (opt out with `data-no-cards`); print stylesheet; wider containers and zoom on 1536/1920/2560px screens.
- New templates: use `side-label` spans for nav text, theme tokens (not raw hex) for colours, and `grid-cols-1 sm:grid-cols-N` for grids.

## Welcome / register / login uplift (Tier 1)

- Welcome page (`templates/home.html`, data from `apps/accounts/public.py`, cached 5 min under `public:home:v1`): trust counters that appear only above `PUBLIC_STATS_MIN` (base.py: organizations 10, slots 20, universities 5, students 100, regions 3), a preview of the 6 newest OPEN slots of APPROVED organizations (title, industry, place, level, seats left, stipend yes/no; never the organization), tabbed "How it works" (`includes/how_step.html`), and a fee section reading `get_application_fee()` with a link to Terms `#payments` (fees are non-refundable).
- Register is a 3-step wizard (role -> details -> password and terms). Steps are checked in the browser only; the final POST is unchanged and the server still validates everything. Its submit guard runs in the capture phase because `form-loading.js` would otherwise POST anyway.
- `static/js/phone-input.js` handles every input whose name ends in `phone`: fixed +255 chip, accepts 07XX/06XX/7XX/255..., submits `255XXXXXXXXX` through a hidden input that takes over the field's name.
- Share/SEO: meta, Open Graph and favicons in `base.html`; pages are `noindex` unless they override `{% block robots %}`; `/robots.txt` and `/sitemap.xml` (`apps/accounts/seo.py`); images generated from the logo in `static/images/`.
- Kiswahili: new strings were added to `locale/sw/LC_MESSAGES/django.po` and `django.mo` was rebuilt with a small script because msgfmt is not installed here. The Swahili text needs a native speaker's review.

## Register/login hardening (Tier 2, no paid or keyed services)

- **No account enumeration on register.** `RegisterForm` no longer reports an existing email (its `validate_unique` is disabled on purpose, otherwise Django's "User with this Email already exists" leaks). `process_registration_task` returns `user_id="decoy"` for an existing address and emails its owner (at most once an hour, via `consume("register-notice")`), with login and reset links passed in from the view. `finalize_auth` then routes the decoy to `verify-email`, where `views_otp._decoy_verify_email` mirrors the real page, resend limits and the 5-guess lockout, but no code ever works. A duplicate phone gets one generic message that does not say why.
- **Leaked passwords.** `apps.accounts.validators.BreachedPasswordValidator` (in `AUTH_PASSWORD_VALIDATORS`) checks a bundled, offline list of ~46,000 leaked passwords of 8+ characters (`apps/accounts/data/breached_passwords.txt`, derived from the UK NCSC list). No external call.
- **Keyless bot check** on login and register (`apps/accounts/antibot.py`): hidden honeypot `website` plus a signed `form_ts` timestamp (min 2 s login, 5 s register, max 6 h). Failures all show the same generic message. `ANTIBOT_ENABLED` (env, default on); `conftest.py` turns it off and `test_security.py` turns it on.
- **Organization identifiers.** `Organization.brela_number` and `Organization.tin` (migration `companies/0006`), both optional, asked at registration and editable on the profile; TIN normalised to 123-456-789 (`apps/companies/identifiers.py`). The admin verification page shows them and a warning when the official email is a free webmail address (`Organization.official_email_is_free`; never blocks).
- Not done by choice: Cloudflare Turnstile (needs a key), passkey sign-in for non-admins, new-device alerts, the marketing-consent checkbox and the verified-organization badge.

## Entry points, FAQ/WhatsApp and signup funnel (Tier 3, no keys)

- **Role entry points.** `/auth/register/?role=student|organization` (`ROLE_PARAMS` in `apps/accounts/views.py`) pre-ticks the role and the wizard starts at step 2 with a "Registering as ... Change" bar. It is GET-only: a rejected POST shows the wizard at the failing step, never skips step 1. Buttons are in the welcome hero, the closing banner and the login page.
- **FAQ and WhatsApp.** The welcome page FAQ (`<details>`, `includes/faq_item.html`; the fee answer reads the live fee) and a floating `wa.me/255756322833` button in `base.html` for signed-out visitors only (hidden when printing). The questions are drafts for the owner to correct.
- **Signup funnel** (`apps/core/funnel.py`): per-day Redis counters via `incr_daily("funnel:<step>")`; no cookies, no IPs, so the numbers are page views. Steps: welcome, register view, wizard step 2 and 3, submit, account created. Crawlers are skipped, signed-in visitors do not count as welcome views, and only a real new account counts as "created" (the decoy outcome does not). The wizard reports steps 2/3 through `POST /auth/funnel/` (CSRF-exempt on purpose, whitelisted names, rate-limited). Shown on the admin Metrics page.
- Not done by choice: #17 Google sign-in (needs an OAuth client), #19 testimonials/logos (needs real content and consent), #21 compiled Tailwind (deferred; when done, commit the built file).

## Welcome search, templates editor, metrics charts, one input style, SMS fields hidden

- **Welcome-page slot search** (`apps/accounts/public.py`, `includes/public_slots.html`, `static/js/public-search.js`): Region, District, Industry and Education level. It is a plain GET form on `/` (works without JS); JS refreshes the cards from `/slots/public/` (rate-limited HTML fragment). Filters are validated and ignored when malformed; results are capped at 12 for visitors; organization names never leave the server; suspended organizations are excluded (the preview previously did not exclude them).
- **Notification templates** (`apps/notifications/events.py`, view `notification_templates`): the senders look templates up by key + channel, so templates are created *for* one of four system events (`EVENTS`), the channel always follows the event, and migration `notifications/0002` preloads the four built-ins with wording identical to the senders' fallbacks (a test guards this). Only plain `{name}` placeholders from the event's variable list are accepted (anything else would make the sender skip formatting). Actions: create, duplicate-as-starting-point, edit, delete (falls back to built-in wording), send-me-a-test (rate limited, audited). `acceptance_email` now also gets `company_note`.
- **Metrics** (`apps/core/metrics.py`, `static/js/metrics-charts.js`): range switch 7/30/90/all (all = monthly, up to 3 years), 14 charts rendered by one generic renderer, "No data in this period" states, dark-mode colours, Chart.js served locally from `static/vendor/chartjs/`. Searches and funnel counters are only kept 45 days.
- **One input style**: every control is the icon-chip field (`.ipt-field-box`); `includes/form_field.html` + `form_tags.field_icon` draw label/chip/help/errors for Django fields; prefix chips (`+255`, `TZS`) use `.ipt-field-prefix`; a zero-specificity `:where()` fallback in `responsive.css` styles any bare control. `test_no_raw_form_control_sits_outside_an_icon_chip_box` fails if a template adds an unstyled one (the top-bar language switcher is the only exception).
- **SMS gateway**: the endpoint and API key fields are gone from Admin Settings and `admin_settings` ignores `sms_api_endpoint` / `sms_api_key` in a POST. Gateway configuration lives only in the server `.env`.

## Welcome page: 6 open slots from the most-searched places

- `apps/accounts/public.py`: `PREVIEW_LIMIT = SEARCH_LIMIT = 6`. A search returns at most 6 open slots; with no search the page shows `_popular_slots()`: open slots from the regions searched most in the last 30 days (ties: most open slots), newest first inside a region, topped up from the next region. No searches yet = region with the most open slots leads (no caption).
- Searches are counted per region in a per-day Redis hash (`incr_daily_field` / `daily_field_totals` in `apps/core/cache.py`, key `region-searches`, 45-day TTL, no identifiers, crawlers skipped). Counted from the welcome page (`_count_region_search` in `accounts/views.py`, page and fragment) and the logged-in student search (`slots/views.py`). A district-only search counts for its region. Fails open without Redis.
- Hidden always: not OPEN, past deadline, unapproved or suspended organization, and full slots (PENDING + PAID >= capacity).
- Kiswahili: "showing %(shown)s" and "starting with %(region)s, the most searched region" (needs native review). `django.mo` rebuilt with the scratchpad compile script.
- Tests: `apps/accounts/test_public.py` (29 pass).
