"""Tamper-evident integrity ledger for the FastAPI payment webhook.

Mirrors backend/apps/core/immutability.py so hashes computed here match the
ones Django computes (same canonical serialization + SHA-256 chain). Seals a
record by inserting a row into core_integrityrecord.
"""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import text


def canonical(record_type, record_id, fields):
    def _to_str(value):
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    return json.dumps(
        {"type": record_type, "id": record_id, "fields": fields},
        sort_keys=True,
        separators=(",", ":"),
        default=_to_str,
    )


def compute_hash(record_type, record_id, fields, prev_hash):
    material = canonical(record_type, record_id, fields) + "|" + (prev_hash or "")
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def seal(session, *, record_type, record_id, fields):
    def _to_str(value):
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    payload = json.loads(json.dumps(fields, default=_to_str))
    prev = (
        await session.execute(
            text(
                "SELECT record_hash FROM core_integrityrecord "
                "ORDER BY id DESC LIMIT 1"
            )
        )
    ).scalar() or ""

    last = (
        await session.execute(
            text(
                "SELECT id, record_hash, prev_hash FROM core_integrityrecord "
                "WHERE record_type = :t AND record_id = :i "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"t": record_type, "i": record_id},
        )
    ).first()
    if last is not None:
        expected = compute_hash(record_type, record_id, fields, last.prev_hash)
        if expected == last.record_hash:
            return last.id

    now = datetime.now(timezone.utc)
    new_id = (
        await session.execute(
            text(
                "INSERT INTO core_integrityrecord "
                "(record_type, record_id, record_hash, prev_hash, payload, created_at) "
                "VALUES (:t, :i, :h, :p, :pl, :n) RETURNING id"
            ),
            {
                "t": record_type,
                "i": record_id,
                "h": compute_hash(record_type, record_id, fields, prev),
                "p": prev,
                "pl": json.dumps(payload),
                "n": now,
            },
        )
    ).scalar()
    return new_id