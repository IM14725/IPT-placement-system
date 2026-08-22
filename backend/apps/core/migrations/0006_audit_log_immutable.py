from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION enforce_audit_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Audit log is append-only and immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_immutable
BEFORE UPDATE OR DELETE ON core_auditlog
FOR EACH ROW EXECUTE FUNCTION enforce_audit_immutable();
"""

REVERSE = """
DROP TRIGGER IF EXISTS trg_audit_immutable ON core_auditlog;
DROP FUNCTION IF EXISTS enforce_audit_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_integrityrecord"),
    ]

    operations = [
        migrations.RunSQL(SQL, REVERSE),
    ]