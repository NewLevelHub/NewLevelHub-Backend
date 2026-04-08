import io
import os

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework import serializers

from .models import User


# ── Constants ─────────────────────────────────────────────────────────

AVATAR_ALLOWED_CONTENT_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
AVATAR_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
AVATAR_SIZE = (400, 400)


# ── Helpers ───────────────────────────────────────────────────────────

def _delete_file(field):
    """Delete the physical file referenced by an ImageField / FileField."""
    if field and hasattr(field, 'path'):
        try:
            if os.path.isfile(field.path):
                os.remove(field.path)
        except (OSError, ValueError):
            pass


def _resize_avatar(image_file):
    """
    Resize the uploaded file in-place to exactly 400×400 px.
    Uses thumbnail (preserves aspect ratio) then pads with black to fill.
    Saves back as JPEG regardless of original format.
    """
    from PIL import Image

    pil_img = Image.open(image_file)

    if pil_img.mode not in ('RGB', 'RGBA'):
        pil_img = pil_img.convert('RGB')

    pil_img.thumbnail(AVATAR_SIZE, Image.LANCZOS)

    canvas = Image.new('RGB', AVATAR_SIZE, (0, 0, 0))
    offset = (
        (AVATAR_SIZE[0] - pil_img.width) // 2,
        (AVATAR_SIZE[1] - pil_img.height) // 2,
    )
    canvas.paste(pil_img.convert('RGB'), offset)

    output = io.BytesIO()
    canvas.save(output, format='JPEG', quality=85, optimize=True)
    output.seek(0)

    # Mutate the in-memory uploaded file so Django stores the resized bytes.
    image_file.file = output
    image_file.content_type = 'image/jpeg'
    image_file.size = output.getbuffer().nbytes
    if getattr(image_file, 'name', None):
        base = os.path.splitext(image_file.name)[0]
        image_file.name = base + '.jpg'


# ── Nested serializers ────────────────────────────────────────────────

class CompanyBriefSerializer(serializers.Serializer):
    """Minimal company snapshot embedded in the user profile response."""
    id = serializers.IntegerField()
    name = serializers.CharField()


# ── Auth ──────────────────────────────────────────────────────────────

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


# ── Profile ───────────────────────────────────────────────────────────

class UserProfileSerializer(serializers.ModelSerializer):
    """
    Read-only profile returned by GET /me/ and embedded in auth responses.

    Returns:
      id, email, first_name, last_name, full_name, phone, position,
      avatar (absolute URL), role, company {id, name},
      is_email_verified, date_joined
    """
    full_name = serializers.CharField(read_only=True)
    company = CompanyBriefSerializer(read_only=True)
    avatar = serializers.ImageField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'phone', 'position', 'avatar', 'role',
            'company',
            'is_email_verified', 'date_joined',
        ]
        read_only_fields = fields


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    """
    PATCH /me/update/ — writable fields only.
    email, role, company are deliberately excluded and cannot be changed here.
    Avatar is validated for type (JPEG/PNG/WebP) and size (max 5 MB),
    then resized to 400×400 px before saving.
    """
    avatar = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'phone', 'position', 'avatar']

    def validate_avatar(self, image):
        if image is None:
            return image

        content_type = getattr(image, 'content_type', None)
        if content_type not in AVATAR_ALLOWED_CONTENT_TYPES:
            raise serializers.ValidationError(
                'Unsupported image type. Allowed types: JPEG, PNG, WebP.'
            )

        if image.size > AVATAR_MAX_BYTES:
            raise serializers.ValidationError(
                'Avatar file too large. Maximum allowed size is 5 MB.'
            )

        return image

    def update(self, instance, validated_data):
        new_avatar = validated_data.get('avatar')

        if new_avatar is not None:
            # Remove old file from disk before Django writes the new one.
            _delete_file(instance.avatar)
            _resize_avatar(new_avatar)

        return super().update(instance, validated_data)


# ── Password & verification ───────────────────────────────────────────

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


# ── Admin ─────────────────────────────────────────────────────────────

class UserListSerializer(serializers.ModelSerializer):
    """Для списков пользователей (суперадмин)."""
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'full_name', 'role', 'company', 'is_active', 'date_joined', 'last_login']
