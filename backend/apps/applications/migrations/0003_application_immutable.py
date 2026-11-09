from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0002_application_can_cancel_alter_application_status"),
        ("payments", "0002_paid_payment_immutable"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION ipt_application_immutable() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    IF OLD.status = 'PAID' THEN
                        RAISE EXCEPTION 'Paid application cannot be deleted';
                    END IF;
                    IF OLD.is_accepted THEN
                        RAISE EXCEPTION 'Accepted application cannot be deleted';
                    END IF;
                    RETURN OLD;
                END IF;
                IF NEW.status = 'PAID' AND OLD.status <> 'PAID' THEN
                    IF NOT EXISTS (SELECT 1 FROM payments_payment WHERE application_id = NEW.id AND is_paid = TRUE) THEN
                        RAISE EXCEPTION 'Cannot mark application PAID without verified payment';
                    END IF;
                END IF;
                IF OLD.is_accepted THEN
                    RAISE EXCEPTION 'Accepted application is immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER ipt_application_immutable_trg
            BEFORE UPDATE OR DELETE ON applications_application
            FOR EACH ROW EXECUTE FUNCTION ipt_application_immutable();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS ipt_application_immutable_trg ON applications_application;
            DROP FUNCTION IF EXISTS ipt_application_immutable();
            """,
        ),
    ]
