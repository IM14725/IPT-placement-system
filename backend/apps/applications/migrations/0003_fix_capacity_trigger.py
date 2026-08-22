from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0002_paid_visibility_and_capacity_triggers"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                """
                CREATE OR REPLACE FUNCTION enforce_slot_capacity()
                RETURNS trigger AS $$
                DECLARE
                  cap INT;
                  cnt INT;
                BEGIN
                  SELECT capacity INTO cap FROM slots_slot WHERE id = NEW.slot_id;
                  IF cap IS NULL THEN
                    RETURN NEW;
                  END IF;
                  SELECT COUNT(*) INTO cnt FROM applications_application
                    WHERE slot_id = NEW.slot_id
                      AND status IN ('PAID_VERIFIED', 'ACCEPTED')
                      AND (TG_OP = 'INSERT' OR id <> NEW.id);
                  IF NEW.status IN ('PAID_VERIFIED', 'ACCEPTED') THEN
                    cnt := cnt + 1;
                  END IF;
                  IF cnt > cap THEN
                    RAISE EXCEPTION 'Slot % is at full capacity (%/%)',
                      NEW.slot_id, cnt, cap;
                  END IF;
                  RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                """,
                """
                DROP TRIGGER IF EXISTS trg_slot_capacity ON applications_application;
                CREATE TRIGGER trg_slot_capacity
                  BEFORE INSERT OR UPDATE OF status ON applications_application
                  FOR EACH ROW EXECUTE FUNCTION enforce_slot_capacity();
                """,
            ],
            reverse_sql=[
                """
                DROP TRIGGER IF EXISTS trg_slot_capacity ON applications_application;
                DROP FUNCTION IF EXISTS enforce_slot_capacity();
                """,
            ],
        ),
    ]