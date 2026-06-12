from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0007_leaverequest_assigned_reviewer_and_more'),
    ]

    operations = [
        # SeparateDatabaseAndState lets us reconcile a column that already
        # exists in the test DB (added by a now-deleted migration) without
        # crashing on "column already exists".
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE hr_onboarding_steps
                        ADD COLUMN IF NOT EXISTS is_system boolean NOT NULL DEFAULT false;
                    """,
                    reverse_sql="""
                        ALTER TABLE hr_onboarding_steps
                        DROP COLUMN IF EXISTS is_system;
                    """,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='onboardingstep',
                    name='is_system',
                    field=models.BooleanField(default=False),
                ),
            ],
        ),
    ]
