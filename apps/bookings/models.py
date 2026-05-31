from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class Resource(TimeStampedModel):
    TYPE_CHOICES = [
        ('desk', 'Desk'),
        ('meeting_room', 'Meeting Room'),
        ('parking', 'Parking'),
        ('capsule', 'Capsule'),
    ]

    name = models.CharField(max_length=255)
    resource_type = models.CharField(max_length=20, choices=TYPE_CHOICES, db_index=True)
    floor = models.PositiveIntegerField(default=1)
    floor_fk = models.ForeignKey(
        'services.Floor',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resources',
    )
    zone = models.CharField(max_length=100, blank=True, default='')
    description = models.TextField(blank=True, default='')
    photo = models.ImageField(upload_to='resources/', null=True, blank=True)

    capacity = models.PositiveIntegerField(default=1)

    # Оборудование (meeting rooms / desks)
    has_projector = models.BooleanField(default=False)
    has_tv = models.BooleanField(default=False)
    has_whiteboard = models.BooleanField(default=False)
    has_video_conf = models.BooleanField(default=False)
    has_monitor = models.BooleanField(default=False)
    has_dock = models.BooleanField(default=False)
    has_power_outlet = models.BooleanField(default=True)

    # Правила бронирования
    min_duration_minutes = models.PositiveIntegerField(default=30)
    max_duration_minutes = models.PositiveIntegerField(default=480)
    advance_booking_days = models.PositiveIntegerField(default=14)
    min_cancel_minutes = models.PositiveIntegerField(default=30)

    # Расписание доступности
    available_days = models.JSONField(default=list, help_text='[0,1,2,3,4] Mon=0..Sun=6')
    available_from = models.TimeField(default='08:00')
    available_until = models.TimeField(default='22:00')

    # Назначение
    is_hot_desk = models.BooleanField(default=True)
    assigned_company = models.ForeignKey(
        'companies.Company', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assigned_resources',
    )

    # Парковка
    is_vip = models.BooleanField(default=False)

    # Капсулы
    CAPSULE_ZONE_CHOICES = [('quiet', 'Quiet'), ('regular', 'Regular')]
    capsule_zone = models.CharField(max_length=10, choices=CAPSULE_ZONE_CHOICES, blank=True, default='')

    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'resources'
        indexes = [
            models.Index(fields=['resource_type', 'is_active']),
            models.Index(fields=['floor', 'is_active']),
        ]

    def __str__(self):
        return f'{self.name} ({self.get_resource_type_display()})'


class ResourcePhoto(TimeStampedModel):
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to='resources/photos/')

    class Meta:
        db_table = 'resource_photos'
        ordering = ['created_at']

    def __str__(self):
        return f'Photo #{self.pk} for {self.resource_id}'


class Booking(TimeStampedModel):
    STATUS_CHOICES = [
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
        ('no_show', 'No Show'),
    ]

    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name='bookings')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bookings')
    company = models.ForeignKey(
        'companies.Company', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='bookings',
    )
    recurring_booking = models.ForeignKey(
        'bookings.RecurringBooking',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bookings',
    )

    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='confirmed', db_index=True)
    description = models.TextField(blank=True, default='')

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='cancelled_bookings',
    )
    cancel_reason = models.TextField(blank=True, default='')
    reminder_sent = models.BooleanField(default=False, db_index=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'bookings'
        indexes = [
            models.Index(fields=['resource', 'start_time', 'end_time']),
            models.Index(fields=['user', 'status']),
        ]

    def __str__(self):
        return f'{self.resource.name}: {self.start_time:%Y-%m-%d %H:%M} – {self.end_time:%H:%M}'


class BookingCancellationAudit(TimeStampedModel):
    """Immutable audit row for every successful booking cancellation."""

    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name='cancellation_audits',
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='booking_cancellation_audits',
    )
    cancel_reason = models.TextField(blank=True, default='')
    cancelled_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'booking_cancellation_audits'
        indexes = [
            models.Index(fields=['booking', 'cancelled_at']),
            models.Index(fields=['cancelled_by', 'cancelled_at']),
        ]


class BookingChangeAudit(TimeStampedModel):
    """Immutable audit row for booking updates and participants changes."""

    ACTION_TIME_UPDATED = 'time_updated'
    ACTION_PARTICIPANTS_ADDED = 'participants_added'
    ACTION_PARTICIPANT_REMOVED = 'participant_removed'

    ACTION_CHOICES = [
        (ACTION_TIME_UPDATED, 'Time Updated'),
        (ACTION_PARTICIPANTS_ADDED, 'Participants Added'),
        (ACTION_PARTICIPANT_REMOVED, 'Participant Removed'),
    ]

    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name='change_audits',
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='booking_change_audits',
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    changed_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'booking_change_audits'
        indexes = [
            models.Index(fields=['booking', 'changed_at']),
            models.Index(fields=['changed_by', 'changed_at']),
        ]


class BookingParticipant(TimeStampedModel):
    """Дополнительные участники (для конференц-залов)."""
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='participants')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='booking_participations')

    class Meta:
        db_table = 'booking_participants'
        unique_together = ['booking', 'user']


class RecurringBooking(TimeStampedModel):
    """Шаблон рекуррентного бронирования (каждый пн 10:00-11:00)."""
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name='recurring_bookings')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='recurring_bookings')
    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='recurring_bookings')

    day_of_week = models.PositiveIntegerField(help_text='0=Mon .. 6=Sun')
    start_time = models.TimeField()
    end_time = models.TimeField()

    is_active = models.BooleanField(default=True)
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)

    class Meta:
        db_table = 'recurring_bookings'


class ResourceBlock(TimeStampedModel):
    """Блокировка ресурса администрацией (ремонт, мероприятие)."""
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name='blocks')
    blocked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    reason = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'resource_blocks'
