from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers
from apps.users.models import User
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
            'plan', 'max_employees', 'storage_limit_gb',
        ]
        read_only_fields = ['id']


class CompanyUpdateSerializer(serializers.ModelSerializer):
    """PATCH/PUT serializer for superadmin — all writable Company fields, no CompanySettings."""

    class Meta:
        model = Company
        fields = [
            'name', 'description', 'logo', 'floor', 'office_number',
            'contact_email', 'contact_phone',
            'plan', 'max_employees', 'storage_limit_gb',
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
        validated_data['company'] = self.context['company']
        validated_data['invited_by'] = self.context['request'].user
        invitation = super().create(validated_data)
        send_invitation_email.delay(invitation.id)
        return invitation


class InvitationListSerializer(serializers.ModelSerializer):
    invited_by_name = serializers.CharField(source='invited_by.full_name', read_only=True)

    class Meta:
        model = Invitation
        fields = [
            'id', 'email', 'role', 'token', 'invited_by_name',
            'is_used', 'is_expired', 'is_valid', 'expires_at', 'created_at',
        ]


class CompanyMemberSerializer(serializers.Serializer):
    """Сотрудник компании (read-only представление)."""
    id = serializers.IntegerField()
    email = serializers.EmailField()
    full_name = serializers.CharField()
    role = serializers.CharField()
    position = serializers.CharField()
    avatar = serializers.ImageField()
    is_active = serializers.BooleanField()
    date_joined = serializers.DateTimeField()
    last_login = serializers.DateTimeField()
