from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('bookings', '0002_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='BookingCancellationAudit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('cancel_reason', models.TextField(blank=True, default='')),
                ('cancelled_at', models.DateTimeField(db_index=True)),
                (
                    'booking',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='cancellation_audits',
                        to='bookings.booking',
                    ),
                ),
                (
                    'cancelled_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='booking_cancellation_audits',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'db_table': 'booking_cancellation_audits',
            },
        ),
        migrations.AddIndex(
            model_name='bookingcancellationaudit',
            index=models.Index(fields=['booking', 'cancelled_at'], name='booking_can_booking_2bc640_idx'),
        ),
        migrations.AddIndex(
            model_name='bookingcancellationaudit',
            index=models.Index(fields=['cancelled_by', 'cancelled_at'], name='booking_can_cancell_5bcf22_idx'),
        ),
    ]
