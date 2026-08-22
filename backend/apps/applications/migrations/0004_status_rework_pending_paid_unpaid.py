from datetime import timedelta

from django.db import migrations, models

import apps.applications.models as app_models


def _map_statuses(apps, schema_editor):
    Application = apps.get_model("applications", "Application")
    for row in Application.objects.all():
        changed = False
        if row.status == "PENDING_PAYMENT":
            row.status = "PENDING"
            changed = True
        elif row.status == "PAID_VERIFIED":
            row.status = "PAID"
            changed = True
        elif row.status == "ACCEPTED":
            row.status = "PAID"
            row.is_accepted = True
            changed = True
        if row.payment_deadline is None and row.status == "PENDING":
            row.payment_deadline = row.created_at + timedelta(
                hours=app_models.PAYMENT_DEADLINE_HOURS
            )
            changed = True
        if changed:
            row.save(update_fields=["status", "is_accepted", "payment_deadline", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0003_fix_capacity_trigger"),
    ]

    operations = [
        # Drop legacy triggers first so the data migration below is not blocked.
        migrations.RunSQL(
            sql=[
                "DROP TRIGGER IF EXISTS trg_enforce_paid_before_visible ON applications_application;",
                "DROP FUNCTION IF EXISTS enforce_paid_before_visible();",
                "DROP TRIGGER IF EXISTS trg_slot_capacity ON applications_application;",
                "DROP FUNCTION IF EXISTS enforce_slot_capacity();",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddField(
            model_name="application",
            name="payment_deadline",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="application",
            name="is_accepted",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="application",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("PAID", "Paid"),
                    ("UNPAID", "Unpaid"),
                ],
                default="PENDING",
                max_length=20,
            ),
        ),
        migrations.RunPython(_map_statuses, migrations.RunPython.noop),
        migrations.RunSQL(
            sql=[
                """
                CREATE OR REPLACE FUNCTION enforce_paid_before_visible()
                RETURNS trigger AS $$
                BEGIN
                  IF NEW.status = 'PAID' THEN
                    IF NOT EXISTS (
                      SELECT 1 FROM payments_payment p
                      WHERE p.application_id = NEW.id AND p.is_paid = TRUE
                    ) THEN
                      RAISE EXCEPTION
                        'Application % cannot be PAID without a verified payment',
                        NEW.id;
                    END IF;
                  END IF;
                  RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                """,
                """
                DROP TRIGGER IF EXISTS trg_enforce_paid_before_visible
                    ON applications_application;
                CREATE TRIGGER trg_enforce_paid_before_visible
                  BEFORE INSERT OR UPDATE OF status ON applications_application
                  FOR EACH ROW EXECUTE FUNCTION enforce_paid_before_visible();
                """,
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
                      AND status IN ('PENDING', 'PAID')
                      AND (TG_OP = 'INSERT' OR id <> NEW.id);
                  IF NEW.status IN ('PENDING', 'PAID') THEN
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
                DROP TRIGGER IF EXISTS trg_enforce_paid_before_visible
                    ON applications_application;
                DROP FUNCTION IF EXISTS enforce_paid_before_visible();
                """,
                """
                DROP TRIGGER IF EXISTS trg_slot_capacity ON applications_application;
                DROP FUNCTION IF EXISTS enforce_slot_capacity();
                """,
            ],
        ),
    ]