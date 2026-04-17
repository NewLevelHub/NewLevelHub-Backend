from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0005_merge_0004_booking_branches'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='recurring_booking',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name='bookings',
                to='bookings.recurringbooking',
            ),
        ),
    ]
