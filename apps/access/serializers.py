from rest_framework import serializers
from datetime import timedelta
from io import BytesIO
import logging
from django.core.files.base import ContentFile
from django.utils import timezone
import qrcode

from .models import GuestPass, AccessLog
from . import tasks
from apps.users.models import User

logger = logging.getLogger(__name__)


class GuestPassCreateSerializer(serializers.ModelSerializer):
    purpose = serializers.CharField(source='visit_purpose')
    is_single_use = serializers.BooleanField(write_only=True)
    valid_from = serializers.DateTimeField(required=False)

    class Meta:
        model = GuestPass
        fields = [
            'guest_name', 'guest_email', 'guest_phone', 'purpose',
            'valid_from', 'valid_until', 'is_single_use',
        ]

    def validate(self, attrs):
        request = self.context['request']
        user = request.user
        now = timezone.now()

        # Default valid_from to now if not provided
        valid_from = attrs.get('valid_from', now)
        attrs['valid_from'] = valid_from

        valid_until = attrs['valid_until']

        # Only reject if explicitly more than 60 seconds in the past (handles minor clock drift)
        if valid_from < now - timedelta(seconds=60):
            raise serializers.ValidationError({'valid_from': 'Cannot be more than 60 seconds in the past.'})

        if valid_until <= valid_from:
            raise serializers.ValidationError({'valid_until': 'Must be later than valid_from.'})

        if valid_until > valid_from + timedelta(days=30):
            raise serializers.ValidationError({'valid_until': 'Cannot be more than 30 days from valid_from.'})

        if attrs['guest_email'].strip().lower() == user.email.strip().lower():
            raise serializers.ValidationError({'guest_email': 'Cannot create a guest pass for yourself.'})

        existing_user = User.objects.filter(email__iexact=attrs['guest_email'].strip()).first()
        if existing_user and existing_user.role != 'guest':
            raise serializers.ValidationError(
                {'guest_email': 'Cannot create a guest pass for company member accounts.'}
            )

        active_count = GuestPass.objects.filter(
            company=user.company,
            guest_email=attrs['guest_email'],
            status='active',
        ).count()
        if active_count >= 2:
            raise serializers.ValidationError({'guest_email': 'Guest can have at most 2 active passes.'})

        if user.role == 'guest':
            raise serializers.ValidationError('Guests cannot create passes.')

        return attrs

    def _generate_qr_image(self, guest_pass):
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(str(guest_pass.qr_code))
        qr.make(fit=True)
        image = qr.make_image(fill_color='black', back_color='white')
        image_buffer = BytesIO()
        image.save(image_buffer, format='PNG')
        image_buffer.seek(0)
        image_name = f'{guest_pass.qr_code}.png'
        guest_pass.qr_image.save(image_name, ContentFile(image_buffer.read()), save=False)

    def create(self, validated_data):
        user = self.context['request'].user
        is_single_use = validated_data.pop('is_single_use')
        validated_data['usage_type'] = 'single' if is_single_use else 'multi'
        validated_data['created_by'] = user
        validated_data['company'] = user.company
        guest_pass = super().create(validated_data)

        self._generate_qr_image(guest_pass)
        guest_pass.save(update_fields=['qr_image'])

        try:
            tasks.send_guest_pass_email.delay(guest_pass.id)
        except Exception:
            # Do not fail pass creation when broker/result backend is unavailable.
            logger.exception('Failed to enqueue guest pass email task for pass_id=%s', guest_pass.id)
        return guest_pass


class GuestPassSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True)
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)
    created_by_company_name = serializers.CharField(source='created_by.company.name', read_only=True, allow_null=True)
    is_valid = serializers.BooleanField(read_only=True)
    purpose = serializers.CharField(source='visit_purpose', read_only=True)
    is_single_use = serializers.SerializerMethodField()
    qr_image = serializers.ImageField(read_only=True)

    class Meta:
        model = GuestPass
        fields = [
            'id', 'created_by', 'created_by_name', 'created_by_email', 'created_by_company_name', 'company',
            'guest_name', 'guest_email', 'guest_phone', 'purpose',
            'qr_code', 'qr_image', 'status', 'usage_type', 'is_single_use', 'times_used',
            'valid_from', 'valid_until', 'is_valid',
            'created_at',
        ]
        read_only_fields = ['id', 'created_by', 'company', 'qr_code', 'qr_image', 'times_used', 'created_at']

    def get_is_single_use(self, obj):
        return obj.usage_type == 'single'


class GuestPassValidateSerializer(serializers.Serializer):
    qr_code = serializers.UUIDField()


class AccessLogSerializer(serializers.ModelSerializer):
    invited_by = serializers.SerializerMethodField()
    validated_at = serializers.DateTimeField(source='created_at', read_only=True)
    validated_by = serializers.SerializerMethodField()

    class Meta:
        model = AccessLog
        fields = [
            'id', 'guest_pass', 'user', 'checked_by',
            'entry_point', 'method', 'is_entry', 'created_at',
            'invited_by', 'validated_at', 'validated_by',
        ]
        read_only_fields = ['id', 'created_at']

    def get_invited_by(self, obj):
        if obj.guest_pass and obj.guest_pass.created_by:
            return obj.guest_pass.created_by.full_name
        return None

    def get_validated_by(self, obj):
        if obj.checked_by:
            return obj.checked_by.full_name
        return None
