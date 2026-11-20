# Single-server scaling: Postgres tuning, pooling, partitioning

Target: one Windows server, 8 GB RAM, running PostgreSQL, Redis, Django, FastAPI and Celery together.

## 1. Queries and indexes

Migrations `*_scaling_indexes` (applications 0007, slots 0007, notifications 0004, payments 0003,
core 0006, companies 0009):

| Index | Serves |
|---|---|
| `Application(status, payment_deadline)` | `expire_unpaid` and `remind_expiring` beat jobs (every 10 min) |
| `Application(slot, status)` | capacity counts (PENDING + PAID per slot) |
| `Slot(status, organization)`, `Slot(status, application_deadline)` | slot search, company slot list |
| `Notification(user, is_read, -created_at)` | bell, unread counts, inbox |
| `Message(status, created_at)` | SMS/email retry jobs |

Dropped as redundant: `Payment(reference_id)` (already `unique`), `Payment(status)` (low selectivity),
`IntegrityRecord.record_type` (prefix of `(record_type, record_id)`), `OrganizationSession.session_key`
(`unique` already indexes it). Dropping an index does not touch rows, so the immutability triggers
are unaffected.

`apps/core/test_query_counts.py` fails if a busy page runs more queries for 6 applicants than for 2
(an N+1). Add a page to it whenever you add a list view.

Find slow queries on real data: `psql -f scripts\slow_queries.sql` (needs `pg_stat_statements`).

## 2. Connection pooling (no PgBouncer)

PgBouncer has no maintained native Windows build, so pooling is done inside each process:

- Django: set `DB_POOL=1` (psycopg pool; `DB_POOL_MIN=2`, `DB_POOL_MAX=10`, `DB_POOL_TIMEOUT=10`).
  Off, connections are reused for 60 s with a health check.
- FastAPI: `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=5`, `DB_POOL_TIMEOUT=10`, `DB_POOL_RECYCLE=1800`.

Connection budget against `max_connections = 100` (leave ~10 for admin/migrations/psql):

| Process | Count | Max each | Total |
|---|---|---|---|
| Django web workers | 4 | 10 | 40 |
| Celery worker (`--pool=solo`) + beat | 2 | 1-2 | 4 |
| FastAPI | 1 | 10 (5 + 5 overflow) | 10 |

If you add web workers, lower `DB_POOL_MAX` so `workers x DB_POOL_MAX` still fits. Connections are not
the only cost: each one can use `work_mem` per sort node, which is why `work_mem` stays at 16 MB.

## 3. PostgreSQL configuration

`scripts\postgres_tune.ps1 -SuperUser postgres` applies these with `ALTER SYSTEM` (8 GB box):

| Setting | Value | Why |
|---|---|---|
| `shared_buffers` | 2 GB | ~25% of RAM; hot tables and indexes stay cached (restart) |
| `effective_cache_size` | ~4.8 GB | planner hint: OS cache + shared_buffers |
| `work_mem` | 16 MB | per sort/hash node; x100 connections worst case stays inside RAM |
| `maintenance_work_mem` | ~490 MB | faster VACUUM and index builds |
| `max_connections` | 100 | sized with the pool budget above (restart) |
| `random_page_cost` | 1.1 | SSD |
| `idle_in_transaction_session_timeout` | 60 s | a stuck request must not hold the seat-booking row locks |
| `lock_timeout` | 15 s | fail fast instead of piling up behind a lock |
| `log_min_duration_statement` | 500 ms | slow queries land in the Postgres log |
| `shared_preload_libraries` | `pg_stat_statements` | per-query totals (restart) |
| autovacuum scale factors | 0.05 / 0.02 | applications, notifications and sessions churn; vacuum earlier |

Restart the PostgreSQL service once afterwards. For another RAM size pass `-RamGb`.
Check the result with `SHOW shared_buffers;` and the connection section of `slow_queries.sql`.

## 4. Caching

Redis caching already exists (singleflight hot-key cache, versioned slot-search keys). Nothing added.
Notification retention: `notifications.purge_old_read` runs daily and deletes **read** notifications
older than `NOTIFICATION_RETENTION_DAYS` (default 90). Unread ones are kept.

## 5. Partitioning: not done, revisit at these thresholds

Not worth it yet, and the audit, payment, application and verified-document tables are protected by
immutability triggers and the hash-chained `IntegrityRecord` ledger, which partitioning would have to
re-implement (a primary key on a partitioned table must include the partition key, and triggers and the
chain ordering would need rework). Revisit when:

- `core_auditlog` or `core_integrityrecord` passes **~20-30 million rows**, or queries on them stay slow
  with the indexes above, or
- `notifications_notification` passes **~10 million rows** even with the daily purge, or
- `VACUUM`/index rebuild windows on any table exceed what the maintenance window allows.

The first candidate would be `notifications_notification` (not part of the ledger), partitioned monthly
by `created_at`; the ledger tables would need a separate design.
