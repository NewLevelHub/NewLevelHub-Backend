from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


# ── Карта здания ──────────────────────────────────────────────────────

class Floor(TimeStampedModel):
    number = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=100, blank=True, default='')
    plan_image = models.ImageField(upload_to='floor_plans/', null=True, blank=True)

    class Meta:
        db_table = 'building_floors'
        ordering = ['number']

    def __str__(self):
        return self.name or f'Floor {self.number}'


class MapPoint(TimeStampedModel):
    POINT_TYPE_CHOICES = [
        ('desk', 'Desk'),
        ('meeting_room', 'Meeting Room'),
        ('parking', 'Parking'),
        ('capsule', 'Capsule'),
        ('toilet', 'Toilet'),
        ('kitchen', 'Kitchen'),
        ('elevator', 'Elevator'),
        ('exit', 'Exit'),
        ('office', 'Office'),
        ('other', 'Other'),
    ]

    floor = models.ForeignKey(Floor, on_delete=models.CASCADE, related_name='points')
    point_type = models.CharField(max_length=20, choices=POINT_TYPE_CHOICES)
    label = models.CharField(max_length=255, blank=True, default='')
    x = models.FloatField(help_text='X coordinate on floor plan (0-100%)')
    y = models.FloatField(help_text='Y coordinate on floor plan (0-100%)')

    resource = models.ForeignKey(
        'bookings.Resource', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='map_points',
    )
    company = models.ForeignKey(
        'companies.Company', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='map_points',
    )

    class Meta:
        db_table = 'building_map_points'


# ── Сервисные заявки ──────────────────────────────────────────────────

class ServiceRequest(TimeStampedModel):
    TYPE_CHOICES = [
        ('cleaning', 'Cleaning'),
        ('repair', 'Repair'),
        ('supplies', 'Supplies'),
        ('general', 'General'),
    ]
    STATUS_CHOICES = [
        ('new', 'New'),
        ('accepted', 'Accepted'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
    ]
    URGENCY_CHOICES = [
        ('normal', 'Normal'),
        ('urgent', 'Urgent'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='service_requests')
    request_type = models.CharField(max_length=20, choices=TYPE_CHOICES, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new', db_index=True)
    urgency = models.CharField(max_length=10, choices=URGENCY_CHOICES, default='normal')

    floor = models.PositiveIntegerField(null=True, blank=True)
    location = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField(blank=True, default='')
    photo = models.ImageField(upload_to='service_requests/', null=True, blank=True)

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assigned_service_requests',
    )

    rating = models.PositiveIntegerField(null=True, blank=True, help_text='1-5 stars')
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'service_requests'

    def __str__(self):
        return f'{self.get_request_type_display()} — {self.get_status_display()}'


# ── Лента объявлений ─────────────────────────────────────────────────

class Announcement(TimeStampedModel):
    SCOPE_CHOICES = [
        ('building', 'Building-wide'),
        ('company', 'Company internal'),
    ]
    CATEGORY_CHOICES = [
        ('info', 'Information'),
        ('important', 'Important'),
        ('event', 'Event'),
    ]

    # ``scope`` is now derived data — kept for legacy admin/list filters but
    # always recomputed from ``company`` on save.  ``company is None`` means a
    # building-wide (БЦ) announcement; a non-null company means a company feed.
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES, default='building')
    company = models.ForeignKey(
        'companies.Company', on_delete=models.CASCADE,
        null=True, blank=True, related_name='announcements',
    )
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    title = models.CharField(max_length=255)
    body = models.TextField()
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES, default='info')
    image = models.ImageField(upload_to='announcements/', null=True, blank=True)
    is_pinned = models.BooleanField(default=False)
    notify_email = models.BooleanField(default=False, help_text='Send email to all recipients')

    class Meta:
        db_table = 'announcements'
        ordering = ['-is_pinned', '-created_at']

    def save(self, *args, **kwargs):
        self.scope = 'company' if self.company_id else 'building'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class AnnouncementRead(TimeStampedModel):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name='reads')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        db_table = 'announcement_reads'
        unique_together = ['announcement', 'user']
