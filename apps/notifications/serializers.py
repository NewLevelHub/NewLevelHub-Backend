from rest_framework import serializers
from .models import Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    """
    Serializes a Notification instance.

    Field mapping (API name → model field):
      type     → notification_type
      message  → body
      link     → url
    """
    type = serializers.CharField(source='notification_type', read_only=True)
    message = serializers.CharField(source='body', read_only=True)
    link = serializers.CharField(source='url', read_only=True, allow_null=True)

    class Meta:
        model = Notification
        fields = ['id', 'type', 'title', 'message', 'link', 'is_read', 'created_at']
        read_only_fields = ['id', 'type', 'title', 'message', 'link', 'created_at']


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = [
            'booking_in_app', 'booking_email',
            'task_in_app', 'task_email',
            'access_in_app', 'access_email',
            'service_in_app', 'service_email',
            'announcement_in_app', 'announcement_email',
            'hr_in_app', 'hr_email',
            'do_not_disturb',
        ]


class UnreadCountSerializer(serializers.Serializer):
    count = serializers.IntegerField()
