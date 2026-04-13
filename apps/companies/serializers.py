from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers
from apps.users.models import User
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
    """Detail serializer — includes employee_count and storage_used (bytes)."""
    employee_count = serializers.SerializerMethodField()
    storage_used = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = [
            'id', 'name', 'description', 'logo', 'floor', 'office_number',
            'contact_email', 'contact_phone',
            'plan', 'max_employees', 'storage_limit_gb', 'max_boards',
            'is_active', 'working_hours_start', 'working_hours_end',
            'employee_count', 'storage_used', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_employee_count(self, obj):
        return obj.members.filter(is_active=True).count()

    def get_storage_used(self, obj):
        """Return total file_size (bytes) used by this company's files."""
        result = obj.files.aggregate(total=Sum('file_size'))
        return result['total'] or 0


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


class CompanySettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanySettings
        fields = [
            'custom_task_categories', 'custom_labels',
            'vacation_days_per_year', 'onboarding_enabled', 'brand_primary_color',
        ]


class InvitationCreateSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(choices=['employee', 'company_admin'])

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
        if value == 'company_admin' and request.user.role != 'superadmin':
            raise serializers.ValidationError('Only superadmin can invite company_admin.')
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
        if request.user.role == 'company_admin' and role == 'company_admin':
            raise serializers.ValidationError(
                {'role': 'Company admins cannot invite other company admins.'},
            )

        return attrs

    def create(self, validated_data):
        company = self.context['company']
        validated_data['company'] = company
        validated_data['invited_by'] = self.context['request'].user
        invitation = super().create(validated_data)
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
