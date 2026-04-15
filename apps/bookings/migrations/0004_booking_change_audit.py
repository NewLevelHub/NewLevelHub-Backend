from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('bookings', '0003_booking_cancellation_audit'),
    ]

    operations = [
        migrations.CreateModel(
            name='BookingChangeAudit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'action',
                    models.CharField(
                        choices=[
                            ('time_updated', 'Time Updated'),
                            ('participants_added', 'Participants Added'),
                            ('participant_removed', 'Participant Removed'),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('changed_at', models.DateTimeField(db_index=True)),
                (
                    'booking',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='change_audits',
                        to='bookings.booking',
                    ),
                ),
                (
                    'changed_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='booking_change_audits',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'db_table': 'booking_change_audits',
            },
        ),
        migrations.AddIndex(
            model_name='bookingchangeaudit',
            index=models.Index(fields=['booking', 'changed_at'], name='booking_cha_booking_76d5df_idx'),
        ),
        migrations.AddIndex(
            model_name='bookingchangeaudit',
            index=models.Index(fields=['changed_by', 'changed_at'], name='booking_cha_changed_903f85_idx'),
        ),
    ]
