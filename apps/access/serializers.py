from rest_framework import serializers
from datetime import timedelta
import logging
from django.utils import timezone

from apps.core.exceptions import raise_validation_error
from .models import GuestPass, AccessLog
from .qr_image import generate_guest_pass_qr_image
from . import tasks
from apps.users.models import User

logger = logging.getLogger(__name__)

# Plans that grant employees unlimited guest passes per invitee email.
# Plans not in this set (e.g. 'basic') enforce a limit of 2 active passes
# per guest email within the company.
PLANS_WITHOUT_GUEST_LIMIT = {'standard', 'premium'}


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
            raise_validation_error('valid_from', 'access.valid_from_in_past')

        if valid_until <= valid_from:
            raise_validation_error('valid_until', 'access.valid_until_before_valid_from')

        if valid_until > valid_from + timedelta(days=30):
            raise_validation_error('valid_until', 'access.valid_until_too_far')

        if not attrs['is_single_use'] and valid_until > valid_from + timedelta(days=1):
            raise_validation_error('valid_until', 'access.multi_use_max_one_day')

        if attrs['guest_email'].strip().lower() == user.email.strip().lower():
            raise_validation_error('guest_email', 'access.cannot_create_for_self')

        existing_user = User.objects.filter(email__iexact=attrs['guest_email'].strip()).first()
        if existing_user and existing_user.role != 'guest':
            raise_validation_error('guest_email', 'access.cannot_create_for_employee')

        if user.role == 'guest':
            # Guests may have at most 2 active passes total (across all invitees).
            active_count = GuestPass.objects.filter(
                created_by=user, status='active',
            ).count()
            if active_count >= 2:
                raise_validation_error('non_field_errors', 'access.guest_max_passes_reached')
        elif user.role not in ('superadmin', 'company_admin', 'service_manager', 'reception'):
            # Employees: max 2 active passes per invitee email within the company,
            # unless the company is on a plan that lifts this restriction.
            company = user.company
            company_plan = getattr(company, 'plan', 'basic') if company else 'basic'
            if company_plan not in PLANS_WITHOUT_GUEST_LIMIT:
                active_count = GuestPass.objects.filter(
                    company=company,
                    guest_email=attrs['guest_email'],
                    status='active',
                ).count()
                if active_count >= 2:
                    raise_validation_error('guest_email', 'access.guest_max_passes_reached')

        return attrs

    def create(self, validated_data):
        user = self.context['request'].user
        is_single_use = validated_data.pop('is_single_use')
        validated_data['usage_type'] = 'single' if is_single_use else 'multi'
        validated_data['created_by'] = user
        validated_data['company'] = user.company
        guest_pass = super().create(validated_data)

        generate_guest_pass_qr_image(guest_pass)

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
    last_validated_at = serializers.SerializerMethodField()
    last_validated_by = serializers.SerializerMethodField()
    last_method = serializers.SerializerMethodField()

    class Meta:
        model = GuestPass
        fields = [
            'id', 'created_by', 'created_by_name', 'created_by_email', 'created_by_company_name', 'company',
            'guest_name', 'guest_email', 'guest_phone', 'purpose',
            'qr_code', 'qr_image', 'status', 'usage_type', 'is_single_use', 'times_used',
            'valid_from', 'valid_until', 'is_valid',
            'created_at',
            'last_validated_at', 'last_validated_by', 'last_method',
        ]
        read_only_fields = ['id', 'created_by', 'company', 'qr_code', 'qr_image', 'times_used', 'created_at']

    def get_is_single_use(self, obj):
        return obj.usage_type == 'single'

    def _last_log(self, obj):
        logs = getattr(obj, 'prefetched_logs', None)
        if logs is not None:
            return logs[0] if logs else None
        return obj.access_logs.select_related('checked_by').order_by('-created_at').first()

    def get_last_validated_at(self, obj):
        log = self._last_log(obj)
        return log.created_at.isoformat() if log else None

    def get_last_validated_by(self, obj):
        log = self._last_log(obj)
        if log and log.checked_by:
            return log.checked_by.full_name
        return None

    def get_last_method(self, obj):
        log = self._last_log(obj)
        return log.method if log else None


class GuestPassValidateSerializer(serializers.Serializer):
    qr_code = serializers.UUIDField()


class GuestPassValidationLogSerializer(serializers.ModelSerializer):
    validated_at = serializers.DateTimeField(source='created_at', read_only=True)
    validated_by = serializers.SerializerMethodField()

    class Meta:
        model = AccessLog
        fields = ['id', 'validated_at', 'validated_by', 'method', 'entry_point']
        read_only_fields = fields

    def get_validated_by(self, obj):
        return obj.checked_by.full_name if obj.checked_by else None


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
