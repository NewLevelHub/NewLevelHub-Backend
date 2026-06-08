from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers
from apps.users.models import User
from apps.notifications.utils import create_notification
from apps.core.exceptions import raise_validation_error
from apps.services.models import Floor

from .invite_policy import email_blocks_new_company_invitation
from .limits import notify_company_admins_limit_thresholds
from .models import Company, CompanySettings, Invitation
from .tasks import send_invitation_email

_CATEGORIES_MAX_COUNT = 10
_CATEGORIES_ITEM_MAX_LEN = 50


class CategoriesField(serializers.JSONField):
    """
    JSONField subclass that correctly handles ``categories`` arriving as a
    JSON-encoded string from a ``multipart/form-data`` request.

    Background
    ----------
    DRF's ``JSONField.get_value`` wraps QueryDict values in its internal
    ``JSONString`` class (which carries ``is_json_string = True``) so that
    ``to_internal_value`` knows to call ``json.loads``.  That works perfectly
    for the case ``categories=["B2B","SaaS"]`` (a JSON-encoded array).

    However, when a serializer's ``to_internal_value`` mutates the incoming
    data (e.g. sets ``data['categories'] = ['B2B', 'SaaS']`` as a Python list
    on a QueryDict copy), ``get_value`` still calls ``JSONString(the_list)``
    which invokes ``str.__new__(cls, ['B2B', 'SaaS'])`` → ``"['B2B', 'SaaS']"``
    (Python repr, not valid JSON), causing a spurious 400.

    The correct fix is to override ``get_value`` so that when the raw value is
    already a Python list we skip the JSON-decoding step entirely.  When it is
    a string we apply the same ``JSONString`` wrapping DRF uses, which means
    ``to_internal_value`` will call ``json.loads`` — turning
    ``'["B2B","SaaS"]'`` into ``['B2B', 'SaaS']`` as expected.

    A bare non-JSON string (e.g. ``categories=B2B``) makes ``json.loads`` raise
    ``ValueError``, which DRF converts to ``self.fail('invalid')`` and the
    ``_validate_categories`` validator then surfaces the correct i18n error.

    Accepted multipart encodings
    ----------------------------
    * ``categories=["B2B","SaaS"]``  → ``['B2B', 'SaaS']`` ✓
    * ``categories=[]``              → ``[]`` ✓
    * ``categories=B2B``             → 400 ``company.categories_must_be_list`` ✓
    """

    def get_value(self, dictionary):
        from rest_framework.utils import html as _html

        if _html.is_html_input(dictionary) and self.field_name in dictionary:
            raw = dictionary[self.field_name]
            # If a previous layer already decoded the value to a list, pass it
            # through directly — no JSONString wrapping needed.
            if isinstance(raw, list):
                return raw
            # Otherwise apply the standard JSONString wrapping so that
            # to_internal_value (which checks is_json_string) calls json.loads.

            class JSONString(str):  # noqa: N801  (inline class, matches DRF style)
                def __new__(cls, value):
                    ret = str.__new__(cls, value)
                    ret.is_json_string = True
                    return ret
            return JSONString(raw)
        return super().get_value(dictionary)


def _validate_categories(value):
    """
    Shared validator for the `categories` field.
    - Must be a list
    - Each item must be a non-empty string
    - Maximum 10 items
    - Each item max 50 characters
    Raises serializers.ValidationError using the _i18n marker so the custom
    exception handler translates the message.
    """
    if not isinstance(value, list):
        raise serializers.ValidationError(
            [{'_i18n': True, 'key': 'company.categories_must_be_list', 'params': {}}]
        )
    if len(value) > _CATEGORIES_MAX_COUNT:
        raise serializers.ValidationError(
            [{'_i18n': True, 'key': 'company.categories_too_many', 'params': {}}]
        )
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise serializers.ValidationError(
                [{'_i18n': True, 'key': 'company.categories_item_must_be_string', 'params': {}}]
            )
        if len(item) > _CATEGORIES_ITEM_MAX_LEN:
            raise serializers.ValidationError(
                [{'_i18n': True, 'key': 'company.categories_item_too_long', 'params': {}}]
            )
    return value


class CompanyAdminUserSerializer(serializers.ModelSerializer):
    """Minimal read-only snapshot of the company admin user embedded in company responses."""
    full_name = serializers.CharField(read_only=True)
    avatar = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'full_name', 'avatar', 'position']
        read_only_fields = fields

    def get_avatar(self, obj):
        if not obj.avatar:
            return None
        request = self.context.get('request')
        url = obj.avatar.url
        if request:
            return request.build_absolute_uri(url)
        return url


class CompanySerializer(serializers.ModelSerializer):
    """Lightweight serializer used for list responses."""
    employee_count = serializers.IntegerField(read_only=True)
    company_admin = serializers.SerializerMethodField()
    floor_id = serializers.IntegerField(source='floor_fk_id', read_only=True, allow_null=True)
    floor_number = serializers.IntegerField(source='floor_fk.number', read_only=True, allow_null=True)
    floor_name = serializers.SerializerMethodField()

    def get_company_admin(self, obj):
        admin = obj.members.filter(role='company_admin', is_active=True).first()
        if admin is None:
            return None
        return CompanyAdminUserSerializer(admin, context=self.context).data

    def get_floor_name(self, obj):
        return obj.floor_fk.name if obj.floor_fk else None

    class Meta:
        model = Company
        fields = [
            'id', 'name', 'description', 'logo', 'floor', 'office_number',
            'company_admin', 'categories',
            'plan', 'max_employees', 'storage_limit_gb', 'max_boards',
            'is_active', 'working_hours_start', 'working_hours_end',
            'employee_count', 'created_at', 'updated_at',
            'floor_id', 'floor_number', 'floor_name',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class CompanyDetailSerializer(serializers.ModelSerializer):
    """Detail serializer — includes employee_count, storage_used (bytes), and onboarding_completed."""
    employee_count = serializers.SerializerMethodField()
    storage_used = serializers.SerializerMethodField()
    onboarding_completed = serializers.SerializerMethodField()
    company_admin = serializers.SerializerMethodField()
    floor_id = serializers.IntegerField(source='floor_fk_id', read_only=True, allow_null=True)
    floor_number = serializers.IntegerField(source='floor_fk.number', read_only=True, allow_null=True)
    floor_name = serializers.SerializerMethodField()

    def get_company_admin(self, obj):
        admin = obj.members.filter(role='company_admin', is_active=True).first()
        if admin is None:
            return None
        return CompanyAdminUserSerializer(admin, context=self.context).data

    def get_floor_name(self, obj):
        return obj.floor_fk.name if obj.floor_fk else None

    class Meta:
        model = Company
        fields = [
            'id', 'name', 'description', 'logo', 'floor', 'office_number',
            'company_admin', 'categories',
            'plan', 'max_employees', 'storage_limit_gb', 'max_boards',
            'is_active', 'working_hours_start', 'working_hours_end',
            'employee_count', 'storage_used', 'onboarding_completed',
            'created_at', 'updated_at',
            'floor_id', 'floor_number', 'floor_name',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_employee_count(self, obj):
        return obj.employee_count

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

    categories = CategoriesField(default=list, required=False)
    floor_id = serializers.PrimaryKeyRelatedField(
        queryset=Floor.objects.all(),
        source='floor_fk',
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = Company
        fields = [
            'id', 'name', 'description', 'logo', 'floor', 'office_number',
            'categories',
            'plan', 'max_employees', 'storage_limit_gb', 'max_boards',
            'floor_id',
        ]
        read_only_fields = ['id']

    def validate_categories(self, value):
        return _validate_categories(value)

    def create(self, validated_data):
        plan = validated_data.get('plan', 'basic')
        plan_defaults = Company.PLAN_DEFAULT_LIMITS.get(plan, Company.PLAN_DEFAULT_LIMITS['basic'])

        for field_name in ('max_employees', 'max_boards', 'storage_limit_gb'):
            if field_name not in validated_data:
                validated_data[field_name] = plan_defaults[field_name]

        return super().create(validated_data)


class CompanyUpdateSerializer(serializers.ModelSerializer):
    """PATCH/PUT serializer for superadmin — all writable Company fields, no CompanySettings."""

    categories = CategoriesField(required=False)
    floor_id = serializers.PrimaryKeyRelatedField(
        queryset=Floor.objects.all(),
        source='floor_fk',
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = Company
        fields = [
            'name', 'description', 'logo', 'floor', 'office_number',
            'categories',
            'plan', 'max_employees', 'storage_limit_gb', 'max_boards',
            'floor_id',
        ]

    def validate_categories(self, value):
        return _validate_categories(value)


class CompanyAdminUpdateSerializer(serializers.ModelSerializer):
    """Restricted PATCH serializer for company_admin — subset of fields only."""

    categories = CategoriesField(required=False)
    floor_id = serializers.PrimaryKeyRelatedField(
        queryset=Floor.objects.all(),
        source='floor_fk',
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = Company
        fields = ['name', 'description', 'logo', 'categories', 'floor_id']

    def validate_categories(self, value):
        return _validate_categories(value)


class WorkingHoursSerializer(serializers.Serializer):
    """Nested serializer for working_hours: {start, end} as time strings (HH:MM)."""
    start = serializers.TimeField(format='%H:%M', input_formats=['%H:%M', '%H:%M:%S'])
    end = serializers.TimeField(format='%H:%M', input_formats=['%H:%M', '%H:%M:%S'])

    def validate(self, attrs):
        if attrs['start'] >= attrs['end']:
            raise serializers.ValidationError(
                [{'_i18n': True, 'key': 'company.working_hours_start_after_end', 'params': {}}]
            )
        return attrs


HEX_COLOR_REGEX = r'^#([A-Fa-f0-9]{3}|[A-Fa-f0-9]{6})$'


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
    brand_primary_color = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
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

    def validate_brand_primary_color(self, value):
        import re
        if value and not re.match(HEX_COLOR_REGEX, value):
            raise serializers.ValidationError(
                [{'_i18n': True, 'key': 'company.hex_color_invalid', 'params': {}}]
            )
        return value

    def validate_custom_labels(self, value):
        """Validate each label has {name: str, color: valid hex}."""
        import re
        hex_re = re.compile(HEX_COLOR_REGEX)
        errors = {}
        _field_required = {'_i18n': True, 'key': 'services.field_required', 'params': {}}
        _hex_invalid = {'_i18n': True, 'key': 'company.hex_color_invalid', 'params': {}}
        for idx, item in enumerate(value):
            item_errors = {}
            if 'name' not in item or not isinstance(item.get('name'), str) or not item['name']:
                item_errors['name'] = _field_required
            if 'color' not in item:
                item_errors['color'] = _field_required
            elif not hex_re.match(str(item['color'])):
                item_errors['color'] = _hex_invalid
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
    # Company-scoped invites — only roles that belong to a company. Building-staff
    # roles (reception, service_manager) go through BuildingInvitationCreateSerializer.
    role = serializers.ChoiceField(choices=['employee', 'company_admin'])

    class Meta:
        model = Invitation
        fields = ['email', 'role']

    def validate_email(self, value):
        email = value.strip().lower()
        company = self.context['company']

        block_msg = email_blocks_new_company_invitation(email, company)
        if block_msg:
            raise serializers.ValidationError(block_msg)

        has_active_invitation = Invitation.objects.filter(
            company=company,
            email__iexact=email,
            is_used=False,
            expires_at__gt=timezone.now(),
        ).exists()
        if has_active_invitation:
            raise serializers.ValidationError(
                [{'_i18n': True, 'key': 'company.invite_active_for_email', 'params': {}}]
            )

        return email

    def validate_role(self, value):
        request = self.context['request']
        if value == 'company_admin' and request.user.role != 'superadmin':
            raise serializers.ValidationError(
                [{'_i18n': True, 'key': 'company.role_superadmin_only', 'params': {'role': value}}]
            )
        return value

    def validate(self, attrs):
        request = self.context['request']
        company = self.context['company']
        email = attrs['email'].strip()

        if company.employee_count >= company.max_employees:
            raise serializers.ValidationError(
                [{'_i18n': True, 'key': 'company.member_limit_exceeded', 'params': {}}]
            )

        active_exists = Invitation.objects.filter(
            company=company,
            email__iexact=email,
            is_used=False,
            expires_at__gte=timezone.now(),
        ).exists()
        if active_exists:
            raise_validation_error('email', 'company.invite_active_for_email')

        role = attrs.get('role', 'employee')
        if request.user.role == 'company_admin' and role == 'company_admin':
            raise_validation_error('role', 'company.admin_cannot_invite_role', {'role': role})

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
        current_employees = company.employee_count
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


class BuildingInvitationCreateSerializer(serializers.ModelSerializer):
    """
    Invite a building-staff user (reception or service_manager) without tying
    them to any company. Superadmin-only — gated at the view level.
    """

    role = serializers.ChoiceField(choices=['reception', 'service_manager'])

    class Meta:
        model = Invitation
        fields = ['email', 'role']

    def validate_email(self, value):
        email = value.strip().lower()
        has_active_invitation = Invitation.objects.filter(
            company__isnull=True,
            email__iexact=email,
            is_used=False,
            expires_at__gt=timezone.now(),
        ).exists()
        if has_active_invitation:
            raise serializers.ValidationError(
                [{'_i18n': True, 'key': 'company.invite_active_for_email', 'params': {}}]
            )
        return email

    def create(self, validated_data):
        validated_data['company'] = None
        validated_data['invited_by'] = self.context['request'].user
        invitation = super().create(validated_data)
        create_notification(
            user=invitation.invited_by,
            notification_type='invitation',
            title='Приглашение отправлено',
            message=f'На адрес {invitation.email} отправлено приглашение сотрудника здания.',
            link='/team/manage',
        )
        send_invitation_email.delay(invitation.id)
        return invitation


class CompanyMemberSerializer(serializers.ModelSerializer):
    """Company member — read-only list representation."""
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'full_name', 'role', 'position',
            'avatar', 'is_active', 'is_email_verified', 'date_joined', 'last_login',
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
