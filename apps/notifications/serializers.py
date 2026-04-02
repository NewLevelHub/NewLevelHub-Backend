from rest_framework import serializers
from .models import Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'notification_type', 'title', 'body', 'url', 'is_read', 'read_at', 'created_at']
        read_only_fields = ['id', 'notification_type', 'title', 'body', 'url', 'created_at']


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
