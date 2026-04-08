from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import User


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'phone', 'password', 'password_confirm']

    def validate(self, attrs):
        if attrs['password'] != attrs.pop('password_confirm'):
            raise serializers.ValidationError({'password_confirm': 'Passwords do not match'})
        return attrs

    def create(self, validated_data):
        validated_data.setdefault('is_email_verified', False)
        return User.objects.create_user(**validated_data)


class InviteRegistrationSerializer(serializers.ModelSerializer):
    """Регистрация сотрудника по инвайт-токену."""
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)
    invite_token = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'phone', 'password', 'password_confirm', 'invite_token']

    def validate(self, attrs):
        if attrs['password'] != attrs.pop('password_confirm'):
            raise serializers.ValidationError({'password_confirm': 'Passwords do not match'})
        # TODO: валидация invite_token — проверить существование, срок, одноразовость
        return attrs

    def create(self, validated_data):
        validated_data.pop('invite_token', None)
        # TODO: привязать пользователя к company из инвайта, проставить role='employee'
        return User.objects.create_user(**validated_data)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()

    def validate(self, attrs):
        user = authenticate(email=attrs['email'], password=attrs['password'])
        if not user:
            raise serializers.ValidationError('Invalid credentials')
        if not user.is_active:
            raise serializers.ValidationError('Account is deactivated')
        attrs['user'] = user
        return attrs


class UserProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    company_name = serializers.CharField(source='company.name', read_only=True, default=None)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'phone', 'position', 'avatar', 'role',
            'company', 'company_name',
            'is_email_verified', 'date_joined', 'last_login',
        ]
        read_only_fields = ['id', 'email', 'role', 'company', 'is_email_verified', 'date_joined', 'last_login']


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'phone', 'position', 'avatar']


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField(min_length=8)

    def validate_current_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Current password is incorrect')
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.UUIDField()
    new_password = serializers.CharField(min_length=8)

    def validate_new_password(self, value):
        try:
            validate_password(value)
        except ValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value


class EmailVerifySerializer(serializers.Serializer):
    token = serializers.UUIDField()


class CompanyBriefSerializer(serializers.Serializer):
    """Краткое представление компании для вложенных сериализаторов."""
    id = serializers.IntegerField()
    name = serializers.CharField()


class UserListSerializer(serializers.ModelSerializer):
    """Для списков пользователей (суперадмин)."""
    full_name = serializers.CharField(read_only=True)
    company = CompanyBriefSerializer(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'role', 'company', 'is_active', 'date_joined', 'last_login', 'avatar',
        ]


class UserDetailSerializer(serializers.ModelSerializer):
    """Полная информация о пользователе для суперадмина."""
    full_name = serializers.CharField(read_only=True)
    company = CompanyBriefSerializer(read_only=True)
    bookings_count = serializers.IntegerField(read_only=True)
    tasks_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'phone', 'position', 'avatar', 'role',
            'company',
            'is_active', 'is_email_verified',
            'date_joined', 'last_login',
            'bookings_count', 'tasks_count',
        ]
