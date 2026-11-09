from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION ipt_document_verified_immutable() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    IF OLD.is_verified THEN
                        RAISE EXCEPTION 'Verified document cannot be deleted';
                    END IF;
                    RETURN OLD;
                END IF;
                IF OLD.is_verified THEN
                    RAISE EXCEPTION 'Verified document is immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER ipt_document_verified_immutable_trg
            BEFORE UPDATE OR DELETE ON documents_document
            FOR EACH ROW EXECUTE FUNCTION ipt_document_verified_immutable();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS ipt_document_verified_immutable_trg ON documents_document;
            DROP FUNCTION IF EXISTS ipt_document_verified_immutable();
            """,
        ),
    ]
