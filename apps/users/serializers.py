from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

User = get_user_model()


class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    Serializer for user registration.
    """
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ['email', 'phone', 'first_name', 'last_name', 'password', 'password_confirm', 'role']
        extra_kwargs = {
            'role': {'default': 'employee'},
        }

    def validate(self, attrs):
        """Validate that passwords match."""
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({"password": "Passwords do not match"})
        return attrs

    def create(self, validated_data):
        """Create user with hashed password."""
        validated_data.pop('password_confirm')
        user = User.objects.create_user(**validated_data)
        return user


class UserLoginSerializer(serializers.Serializer):
    """
    Serializer for user login.
    """
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True)


class UserSerializer(serializers.ModelSerializer):
    """
    Serializer for User model (read operations).
    """
    full_name = serializers.ReadOnlyField()

    class Meta:
        model = User
        fields = [
            'id', 'email', 'phone', 'first_name', 'last_name',
            'full_name', 'role', 'is_active', 'face_data_ref',
            'last_login', 'date_joined', 'created_at',
        ]
        read_only_fields = ['id', 'date_joined', 'created_at', 'last_login']


class UserUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for updating user profile.
    """
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'phone']


class PasswordResetRequestSerializer(serializers.Serializer):
    """
    Serializer for password reset request.
    """
    email = serializers.EmailField(required=True)


class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    Serializer for password reset confirmation.
    """
    token = serializers.CharField(required=True)
    password = serializers.CharField(required=True, validators=[validate_password])
    password_confirm = serializers.CharField(required=True)

    def validate(self, attrs):
        """Validate that passwords match."""
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({"password": "Passwords do not match"})
        return attrs


class MobileBiometricConfirmSerializer(serializers.Serializer):
    """
    Serializer for mobile biometric authentication confirmation.
    """
    user_id = serializers.IntegerField(required=True)
    biometric_token = serializers.CharField(required=True)
    device_id = serializers.CharField(required=True)


class UserRoleSerializer(serializers.Serializer):
    """Serializer for public user role reference endpoint."""
    code = serializers.CharField()
    label = serializers.CharField()
    description = serializers.CharField()
