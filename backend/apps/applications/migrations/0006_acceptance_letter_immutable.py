from django.db import migrations


class Migration(migrations.Migration):
    """An acceptance letter can never be edited or deleted; only its 'current' flag may be switched off
    when a replacement is uploaded. This holds for any write path, not just the ORM."""

    dependencies = [("applications", "0005_acceptance_letter")]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION ipt_acceptance_letter_guard() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'An acceptance letter cannot be deleted';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.application_id IS DISTINCT FROM OLD.application_id
                   OR NEW.version IS DISTINCT FROM OLD.version
                   OR NEW.file IS DISTINCT FROM OLD.file
                   OR NEW.original_name IS DISTINCT FROM OLD.original_name
                   OR NEW.sha256 IS DISTINCT FROM OLD.sha256
                   OR NEW.source_sha256 IS DISTINCT FROM OLD.source_sha256
                   OR NEW.page_count IS DISTINCT FROM OLD.page_count
                   OR NEW.uploaded_by_label IS DISTINCT FROM OLD.uploaded_by_label
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR (NEW.uploaded_by_id IS DISTINCT FROM OLD.uploaded_by_id AND NEW.uploaded_by_id IS NOT NULL) THEN
                    RAISE EXCEPTION 'An acceptance letter cannot be edited';
                END IF;
                IF OLD.is_current = FALSE AND NEW.is_current = TRUE THEN
                    RAISE EXCEPTION 'A replaced acceptance letter cannot become current again';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER ipt_acceptance_letter_guard_trg
            BEFORE UPDATE OR DELETE ON applications_acceptanceletter
            FOR EACH ROW EXECUTE FUNCTION ipt_acceptance_letter_guard();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS ipt_acceptance_letter_guard_trg ON applications_acceptanceletter;
            DROP FUNCTION IF EXISTS ipt_acceptance_letter_guard();
            """,
        ),
    ]
