from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION enforce_payment_immutable() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_paid THEN
            RAISE EXCEPTION 'Paid payment % is immutable', OLD.id;
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.is_paid AND (
        NEW.reference_id   IS DISTINCT FROM OLD.reference_id
        OR NEW.student_id     IS DISTINCT FROM OLD.student_id
        OR NEW.application_id IS DISTINCT FROM OLD.application_id
        OR NEW.amount         IS DISTINCT FROM OLD.amount
        OR NEW.currency       IS DISTINCT FROM OLD.currency
        OR NEW.method         IS DISTINCT FROM OLD.method
        OR NEW.gateway        IS DISTINCT FROM OLD.gateway
        OR NEW.gateway_txn_id IS DISTINCT FROM OLD.gateway_txn_id
        OR NEW.status         IS DISTINCT FROM OLD.status
        OR NEW.is_paid        IS DISTINCT FROM OLD.is_paid
        OR NEW.paid_at        IS DISTINCT FROM OLD.paid_at
    ) THEN
        RAISE EXCEPTION 'Paid payment % is immutable', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_payment_immutable
BEFORE UPDATE OR DELETE ON payments_payment
FOR EACH ROW EXECUTE FUNCTION enforce_payment_immutable();
"""

REVERSE = """
DROP TRIGGER IF EXISTS trg_payment_immutable ON payments_payment;
DROP FUNCTION IF EXISTS enforce_payment_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(SQL, REVERSE),
    ]