from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class Notification(TimeStampedModel):
    TYPE_CHOICES = [
        # Booking
        ('booking_confirmed', 'Booking Confirmed'),
        ('booking_reminder', 'Booking Reminder'),
        ('booking_cancelled', 'Booking Cancelled'),
        ('booking_completed', 'Booking Completed'),
        # Task
        ('task_assigned', 'Task Assigned'),
        ('task_moved', 'Task Moved'),
        ('task_comment', 'Task Comment'),
        ('task_deadline', 'Task Deadline'),
        ('task_deadline_soon', 'Task Deadline Soon'),
        ('task_deadline_overdue', 'Task Deadline Overdue'),
        # Guest / Access
        ('guest_validated', 'Guest Validated'),
        ('guest_arrived', 'Guest Arrived'),
        ('guest_pass_expiring', 'Guest Pass Expiring'),
        ('pass_expiring', 'Pass Expiring'),
        # Service
        ('service_request_update', 'Service Request Update'),
        ('service_accepted', 'Service Request Accepted'),
        ('service_status', 'Service Request Status'),
        ('service_completed', 'Service Request Completed'),
        # Announcement
        ('announcement', 'Announcement'),
        ('announcement_building', 'Building Announcement'),
        ('announcement_company', 'Company Announcement'),
        # Invitations / HR
        ('invitation', 'Invitation'),
        ('invite_received', 'Invitation Received'),
        ('leave_review', 'Leave Review'),
        ('leave_approved', 'Leave Approved'),
        ('leave_rejected', 'Leave Rejected'),
        # Misc
        ('new_employee', 'New Employee Joined'),
        ('system', 'System'),
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
    """
    Per-user notification preference toggles.

    Design: opt-out vs opt-in defaults for email fields
    ---------------------------------------------------
    Opt-out by default (email default=True) — core transactional notifications
    users always expect to receive and rarely want to miss:
      - booking_confirmed_email   : booking creation confirmation
      - task_assigned_email       : assigned to a CRM task
      - invitation_email          : company invitation received
      - guest_validated_email     : guest pass approved (company_admin)
      - leave_review_email        : leave request pending review (company_admin)

    Opt-in by default (email default=False) — informational or potentially
    noisy notifications where email delivery would be excessive by default:
      - task_moved_email           : task column changed
      - task_comment_email         : comment added to a task
      - task_deadline_email        : task deadline approaching
      - service_request_update_email : service request status update
      - announcement_email         : company/building announcements

    All in_app toggles default to True so users always receive in-app
    notifications regardless of their email preference.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='notification_preferences',
    )

    # Per-type fields — each notification type has its own independent toggle.
    # Booking
    booking_confirmed_in_app = models.BooleanField(default=True)
    booking_confirmed_email = models.BooleanField(default=True)
    booking_reminder_in_app = models.BooleanField(default=True)
    booking_reminder_email = models.BooleanField(default=True)
    booking_cancelled_in_app = models.BooleanField(default=True)
    booking_cancelled_email = models.BooleanField(default=True)
    # Task
    task_assigned_in_app = models.BooleanField(default=True)
    task_assigned_email = models.BooleanField(default=True)
    task_moved_in_app = models.BooleanField(default=True)
    task_moved_email = models.BooleanField(default=False)
    task_comment_in_app = models.BooleanField(default=True)
    task_comment_email = models.BooleanField(default=False)
    task_deadline_in_app = models.BooleanField(default=True)
    task_deadline_email = models.BooleanField(default=False)
    # Guest / Access
    guest_validated_in_app = models.BooleanField(default=True)
    guest_validated_email = models.BooleanField(default=True)
    guest_pass_expiring_in_app = models.BooleanField(default=True)
    guest_pass_expiring_email = models.BooleanField(default=True)
    # Service
    service_request_update_in_app = models.BooleanField(default=True)
    service_request_update_email = models.BooleanField(default=False)
    # Announcement
    announcement_in_app = models.BooleanField(default=True)
    announcement_email = models.BooleanField(default=True)
    # HR
    invitation_in_app = models.BooleanField(default=True)
    invitation_email = models.BooleanField(default=True)
    leave_review_in_app = models.BooleanField(default=True)
    leave_review_email = models.BooleanField(default=True)
    new_employee_in_app = models.BooleanField(default=True)
    new_employee_email = models.BooleanField(default=False)
    # System
    system_in_app = models.BooleanField(default=True)
    system_email = models.BooleanField(default=False)

    # Legacy group fields — kept for backward compatibility but no longer used
    # by the preferences API. Will be removed in a future migration.
    booking_in_app = models.BooleanField(default=True)
    booking_email = models.BooleanField(default=True)
    task_in_app = models.BooleanField(default=True)
    task_email = models.BooleanField(default=False)
    access_in_app = models.BooleanField(default=True)
    access_email = models.BooleanField(default=True)
    service_in_app = models.BooleanField(default=True)
    service_email = models.BooleanField(default=False)
    hr_in_app = models.BooleanField(default=True)
    hr_email = models.BooleanField(default=True)

    do_not_disturb = models.BooleanField(default=False)
    dnd_enabled = models.BooleanField(default=False)
    dnd_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'notification_preferences'
