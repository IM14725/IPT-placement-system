from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION ipt_auditlog_append_only() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'AuditLog is append-only';
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER ipt_auditlog_no_update BEFORE UPDATE ON core_auditlog
            FOR EACH ROW EXECUTE FUNCTION ipt_auditlog_append_only();

            CREATE TRIGGER ipt_auditlog_no_delete BEFORE DELETE ON core_auditlog
            FOR EACH ROW EXECUTE FUNCTION ipt_auditlog_append_only();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS ipt_auditlog_no_update ON core_auditlog;
            DROP TRIGGER IF EXISTS ipt_auditlog_no_delete ON core_auditlog;
            DROP FUNCTION IF EXISTS ipt_auditlog_append_only();
            """,
        ),
    ]
