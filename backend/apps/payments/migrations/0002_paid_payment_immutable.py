from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION ipt_payment_paid_immutable() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    IF OLD.is_paid THEN
                        RAISE EXCEPTION 'Paid payment cannot be deleted';
                    END IF;
                    RETURN OLD;
                END IF;
                IF OLD.is_paid THEN
                    IF NEW.reference_id IS DISTINCT FROM OLD.reference_id OR
                       NEW.student_id IS DISTINCT FROM OLD.student_id OR
                       NEW.application_id IS DISTINCT FROM OLD.application_id OR
                       NEW.amount IS DISTINCT FROM OLD.amount OR
                       NEW.currency IS DISTINCT FROM OLD.currency OR
                       NEW.method IS DISTINCT FROM OLD.method OR
                       NEW.gateway IS DISTINCT FROM OLD.gateway OR
                       NEW.gateway_txn_id IS DISTINCT FROM OLD.gateway_txn_id OR
                       NEW.status IS DISTINCT FROM OLD.status OR
                       NEW.is_paid IS DISTINCT FROM OLD.is_paid OR
                       NEW.paid_at IS DISTINCT FROM OLD.paid_at THEN
                        RAISE EXCEPTION 'Paid payment financial fields are immutable';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER ipt_payment_paid_immutable_trg
            BEFORE UPDATE OR DELETE ON payments_payment
            FOR EACH ROW EXECUTE FUNCTION ipt_payment_paid_immutable();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS ipt_payment_paid_immutable_trg ON payments_payment;
            DROP FUNCTION IF EXISTS ipt_payment_paid_immutable();
            """,
        ),
    ]
