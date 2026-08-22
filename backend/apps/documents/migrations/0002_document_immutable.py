from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION enforce_document_immutable() RETURNS trigger AS $$
BEGIN
    IF OLD.is_verified THEN
        RAISE EXCEPTION 'Verified document % is immutable', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_document_immutable
BEFORE UPDATE OR DELETE ON documents_document
FOR EACH ROW EXECUTE FUNCTION enforce_document_immutable();
"""

REVERSE = """
DROP TRIGGER IF EXISTS trg_document_immutable ON documents_document;
DROP FUNCTION IF EXISTS enforce_document_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(SQL, REVERSE),
    ]