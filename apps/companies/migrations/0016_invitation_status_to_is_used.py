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
                ALTER TABLE invitations
                    ADD COLUMN IF NOT EXISTS is_used BOOLEAN NOT NULL DEFAULT FALSE;
                UPDATE invitations SET is_used = TRUE WHERE status = 'accepted';
                ALTER TABLE invitations DROP COLUMN IF EXISTS status;
            """,
            reverse_sql="""
                ALTER TABLE invitations
                    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'pending';
                UPDATE invitations SET status = 'accepted' WHERE is_used = TRUE;
                ALTER TABLE invitations DROP COLUMN IF EXISTS is_used;
            """,
        ),
    ]
