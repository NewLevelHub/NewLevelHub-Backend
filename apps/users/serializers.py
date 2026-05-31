import io
import os

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.companies.invite_policy import existing_user_cannot_accept_invite_error
from apps.companies.models import Invitation
from apps.core.error_codes import EMAIL_NOT_VERIFIED
from apps.core.exceptions import LocalizedError, raise_validation_error
from apps.core.i18n import get_lang, translate
from apps.hr.tasks import initialize_user_onboarding_progress
from .models import User


def _normalized_invite_email(raw_email):
    """Match UserManager.create_user email normalization for lookups."""
    return User.objects.normalize_email((raw_email or '').strip())


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
    onboarding_completed = serializers.SerializerMethodField()

    def get_onboarding_completed(self, obj):
        from apps.companies.models import CompanySettings
        try:
            return CompanySettings.objects.values_list(
                'onboarding_completed', flat=True
            ).get(company_id=obj.pk)
        except CompanySettings.DoesNotExist:
            return False


# ── Auth ──────────────────────────────────────────────────────────────

class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'phone', 'password', 'password_confirm']

    def validate(self, attrs):
        if attrs['password'] != attrs.pop('password_confirm'):
            raise_validation_error('password_confirm', 'auth.passwords_dont_match')
        return attrs

    def create(self, validated_data):
        validated_data.setdefault('is_email_verified', False)
        return User.objects.create_user(**validated_data)


class InviteRegistrationSerializer(serializers.ModelSerializer):
    """Register an employee via an invite token."""
    password = serializers.CharField(write_only=True, min_length=8)
    token = serializers.UUIDField(write_only=True)

    class Meta:
        model = User
        fields = ['token', 'first_name', 'last_name', 'phone', 'password']

    def validate(self, attrs):
        invitation = Invitation.objects.select_related('company').filter(token=attrs['token']).first()
        if not invitation:
            raise_validation_error('token', 'auth.token_invalid')
        if invitation.is_used:
            raise_validation_error('token', 'company.invite_already_used')
        if invitation.is_expired:
            raise_validation_error('token', 'company.invite_expired')
        existing = User.objects.filter(email__iexact=_normalized_invite_email(invitation.email)).first()
        if existing:
            err = existing_user_cannot_accept_invite_error(existing, invitation)
            if err:
                raise serializers.ValidationError({'email': err})
        # Building-staff invites (reception, service_manager) have no company and
        # therefore bypass the employee-count limit check.
        if invitation.company_id and invitation.company.is_employee_limit_reached:
            raise serializers.ValidationError(
                [{'_i18n': True, 'key': 'company.member_limit_exceeded', 'params': {}}]
            )

        attrs['invitation'] = invitation
        attrs['email'] = _normalized_invite_email(invitation.email)
        return attrs

    def create(self, validated_data):
        invitation = validated_data.pop('invitation')
        validated_data.pop('token', None)
        with transaction.atomic():
            # Lock only the Invitation row; ``select_related('company')`` joins via
            # an OUTER JOIN now that the FK is nullable, and Postgres rejects
            # FOR UPDATE on the nullable side of an outer join.
            invitation = (
                Invitation.objects
                .select_for_update(of=('self',))
                .select_related('company')
                .get(pk=invitation.pk)
            )
            if invitation.is_used:
                raise_validation_error('token', 'company.invite_already_used')
            if invitation.is_expired:
                raise_validation_error('token', 'company.invite_expired')
            if invitation.company_id and invitation.company.is_employee_limit_reached:
                raise serializers.ValidationError(
                    [{'_i18n': True, 'key': 'company.member_limit_exceeded', 'params': {}}]
                )

            email_key = _normalized_invite_email(invitation.email)
            existing = User.objects.select_for_update().filter(email__iexact=email_key).first()
            if existing:
                err = existing_user_cannot_accept_invite_error(existing, invitation)
                if err:
                    raise serializers.ValidationError({'email': err})
                existing.first_name = validated_data['first_name']
                existing.last_name = validated_data['last_name']
                existing.phone = validated_data.get('phone', '') or ''
                existing.set_password(validated_data['password'])
                existing.company = invitation.company
                existing.role = invitation.role
                existing.is_active = True
                existing.is_email_verified = False
                existing.save(
                    update_fields=[
                        'first_name',
                        'last_name',
                        'phone',
                        'password',
                        'company',
                        'role',
                        'is_active',
                        'is_email_verified',
                        'updated_at',
                    ],
                )
                user = existing
            else:
                try:
                    user = User.objects.create_user(
                        email=invitation.email,
                        first_name=validated_data['first_name'],
                        last_name=validated_data['last_name'],
                        phone=validated_data.get('phone', ''),
                        password=validated_data['password'],
                        company=invitation.company,
                        role=invitation.role,
                        is_email_verified=False,
                    )
                except IntegrityError as exc:
                    raise serializers.ValidationError(
                        {'email': [{'_i18n': True, 'key': 'users.email_already_registered', 'params': {}}]},
                    ) from exc

            initialize_user_onboarding_progress(user)

            invitation.is_used = True
            invitation.used_at = timezone.now()
            invitation.save(update_fields=['is_used', 'used_at', 'updated_at'])
            return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()
    remember_me = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        email = attrs['email']
        password = attrs['password']
        user_qs = User.objects.filter(email__iexact=email)
        blocked_user = user_qs.filter(is_active=False).first()
        if blocked_user and blocked_user.check_password(password):
            raise PermissionDenied(translate('auth.account_blocked', get_lang(self.context.get('request'))))

        user = authenticate(email=email, password=password)
        if not user:
            raise serializers.ValidationError(
                [{'_i18n': True, 'key': 'auth.invalid_credentials', 'params': {}}]
            )
        if not user.is_email_verified:
            raise LocalizedError(
                code=EMAIL_NOT_VERIFIED,
                i18n_key='auth.email_not_verified',
                http_status=403,
            )
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
                [{'_i18n': True, 'key': 'users.avatar_type_not_supported', 'params': {}}]
            )

        if image.size > AVATAR_MAX_BYTES:
            raise serializers.ValidationError(
                [{'_i18n': True, 'key': 'users.avatar_too_large', 'params': {'max_mb': 5}}]
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
            raise serializers.ValidationError(
                [{'_i18n': True, 'key': 'auth.current_password_wrong', 'params': {}}]
            )
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


class UserListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for user lists (superadmin)."""
    full_name = serializers.CharField(read_only=True)
    company = CompanyBriefSerializer(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'role', 'company', 'is_active', 'is_email_verified',
            'date_joined', 'last_login', 'avatar',
        ]


class UserDetailSerializer(serializers.ModelSerializer):
    """Full user detail for superadmin."""
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
