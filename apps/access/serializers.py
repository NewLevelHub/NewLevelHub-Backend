from rest_framework import serializers
from .models import GuestPass, AccessLog


class GuestPassCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = GuestPass
        fields = [
            'guest_name', 'guest_email', 'guest_phone', 'visit_purpose',
            'usage_type', 'valid_from', 'valid_until',
        ]

    def create(self, validated_data):
        user = self.context['request'].user
        validated_data['created_by'] = user
        validated_data['company'] = user.company
        guest_pass = super().create(validated_data)
        # TODO: отправить QR-код на email гостя (Celery task)
        return guest_pass


class GuestPassSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True)
    is_valid = serializers.BooleanField(read_only=True)

    class Meta:
        model = GuestPass
        fields = [
            'id', 'created_by', 'created_by_name', 'company',
            'guest_name', 'guest_email', 'guest_phone', 'visit_purpose',
            'qr_code', 'status', 'usage_type', 'times_used',
            'valid_from', 'valid_until', 'is_valid',
            'created_at',
        ]
        read_only_fields = ['id', 'created_by', 'company', 'qr_code', 'times_used', 'created_at']


class GuestPassValidateSerializer(serializers.Serializer):
    qr_code = serializers.UUIDField()


class AccessLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccessLog
        fields = [
            'id', 'guest_pass', 'user', 'checked_by',
            'entry_point', 'method', 'is_entry', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']
