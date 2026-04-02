from rest_framework import serializers
from .models import Company, CompanySettings, Invitation


class CompanySerializer(serializers.ModelSerializer):
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


class CompanySettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanySettings
        fields = [
            'custom_task_categories', 'custom_labels',
            'vacation_days_per_year', 'onboarding_enabled', 'brand_primary_color',
        ]


class InvitationCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invitation
        fields = ['email', 'role']

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        validated_data['invited_by'] = self.context['request'].user
        invitation = super().create(validated_data)
        # TODO: отправить email с инвайт-ссылкой (Celery task)
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
