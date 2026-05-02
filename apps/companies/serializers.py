from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers
from apps.users.models import User
from apps.notifications.utils import create_notification

from .limits import notify_company_admins_limit_thresholds
from .models import Company, CompanySettings, Invitation
from .tasks import send_invitation_email


class CompanySerializer(serializers.ModelSerializer):
    """Lightweight serializer used for list responses."""
    employee_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Company
        fields = [
            'id', 'name', 'description', 'logo', 'floor', 'office_number',
            'contact_email', 'contact_phone',
            'plan', 'max_employees', 'storage_limit_gb', 'max_boards',
            'is_active', 'working_hours_start', 'working_hours_end',
            'employee_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class CompanyDetailSerializer(serializers.ModelSerializer):
    """Detail serializer — includes employee_count, storage_used (bytes), and onboarding_completed."""
    employee_count = serializers.SerializerMethodField()
    storage_used = serializers.SerializerMethodField()
    onboarding_completed = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = [
            'id', 'name', 'description', 'logo', 'floor', 'office_number',
            'contact_email', 'contact_phone',
            'plan', 'max_employees', 'storage_limit_gb', 'max_boards',
            'is_active', 'working_hours_start', 'working_hours_end',
            'employee_count', 'storage_used', 'onboarding_completed',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_employee_count(self, obj):
        return obj.members.filter(is_active=True).count()

    def get_storage_used(self, obj):
        """Return total file_size (bytes) used by this company's files."""
        result = obj.files.aggregate(total=Sum('file_size'))
        return result['total'] or 0

    def get_onboarding_completed(self, obj):
        try:
            return obj.settings.onboarding_completed
        except CompanySettings.DoesNotExist:
            return False


class CompanyCreateSerializer(serializers.ModelSerializer):
    """Used only by superadmin to create a new company (POST)."""

    class Meta:
        model = Company
        fields = [
            'id', 'name', 'description', 'logo', 'floor', 'office_number',
            'contact_email', 'contact_phone',
            'plan', 'max_employees', 'storage_limit_gb', 'max_boards',
        ]
        read_only_fields = ['id']

    def create(self, validated_data):
        plan = validated_data.get('plan', 'basic')
        plan_defaults = Company.PLAN_DEFAULT_LIMITS.get(plan, Company.PLAN_DEFAULT_LIMITS['basic'])

        for field_name in ('max_employees', 'max_boards', 'storage_limit_gb'):
            if field_name not in validated_data:
                validated_data[field_name] = plan_defaults[field_name]

        return super().create(validated_data)


class CompanyUpdateSerializer(serializers.ModelSerializer):
    """PATCH/PUT serializer for superadmin — all writable Company fields, no CompanySettings."""

    class Meta:
        model = Company
        fields = [
            'name', 'description', 'logo', 'floor', 'office_number',
            'contact_email', 'contact_phone',
            'plan', 'max_employees', 'storage_limit_gb', 'max_boards',
        ]


class CompanyAdminUpdateSerializer(serializers.ModelSerializer):
    """Restricted PATCH serializer for company_admin — subset of fields only."""

    class Meta:
        model = Company
        fields = ['name', 'description', 'logo', 'contact_email', 'contact_phone']


class WorkingHoursSerializer(serializers.Serializer):
    """Nested serializer for working_hours: {start, end} as time strings (HH:MM)."""
    start = serializers.TimeField(format='%H:%M', input_formats=['%H:%M', '%H:%M:%S'])
    end = serializers.TimeField(format='%H:%M', input_formats=['%H:%M', '%H:%M:%S'])

    def validate(self, attrs):
        if attrs['start'] >= attrs['end']:
            raise serializers.ValidationError(
                'working_hours.start must be earlier than working_hours.end.'
            )
        return attrs


HEX_COLOR_REGEX = r'^#([A-Fa-f0-9]{3}|[A-Fa-f0-9]{6})$'
HEX_COLOR_ERROR = 'Must be a valid hex color, e.g. #RGB or #RRGGBB.'


class CompanySettingsSerializer(serializers.ModelSerializer):
    custom_task_categories = serializers.ListField(
        child=serializers.CharField(max_length=100),
        max_length=50,
        required=False,
    )
    # custom_labels is stored as raw JSON; we validate its shape and colors
    # in validate_custom_labels rather than using a nested serializer as child,
    # which avoids attribute-vs-dict access issues during serialization of stored data.
    custom_labels = serializers.ListField(
        child=serializers.DictField(),
        max_length=30,
        required=False,
    )
    vacation_days_per_year = serializers.IntegerField(min_value=0, required=False)
    onboarding_enabled = serializers.BooleanField(required=False)
    brand_primary_color = serializers.RegexField(
        regex=HEX_COLOR_REGEX,
        required=False,
        allow_null=True,
        allow_blank=True,
        error_messages={'invalid': HEX_COLOR_ERROR},
    )
    working_hours = serializers.SerializerMethodField()

    class Meta:
        model = CompanySettings
        fields = [
            'custom_task_categories', 'custom_labels',
            'vacation_days_per_year', 'onboarding_enabled', 'brand_primary_color',
            'working_hours',
        ]

    def get_working_hours(self, obj):
        company = obj.company
        return {
            'start': company.working_hours_start.strftime('%H:%M') if company.working_hours_start else None,
            'end': company.working_hours_end.strftime('%H:%M') if company.working_hours_end else None,
        }

    def validate_custom_labels(self, value):
        """Validate each label has {name: str, color: valid hex}."""
        import re
        hex_re = re.compile(HEX_COLOR_REGEX)
        errors = {}
        for idx, item in enumerate(value):
            item_errors = {}
            if 'name' not in item or not isinstance(item.get('name'), str) or not item['name']:
                item_errors['name'] = 'This field is required.'
            if 'color' not in item:
                item_errors['color'] = 'This field is required.'
            elif not hex_re.match(str(item['color'])):
                item_errors['color'] = HEX_COLOR_ERROR
            if item_errors:
                errors[idx] = item_errors
        if errors:
            raise serializers.ValidationError(errors)
        return value

    def to_representation(self, instance):
        # Sanitize JSON fields before DRF field-level to_representation runs.
        # The DB may contain null or items of the wrong type (e.g. strings instead
        # of dicts in custom_labels) which would cause DictField / CharField to raise
        # AttributeError.  We normalise here so the read path is always safe.
        raw_labels = instance.custom_labels
        if not isinstance(raw_labels, list):
            instance.custom_labels = []
        else:
            instance.custom_labels = [item for item in raw_labels if isinstance(item, dict)]

        raw_categories = instance.custom_task_categories
        if not isinstance(raw_categories, list):
            instance.custom_task_categories = []
        else:
            instance.custom_task_categories = [
                item for item in raw_categories if isinstance(item, str)
            ]

        return super().to_representation(instance)

    def to_internal_value(self, data):
        # Handle working_hours nested object before the standard field processing.
        working_hours_data = data.get('working_hours') if hasattr(data, 'get') else None
        ret = super().to_internal_value(data)

        if working_hours_data is not None:
            wh_serializer = WorkingHoursSerializer(data=working_hours_data)
            wh_serializer.is_valid(raise_exception=True)
            ret['working_hours_start'] = wh_serializer.validated_data['start']
            ret['working_hours_end'] = wh_serializer.validated_data['end']

        return ret

    def update(self, instance, validated_data):
        # working_hours_start / working_hours_end are stored on Company, not CompanySettings.
        # Pop them here and persist them directly onto the related Company record.
        working_hours_start = validated_data.pop('working_hours_start', None)
        working_hours_end = validated_data.pop('working_hours_end', None)

        instance = super().update(instance, validated_data)

        if working_hours_start is not None:
            instance.company.working_hours_start = working_hours_start
            instance.company.working_hours_end = working_hours_end
            instance.company.save(update_fields=['working_hours_start', 'working_hours_end'])

        return instance


class InvitationCreateSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(choices=['employee', 'company_admin', 'reception'])

    class Meta:
        model = Invitation
        fields = ['email', 'role']

    def validate_email(self, value):
        email = value.strip().lower()
        company = self.context['company']

        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError('User with this email is already registered.')

        has_active_invitation = Invitation.objects.filter(
            company=company,
            email__iexact=email,
            is_used=False,
            expires_at__gt=timezone.now(),
        ).exists()
        if has_active_invitation:
            raise serializers.ValidationError('Active invitation for this email already exists.')

        return email

    def validate_role(self, value):
        request = self.context['request']
        if value in ('company_admin', 'reception') and request.user.role != 'superadmin':
            raise serializers.ValidationError(f'Only superadmin can invite {value}.')
        return value

    def validate(self, attrs):
        request = self.context['request']
        company = self.context['company']
        email = attrs['email'].strip()

        if company.members.filter(is_active=True).count() >= company.max_employees:
            raise serializers.ValidationError('Employee limit reached')

        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                {'email': 'A user with this email is already registered.'},
            )

        active_exists = Invitation.objects.filter(
            company=company,
            email__iexact=email,
            is_used=False,
            expires_at__gte=timezone.now(),
        ).exists()
        if active_exists:
            raise serializers.ValidationError(
                {'email': 'An active invitation already exists for this email.'},
            )

        role = attrs.get('role', 'employee')
        if request.user.role == 'company_admin' and role in ('company_admin', 'reception'):
            raise serializers.ValidationError(
                {'role': f'Company admins cannot invite {role}.'},
            )

        return attrs

    def create(self, validated_data):
        company = self.context['company']
        validated_data['company'] = company
        validated_data['invited_by'] = self.context['request'].user
        invitation = super().create(validated_data)
        create_notification(
            user=invitation.invited_by,
            notification_type='invitation',
            title='Приглашение отправлено',
            message=f'На адрес {invitation.email} отправлено приглашение.',
            link='/team/manage',
        )
        send_invitation_email.delay(invitation.id)
        current_employees = company.members.filter(is_active=True).count()
        notify_company_admins_limit_thresholds(
            company=company,
            metric='employees',
            current_value=current_employees,
            limit_value=company.max_employees,
        )
        return invitation


class InvitationListSerializer(serializers.ModelSerializer):
    invited_by_name = serializers.CharField(source='invited_by.full_name', read_only=True)

    class Meta:
        model = Invitation
        fields = [
            'id', 'email', 'role', 'token', 'invited_by_name',
            'is_used', 'is_expired', 'is_valid', 'expires_at', 'created_at',
        ]


class CompanyMemberSerializer(serializers.ModelSerializer):
    """Company member — read-only list representation."""
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'full_name', 'role', 'position',
            'avatar', 'is_active', 'date_joined', 'last_login',
        ]
        read_only_fields = fields


class CompanyDirectoryListSerializer(serializers.ModelSerializer):
    """Directory row for company team page."""
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id',
            'avatar',
            'full_name',
            'position',
            'email',
            'phone',
            'role',
            'is_active',
            'last_login',
        ]
        read_only_fields = fields


class CompanyDirectoryDetailSerializer(serializers.ModelSerializer):
    """Directory profile with activity counters."""
    full_name = serializers.CharField(read_only=True)
    tasks_count = serializers.IntegerField(read_only=True)
    bookings_last_30_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id',
            'avatar',
            'full_name',
            'position',
            'email',
            'phone',
            'role',
            'is_active',
            'last_login',
            'tasks_count',
            'bookings_last_30_days',
        ]
        read_only_fields = fields


class CompanyMemberActivitySerializer(serializers.Serializer):
    """Activity summary for a single company member."""
    last_login = serializers.DateTimeField(allow_null=True)
    active_tasks_count = serializers.IntegerField()
    completed_tasks_count = serializers.IntegerField()
    bookings_last_30_days = serializers.IntegerField()


class MemberDeactivateSerializer(serializers.Serializer):
    """Request body for deactivating a member (currently no required fields)."""
    pass


class MemberRemoveSerializer(serializers.Serializer):
    """Request body for removing a member from the company."""
    reassign_to = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text='User ID to reassign tasks to. If omitted, tasks become unassigned.',
    )


class OnboardingStepSerializer(serializers.Serializer):
    """A single onboarding step."""
    key = serializers.CharField()
    title = serializers.CharField()
    completed = serializers.BooleanField()


class OnboardingStatusSerializer(serializers.Serializer):
    """Onboarding status for a company."""
    completed = serializers.BooleanField()
    steps = OnboardingStepSerializer(many=True)
