from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION enforce_application_immutable() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'PAID' OR OLD.is_accepted THEN
            RAISE EXCEPTION 'Application % is immutable', OLD.id;
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.is_accepted THEN
        RAISE EXCEPTION 'Accepted application % is immutable', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_application_immutable
BEFORE UPDATE OR DELETE ON applications_application
FOR EACH ROW EXECUTE FUNCTION enforce_application_immutable();
"""

REVERSE = """
DROP TRIGGER IF EXISTS trg_application_immutable ON applications_application;
DROP FUNCTION IF EXISTS enforce_application_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0006_application_letter_sha256"),
    ]

    operations = [
        migrations.RunSQL(SQL, REVERSE),
    ]