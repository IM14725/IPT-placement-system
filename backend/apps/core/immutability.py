"""Enterprise immutability layer.

A tamper-evident, append-only integrity ledger (``core.IntegrityRecord``).

Every finalized critical record (AuditLog, Payment, Application, Document) is
"sealed": a SHA-256 over a canonical payload of its immutable fields, chained
to the hash of the previous ledger row. Editing a sealed record breaks its own
hash; deleting or reordering a ledger row breaks the chain. DB triggers
additionally refuse UPDATE/DELETE on finalized rows so integrity is enforced
regardless of the write path (ORM, raw SQL, or the FastAPI webhook).

The canonical serialization and hash are pure Python so the same algorithm is
reproducible from Django, from data migrations, and from the realtime service.
"""

import hashlib
import json
from datetime import datetime


def _to_str(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def canonical(record_type, record_id, fields):
    """Deterministic canonical JSON for a sealed record."""
    return json.dumps(
        {"type": record_type, "id": record_id, "fields": fields},
        sort_keys=True,
        separators=(",", ":"),
        default=_to_str,
    )


def compute_hash(record_type, record_id, fields, prev_hash):
    """SHA-256 of the canonical payload chained to the previous hash."""
    material = canonical(record_type, record_id, fields) + "|" + (prev_hash or "")
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def seal(record_model, *, record_type, record_id, fields):
    """Append a ledger row for ``record_type/record_id`` (deduplicated).

    ``record_model`` is the IntegrityRecord model (real or historical) so the
    same code works from runtime signals and from data migrations.
    """
    payload = json.loads(json.dumps(fields, default=_to_str))
    prev = (
        record_model.objects.order_by("-id")
        .values_list("record_hash", flat=True)
        .first()
        or ""
    )
    last_for_record = (
        record_model.objects.filter(record_type=record_type, record_id=record_id)
        .order_by("-id")
        .first()
    )
    if last_for_record is not None:
        expected = compute_hash(record_type, record_id, fields, last_for_record.prev_hash)
        if expected == last_for_record.record_hash:
            return last_for_record
    return record_model.objects.create(
        record_type=record_type,
        record_id=record_id,
        record_hash=compute_hash(record_type, record_id, fields, prev),
        prev_hash=prev,
        payload=payload,
    )


def verify_record(record_model, record, current_fields):
    """Return True if ``record`` still matches ``current_fields``."""
    expected = compute_hash(
        record.record_type, record.record_id, current_fields, record.prev_hash
    )
    return expected == record.record_hash


def verify_chain(record_model):
    """Walk the ledger verifying internal links. Returns a list of issues."""
    issues = []
    prev = ""
    for row in record_model.objects.order_by("id"):
        if row.prev_hash != prev:
            issues.append(
                f"Chain break before #{row.id} ({row.record_type}#{row.record_id}): "
                f"expected prev {prev[:12]}… got {row.prev_hash[:12]}…"
            )
        prev = row.record_hash
    return issues


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
