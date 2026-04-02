from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class Notification(TimeStampedModel):
    TYPE_CHOICES = [
        ('booking_confirmed', 'Booking Confirmed'),
        ('booking_reminder', 'Booking Reminder'),
        ('booking_cancelled', 'Booking Cancelled'),
        ('booking_completed', 'Booking Completed'),
        ('task_assigned', 'Task Assigned'),
        ('task_moved', 'Task Moved'),
        ('task_comment', 'Task Comment'),
        ('task_deadline_soon', 'Task Deadline Soon'),
        ('task_deadline_overdue', 'Task Deadline Overdue'),
        ('guest_arrived', 'Guest Arrived'),
        ('pass_expiring', 'Pass Expiring'),
        ('service_accepted', 'Service Request Accepted'),
        ('service_status', 'Service Request Status'),
        ('service_completed', 'Service Request Completed'),
        ('announcement_building', 'Building Announcement'),
        ('announcement_company', 'Company Announcement'),
        ('invite_received', 'Invitation Received'),
        ('leave_approved', 'Leave Approved'),
        ('leave_rejected', 'Leave Rejected'),
        ('new_employee', 'New Employee Joined'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=30, choices=TYPE_CHOICES, db_index=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default='')
    url = models.CharField(max_length=500, blank=True, default='', help_text='Frontend path to navigate')
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read', '-created_at']),
        ]

    def __str__(self):
        return f'{self.get_notification_type_display()} → {self.user.email}'


class NotificationPreference(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_preferences')

    booking_in_app = models.BooleanField(default=True)
    booking_email = models.BooleanField(default=True)
    task_in_app = models.BooleanField(default=True)
    task_email = models.BooleanField(default=False)
    access_in_app = models.BooleanField(default=True)
    access_email = models.BooleanField(default=True)
    service_in_app = models.BooleanField(default=True)
    service_email = models.BooleanField(default=False)
    announcement_in_app = models.BooleanField(default=True)
    announcement_email = models.BooleanField(default=False)
    hr_in_app = models.BooleanField(default=True)
    hr_email = models.BooleanField(default=True)

    do_not_disturb = models.BooleanField(default=False)

    class Meta:
        db_table = 'notification_preferences'
