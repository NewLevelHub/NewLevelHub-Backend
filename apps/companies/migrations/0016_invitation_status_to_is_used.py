from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0015_merge_20260604_0257'),
    ]

    operations = [
        # Add is_used column (the status column may already be missing in some envs,
        # so we use IF NOT EXISTS / IF EXISTS guards).
        migrations.RunSQL(
            sql="""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'invitations' AND column_name = 'status'
                    ) THEN
                        ALTER TABLE invitations
                            ADD COLUMN IF NOT EXISTS is_used BOOLEAN NOT NULL DEFAULT FALSE;
                        UPDATE invitations SET is_used = TRUE WHERE status = 'accepted';
                        ALTER TABLE invitations DROP COLUMN IF EXISTS status;
                    END IF;
                END $$;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
