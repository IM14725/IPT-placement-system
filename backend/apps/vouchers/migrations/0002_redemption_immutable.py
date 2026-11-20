from django.db import migrations


class Migration(migrations.Migration):
    """Database-level guarantees for voucher redemptions, like the payment and application triggers.

    * a code can never be used more times than ``max_uses`` (PENDING + PAID applications hold a use),
      whatever path writes the row;
    * a redemption is never edited, and one that belongs to a PAID application is never deleted
      (the ledger's "discounts granted" figure must not change after the fact).
    """

    dependencies = [
        ("vouchers", "0001_initial"),
        ("applications", "0004_dashboard_features"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION ipt_voucher_redemption_guard() RETURNS trigger AS $$
            DECLARE
                allowed integer;
                used integer;
            BEGIN
                IF TG_OP = 'UPDATE' THEN
                    RAISE EXCEPTION 'Voucher redemptions are append-only';
                END IF;
                IF TG_OP = 'DELETE' THEN
                    IF EXISTS (
                        SELECT 1 FROM applications_application
                        WHERE id = OLD.application_id AND status = 'PAID'
                    ) THEN
                        RAISE EXCEPTION 'Redemption of a paid application cannot be deleted';
                    END IF;
                    RETURN OLD;
                END IF;
                -- INSERT: the code must still have a use left.
                SELECT max_uses INTO allowed FROM vouchers_feevoucher WHERE id = NEW.voucher_id;
                SELECT count(*) INTO used
                FROM vouchers_voucherredemption r
                JOIN applications_application a ON a.id = r.application_id
                WHERE r.voucher_id = NEW.voucher_id AND a.status IN ('PENDING', 'PAID');
                IF used >= allowed THEN
                    RAISE EXCEPTION 'Voucher usage limit reached';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER ipt_voucher_redemption_guard_trg
            BEFORE INSERT OR UPDATE OR DELETE ON vouchers_voucherredemption
            FOR EACH ROW EXECUTE FUNCTION ipt_voucher_redemption_guard();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS ipt_voucher_redemption_guard_trg ON vouchers_voucherredemption;
            DROP FUNCTION IF EXISTS ipt_voucher_redemption_guard();
            """,
        ),
    ]
