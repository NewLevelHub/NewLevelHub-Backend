from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from apps.companies.models import Company
from apps.services.models import Floor
from apps.core.error_codes import (
    BOOKING_ADVANCE_DAYS_EXCEEDED,
    BOOKING_DESK_USER_OVERLAP,
    BOOKING_DURATION_TOO_SHORT,
    BOOKING_DURATION_TOO_LONG,
    BOOKING_PARKING_WHOLE_DAY_ONLY,
)
from apps.core.exceptions import LocalizedError, raise_validation_error
from apps.core.i18n import get_lang, translate
from apps.notifications.utils import create_notification
from .models import (
    Resource, ResourcePhoto, Booking, BookingParticipant,
    RecurringBooking, ResourceBlock, BookingCancellationAudit,
)
from .schedule import busy_slots_for_resource, is_soon_available, seven_day_range_from_today

User = get_user_model()

# Plans that allow access to company-assigned (non-shared) resources.
# basic and free users may only book shared resources (assigned_company IS NULL).
PLANS_WITH_ASSIGNED_RESOURCES = {'standard', 'premium'}

_CANCEL_REASON_I18N_PREFIX = 'booking.cancel_reason.'


def _booking_priority(user) -> int:
    """Return booking priority tier for the user: 3=premium/superadmin, 2=standard, 1=basic/guest."""
    if getattr(user, 'role', None) == 'superadmin':
        return 3
    company = getattr(user, 'company', None)
    plan = getattr(company, 'plan', 'basic') if company else 'basic'
    return {'premium': 3, 'standard': 2}.get(plan, 1)


# Type-specific validation constants
_MEETING_ROOM_MIN_MINUTES = 30
_MEETING_ROOM_MAX_MINUTES = 240  # 4 hours
_CAPSULE_MIN_MINUTES = 60
_CAPSULE_MAX_MINUTES = 480  # 8 hours


_EQUIPMENT_KEYS = {
    'projector': 'has_projector',
    'tv': 'has_tv',
    'whiteboard': 'has_whiteboard',
    'video_conf': 'has_video_conf',
    'monitor': 'has_monitor',
    'dock': 'has_dock',
    'power_outlet': 'has_power_outlet',
}


def equipment_from_resource(resource):
    return {key: getattr(resource, field) for key, field in _EQUIPMENT_KEYS.items()}


def apply_equipment_to_resource(resource, data):
    if not data:
        return
    for key, value in data.items():
        field = _EQUIPMENT_KEYS.get(key)
        if field is not None and isinstance(value, bool):
            setattr(resource, field, value)


class ResourcePhotoSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ResourcePhoto
        fields = ['id', 'image', 'image_url', 'created_at']
        read_only_fields = ['id', 'created_at']

    def get_image_url(self, obj):
        if not obj.image:
            return None
        request = self.context.get('request')
        url = obj.image.url
        if request:
            return request.build_absolute_uri(url)
        return url


class ResourceSerializer(serializers.ModelSerializer):
    """
    API uses ``type`` (maps to ``resource_type``), ``parking_type`` (regular/vip → is_vip),
    and ``equipment`` JSON for meeting rooms.
    """

    type = serializers.ChoiceField(
        source='resource_type',
        choices=[c[0] for c in Resource.TYPE_CHOICES],
    )
    floor_id = serializers.PrimaryKeyRelatedField(
        source='floor_fk',
        queryset=Floor.objects.all(),
        required=False,
        allow_null=True,
        write_only=True,
    )
    floor_number = serializers.IntegerField(source='floor_fk.number', read_only=True, allow_null=True)
    floor_name = serializers.CharField(source='floor_fk.name', read_only=True, allow_null=True)
    equipment = serializers.JSONField(required=False, allow_null=True, write_only=True)
    parking_type = serializers.ChoiceField(
        choices=[('regular', 'regular'), ('vip', 'vip')],
        required=False,
        allow_null=True,
        write_only=True,
    )
    assigned_company = serializers.PrimaryKeyRelatedField(
        queryset=Company.objects.all(),
        required=False,
        allow_null=True,
    )
    availability_start = serializers.TimeField(source='available_from', required=False)
    availability_end = serializers.TimeField(source='available_until', required=False)
    availability_days = serializers.ListField(
        source='available_days',
        child=serializers.IntegerField(min_value=0, max_value=6),
        required=False,
    )

    class Meta:
        model = Resource
        fields = [
            'id',
            'type',
            'name',
            'floor',
            'floor_id',
            'floor_number',
            'floor_name',
            'zone',
            'description',
            'photo',
            'capacity',
            'equipment',
            'is_active',
            'has_monitor',
            'has_dock',
            'has_power_outlet',
            'is_hot_desk',
            'assigned_company',
            'min_duration_minutes',
            'max_duration_minutes',
            'advance_booking_days',
            'min_cancel_minutes',
            'availability_start',
            'availability_end',
            'availability_days',
            'parking_type',
            'capsule_zone',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'floor_number', 'floor_name', 'created_at', 'updated_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.resource_type == 'meeting_room':
            data['equipment'] = equipment_from_resource(instance)
        else:
            data['equipment'] = None
        if instance.resource_type == 'parking':
            data['parking_type'] = 'vip' if instance.is_vip else 'regular'
        else:
            data['parking_type'] = None
        return data

    def validate(self, data):
        instance = getattr(self, 'instance', None)
        rtype = data.get('resource_type', instance.resource_type if instance else None)

        if rtype == 'meeting_room':
            if instance is None:
                if 'capacity' not in data or data.get('capacity') is None:
                    raise_validation_error('capacity', 'booking.capacity_required_for_meeting_room')
            if data.get('capacity') is not None and data['capacity'] < 1:
                raise_validation_error('capacity', 'booking.capacity_min_one')

        if rtype == 'parking' and instance is None and 'parking_type' not in data:
            raise_validation_error('parking_type', 'booking.parking_type_required')

        if rtype == 'capsule' and instance is None:
            zone = data.get('capsule_zone', '') or ''
            if zone not in ('quiet', 'regular'):
                raise_validation_error('capsule_zone', 'booking.capsule_zone_invalid')

        equipment = data.get('equipment', serializers.empty)
        if equipment is not serializers.empty and equipment is not None:
            if not isinstance(equipment, dict):
                raise_validation_error('equipment', 'booking.equipment_must_be_json')
            if rtype != 'meeting_room':
                raise_validation_error('equipment', 'booking.equipment_meeting_room_only')

        availability_start = data.get(
            'available_from',
            instance.available_from if instance else None,
        )
        availability_end = data.get(
            'available_until',
            instance.available_until if instance else None,
        )
        if availability_start and availability_end and availability_start >= availability_end:
            raise_validation_error('availability_start', 'booking.availability_start_after_end')

        assigned_company = data.get('assigned_company')
        if assigned_company is not None:
            if getattr(assigned_company, 'plan', 'basic') not in PLANS_WITH_ASSIGNED_RESOURCES:
                raise_validation_error('assigned_company', 'booking.assigned_company_requires_premium')

        return data

    def _sync_floor_integer(self, validated_data):
        floor_fk = validated_data.get('floor_fk')
        if floor_fk is not None:
            validated_data['floor'] = floor_fk.number
        return validated_data

    def create(self, validated_data):
        self._sync_floor_integer(validated_data)
        equipment = validated_data.pop('equipment', serializers.empty)
        parking_type = validated_data.pop('parking_type', serializers.empty)
        if parking_type is not serializers.empty and parking_type is not None:
            validated_data['is_vip'] = parking_type == 'vip'
        resource = super().create(validated_data)
        if (
            resource.resource_type == 'meeting_room'
            and equipment is not serializers.empty
            and equipment is not None
        ):
            apply_equipment_to_resource(resource, equipment)
            resource.save(update_fields=list(_EQUIPMENT_KEYS.values()))
        return resource

    def update(self, instance, validated_data):
        self._sync_floor_integer(validated_data)
        equipment = validated_data.pop('equipment', serializers.empty)
        parking_type = validated_data.pop('parking_type', serializers.empty)
        if parking_type is not serializers.empty and parking_type is not None:
            validated_data['is_vip'] = parking_type == 'vip'
        resource = super().update(instance, validated_data)
        if (
            resource.resource_type == 'meeting_room'
            and equipment is not serializers.empty
        ):
            apply_equipment_to_resource(resource, equipment or {})
            resource.save(update_fields=list(_EQUIPMENT_KEYS.values()))
        return resource


class ResourceScheduleSlotSerializer(serializers.Serializer):
    """Занятый интервал в поле schedule карточки ресурса (retrieve)."""

    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    booking_id = serializers.IntegerField(allow_null=True)
    user_name = serializers.CharField(allow_null=True)


class ResourceDayScheduleSlotSerializer(serializers.Serializer):
    """Слот в ответе GET /resources/{id}/schedule/?date=YYYY-MM-DD."""

    booking_id = serializers.IntegerField()
    start = serializers.CharField()
    end = serializers.CharField()
    status = serializers.CharField()


class ResourceDetailSerializer(ResourceSerializer):
    """GET retrieve: поля ресурса + занятые интервалы на 7 календарных дней (локальная дата)."""

    schedule = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()

    class Meta(ResourceSerializer.Meta):
        fields = list(ResourceSerializer.Meta.fields) + ['schedule', 'photos']
        read_only_fields = list(ResourceSerializer.Meta.read_only_fields) + ['schedule', 'photos']

    def get_schedule(self, obj):
        start, end = seven_day_range_from_today()
        return ResourceScheduleSlotSerializer(
            busy_slots_for_resource(obj.pk, start, end),
            many=True,
        ).data

    def get_photos(self, obj):
        qs = obj.photos.all()
        return ResourcePhotoSerializer(qs, many=True, context=self.context).data


class ResourceListSerializer(serializers.ModelSerializer):
    """Лёгкий сериализатор для каталога."""

    type = serializers.CharField(source='resource_type', read_only=True)
    availability_days = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        source='available_days',
        read_only=True,
    )
    parking_type = serializers.SerializerMethodField()
    photo_url = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()
    equipment = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    reason = serializers.SerializerMethodField()
    available_at = serializers.SerializerMethodField()
    assigned_company = serializers.PrimaryKeyRelatedField(read_only=True)
    assigned_company_name = serializers.CharField(
        source='assigned_company.name', read_only=True, allow_null=True, default=None,
    )
    floor_id = serializers.IntegerField(source='floor_fk.id', read_only=True, allow_null=True)
    floor_number = serializers.IntegerField(source='floor_fk.number', read_only=True, allow_null=True)
    floor_name = serializers.CharField(source='floor_fk.name', read_only=True, allow_null=True)

    class Meta:
        model = Resource
        fields = [
            'id',
            'type',
            'name',
            'floor_id',
            'floor_number',
            'floor_name',
            'zone',
            'photo',
            'photo_url',
            'photos',
            'capacity',
            'equipment',
            'is_active',
            'is_hot_desk',
            'availability_days',
            'parking_type',
            'capsule_zone',
            'assigned_company',
            'assigned_company_name',
            'status',
            'reason',
            'available_at',
        ]

    def get_parking_type(self, obj):
        if obj.resource_type != 'parking':
            return None
        return 'vip' if obj.is_vip else 'regular'

    def get_photo_url(self, obj):
        if not obj.photo:
            return None
        request = self.context.get('request')
        url = obj.photo.url
        if request:
            return request.build_absolute_uri(url)
        return url

    def get_photos(self, obj):
        qs = obj.photos.all()
        return ResourcePhotoSerializer(qs, many=True, context=self.context).data

    def get_equipment(self, obj):
        if obj.resource_type == 'meeting_room':
            return equipment_from_resource(obj)
        return None

    def _availability_window(self, obj):
        blocks = getattr(obj, '_active_blocks_prefetch', None)
        bookings = getattr(obj, '_active_bookings_prefetch', None)
        ends = []
        if blocks:
            ends.extend(b.end_time for b in blocks)
        if bookings:
            ends.extend(b.end_time for b in bookings)
        if not ends:
            return None
        return max(ends)

    def _active_block(self, obj):
        blocks = getattr(obj, '_active_blocks_prefetch', None) or []
        return blocks[0] if blocks else None

    def get_status(self, obj):
        now = self.context.get('catalog_now') or timezone.now()
        active_block = self._active_block(obj)
        if active_block is not None:
            return 'blocked'
        if not obj.is_active:
            return 'occupied'
        window_end = self._availability_window(obj)
        if window_end is None:
            return 'free'
        if is_soon_available(window_end, now):
            return 'soon_available'
        return 'occupied'

    def get_reason(self, obj):
        active_block = self._active_block(obj)
        if active_block is None:
            return None
        return active_block.reason or ''

    def get_available_at(self, obj):
        now = self.context.get('catalog_now') or timezone.now()
        if self._active_block(obj) is not None:
            return None
        window_end = self._availability_window(obj)
        if window_end is None:
            return None
        if not obj.is_active or not is_soon_available(window_end, now):
            return None
        return window_end


class BulkIdsSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=True,
        allow_empty=False,
    )


class ResourceBulkCreateSerializer(serializers.Serializer):
    template = serializers.DictField()
    count = serializers.IntegerField(min_value=1)
    name_prefix = serializers.CharField(max_length=255, trim_whitespace=True)

    def validate(self, attrs):
        template_payload = dict(attrs['template'])
        template_payload.setdefault('name', f"{attrs['name_prefix']} template")
        serializer = ResourceSerializer(data=template_payload, context=self.context)
        serializer.is_valid(raise_exception=True)
        attrs['template_data'] = serializer.validated_data
        return attrs


class BookingCreateSerializer(serializers.ModelSerializer):
    resource_id = serializers.IntegerField(write_only=True, required=False)
    participant_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=[])

    class Meta:
        model = Booking
        fields = [
            'id',
            'resource',
            'resource_id',
            'user',
            'start_time',
            'end_time',
            'status',
            'description',
            'participant_ids',
        ]
        read_only_fields = ['id', 'user', 'status']
        extra_kwargs = {'resource': {'required': False}}

    def _resolve_resource(self, attrs):
        resource = attrs.get('resource')
        resource_id = attrs.get('resource_id')
        if resource is not None:
            return resource
        if resource_id is None:
            raise_validation_error('resource_id', 'booking.resource_id_required')
        try:
            return Resource.objects.get(pk=resource_id)
        except Resource.DoesNotExist:
            raise_validation_error('resource_id', 'booking.resource_not_found')

    def _validate_access(self, *, resource, user):
        if user.is_superadmin():
            return
        # Cross-company block: resource is locked to a different company.
        if resource.assigned_company_id and resource.assigned_company_id != user.company_id:
            raise_validation_error('resource_id', 'booking.resource_wrong_company')
        # Plan-based block: basic/free companies may not book assigned resources.
        company = getattr(user, 'company', None)
        plan = getattr(company, 'plan', 'basic') if company else 'basic'
        if resource.assigned_company_id and plan not in PLANS_WITH_ASSIGNED_RESOURCES:
            raise_validation_error('resource_id', 'booking.resource_requires_premium')

    def _validate_availability_window(self, *, resource, start_time, end_time):
        local_start = timezone.localtime(start_time)
        local_end = timezone.localtime(end_time)

        # Parking whole-day bookings span midnight (00:00 to next day 00:00),
        # so we skip the single-day check for parking.
        is_parking_whole_day = (
            resource.resource_type == 'parking'
            and local_start.hour == 0 and local_start.minute == 0
            and local_end.hour == 0 and local_end.minute == 0
        )
        if not is_parking_whole_day and local_start.date() != local_end.date():
            raise_validation_error('detail', 'booking.same_day_only')

        available_days = resource.available_days or list(range(7))
        weekday = local_start.weekday()
        if weekday not in available_days:
            raise_validation_error('detail', 'booking.unavailable_day')

        # Skip availability-hours check for whole-day parking bookings
        if not is_parking_whole_day:
            start_local_time = local_start.time()
            end_local_time = local_end.time()
            if start_local_time < resource.available_from or end_local_time > resource.available_until:
                raise_validation_error('detail', 'booking.outside_operating_hours')

    def _validate_type_specific_rules(self, *, resource, start_time, end_time):
        """Validate type-specific booking rules per resource type."""
        rtype = resource.resource_type
        duration = end_time - start_time
        duration_minutes = duration.total_seconds() / 60
        local_start = timezone.localtime(start_time)
        local_end = timezone.localtime(end_time)

        # advance_booking_days applies uniformly to ALL resource types.
        # If the field is null (not possible with the current non-nullable model field,
        # but handled defensively) → no advance limit is enforced.
        if resource.advance_booking_days is not None:
            max_start_date = (
                timezone.localtime(timezone.now()) + timedelta(days=resource.advance_booking_days)
            ).date()
            if timezone.localtime(start_time).date() > max_start_date:
                raise LocalizedError(
                    code=BOOKING_ADVANCE_DAYS_EXCEEDED,
                    i18n_key='booking.advance_days_exceeded',
                    params={'advance_days': resource.advance_booking_days},
                )

        if rtype == 'meeting_room':
            if duration_minutes < _MEETING_ROOM_MIN_MINUTES:
                raise LocalizedError(
                    code=BOOKING_DURATION_TOO_SHORT,
                    i18n_key='booking.duration_too_short',
                    params={'min_minutes': _MEETING_ROOM_MIN_MINUTES},
                )
            if duration_minutes > _MEETING_ROOM_MAX_MINUTES:
                raise LocalizedError(
                    code=BOOKING_DURATION_TOO_LONG,
                    i18n_key='booking.duration_too_long',
                    params={'max_minutes': _MEETING_ROOM_MAX_MINUTES},
                )

        elif rtype == 'parking':
            # Must be whole-day: start 00:00, end 23:59 same day or next day 00:00
            is_whole_day = (
                local_start.hour == 0 and local_start.minute == 0
                and (
                    (local_end.hour == 23 and local_end.minute == 59)
                    or (local_end.hour == 0 and local_end.minute == 0
                        and local_end.date() > local_start.date())
                )
            )
            if not is_whole_day:
                raise LocalizedError(
                    code=BOOKING_PARKING_WHOLE_DAY_ONLY,
                    i18n_key='booking.parking_whole_day_only',
                )

        elif rtype == 'capsule':
            if duration_minutes < _CAPSULE_MIN_MINUTES:
                raise LocalizedError(
                    code=BOOKING_DURATION_TOO_SHORT,
                    i18n_key='booking.duration_too_short',
                    params={'min_minutes': _CAPSULE_MIN_MINUTES},
                )
            if duration_minutes > _CAPSULE_MAX_MINUTES:
                raise LocalizedError(
                    code=BOOKING_DURATION_TOO_LONG,
                    i18n_key='booking.duration_too_long',
                    params={'max_minutes': _CAPSULE_MAX_MINUTES},
                )

    def _validate_resource_duration_limits(self, *, resource, start_time, end_time):
        """Validate against resource-level min/max duration settings.

        Skipped for parking because parking enforces whole-day bookings via
        type-specific rules, making per-resource duration limits inapplicable.
        """
        if resource.resource_type == 'parking':
            return

        duration_minutes = (end_time - start_time).total_seconds() / 60

        if resource.min_duration_minutes and duration_minutes < resource.min_duration_minutes:
            raise LocalizedError(
                code=BOOKING_DURATION_TOO_SHORT,
                i18n_key='booking.duration_too_short',
                params={'min_minutes': resource.min_duration_minutes},
            )
        if resource.max_duration_minutes and duration_minutes > resource.max_duration_minutes:
            raise LocalizedError(
                code=BOOKING_DURATION_TOO_LONG,
                i18n_key='booking.duration_too_long',
                params={'max_minutes': resource.max_duration_minutes},
            )

    def _validate_user_active_limit(self, *, user):
        active_limit = int(getattr(settings, 'MAX_ACTIVE_BOOKINGS_PER_USER', 5))
        active_count = Booking.objects.filter(
            user=user,
            status='confirmed',
            end_time__gt=timezone.now(),
        ).count()
        if active_count >= active_limit:
            raise_validation_error('detail', 'booking.active_limit_exceeded', {'limit': active_limit})

    def _ensure_no_conflicts(self, *, resource, start_time, end_time, exclude_booking_id=None, user=None):
        override_hours = int(getattr(settings, 'PRIORITY_OVERRIDE_HOURS', 2))
        override_cutoff = timezone.now() + timedelta(hours=override_hours)

        booking_overlap_qs = Booking.objects.filter(
            resource=resource,
            status='confirmed',
            start_time__lt=end_time,
            end_time__gt=start_time,
        )
        if exclude_booking_id is not None:
            booking_overlap_qs = booking_overlap_qs.exclude(pk=exclude_booking_id)

        if booking_overlap_qs.exists():
            if user is not None:
                requester_priority = _booking_priority(user)
                # Attempt override: all conflicting bookings must be lower priority
                # and start after the override cutoff window.
                displaceable = booking_overlap_qs.filter(
                    priority__lt=requester_priority,
                    start_time__gt=override_cutoff,
                )
                non_displaceable = booking_overlap_qs.exclude(
                    priority__lt=requester_priority,
                    start_time__gt=override_cutoff,
                )
                if non_displaceable.exists() or not displaceable.exists():
                    raise LocalizedError(code='BOOKING_CONFLICT', i18n_key='booking.conflict', http_status=409)
                # All conflicting bookings can be displaced — store them for cancellation in create()
                self._bookings_to_displace = list(displaceable)
            else:
                raise LocalizedError(code='BOOKING_CONFLICT', i18n_key='booking.conflict', http_status=409)

        has_block_overlap = ResourceBlock.objects.filter(
            resource=resource,
            start_time__lt=end_time,
            end_time__gt=start_time,
        ).exists()
        if has_block_overlap:
            raise LocalizedError(
                code='BOOKING_CONFLICT',
                i18n_key='booking.conflict',
                http_status=409,
            )

    def _ensure_no_user_desk_overlap(self, *, user, resource, start_time, end_time, exclude_booking_id=None):
        if resource.resource_type != 'desk':
            return
        qs = Booking.objects.filter(
            user=user,
            resource__resource_type='desk',
            status='confirmed',
            start_time__lt=end_time,
            end_time__gt=start_time,
        )
        if exclude_booking_id is not None:
            qs = qs.exclude(pk=exclude_booking_id)
        if qs.exists():
            raise LocalizedError(
                code=BOOKING_DESK_USER_OVERLAP,
                i18n_key='booking.desk_user_overlap',
                http_status=409,
            )

    def validate_participant_ids(self, value):
        if not value:
            return value
        existing_ids = set(
            User.objects.filter(id__in=value, is_active=True).values_list('id', flat=True)
        )
        missing = [uid for uid in value if uid not in existing_ids]
        if missing:
            raise serializers.ValidationError(
                f'Users not found or inactive: {missing}'
            )
        return value

    def _check_timezone_aware(self, field_name):
        """
        Reject naive datetimes submitted without timezone info.

        DRF normalises the value before validate() runs, so we inspect
        the raw ``initial_data`` string instead of the already-parsed value.
        """
        raw = self.initial_data.get(field_name, '')
        if not raw:
            return
        raw_str = str(raw)
        # A tz-aware ISO string contains '+', '-' after the time part, or ends with 'Z'.
        # Check for tz designator: look for 'Z' at end, or '+'/'-' after 'T...' time component.
        # Presence of 'T' indicates a datetime; absence of tz offset means naive.
        if 'T' in raw_str or (' ' in raw_str and len(raw_str) > 10):
            has_z = raw_str.endswith('Z') or raw_str.upper().endswith('Z')
            # Find offset: after the time digits there should be +HH:MM or -HH:MM
            # Simplest heuristic: the string after 'T' (or space) must contain +/- for tz
            time_part = raw_str.split('T')[-1] if 'T' in raw_str else raw_str.split(' ')[-1]
            has_offset = '+' in time_part or (time_part.count('-') > 0 and ':' in time_part)
            if not has_z and not has_offset:
                raise_validation_error(field_name, 'booking.datetime_must_be_timezone_aware')

    def validate(self, attrs):
        self._check_timezone_aware('start_time')
        self._check_timezone_aware('end_time')
        attrs['resource'] = self._resolve_resource(attrs)
        attrs.pop('resource_id', None)
        if attrs['start_time'] >= attrs['end_time']:
            raise_validation_error('non_field_errors', 'booking.start_time_before_end_time')
        if attrs['start_time'] <= timezone.now():
            raise_validation_error('start_time', 'booking.start_time_must_be_future')
        self._validate_type_specific_rules(
            resource=attrs['resource'],
            start_time=attrs['start_time'],
            end_time=attrs['end_time'],
        )
        return attrs

    def create(self, validated_data):
        import logging
        from apps.notifications.tasks import send_notification_email
        self._bookings_to_displace = []
        validated_data.pop('resource_id', None)
        participant_ids = validated_data.pop('participant_ids', [])
        user = self.context['request'].user
        start_time = validated_data['start_time']
        end_time = validated_data['end_time']
        resource_id = validated_data['resource'].id

        with transaction.atomic():
            resource = Resource.objects.select_for_update().get(pk=resource_id)
            self._validate_access(resource=resource, user=user)
            self._ensure_no_conflicts(
                resource=resource,
                start_time=start_time,
                end_time=end_time,
                user=user,
            )
            self._ensure_no_user_desk_overlap(
                user=user,
                resource=resource,
                start_time=start_time,
                end_time=end_time,
            )
            self._validate_availability_window(
                resource=resource,
                start_time=start_time,
                end_time=end_time,
            )
            self._validate_resource_duration_limits(
                resource=resource,
                start_time=start_time,
                end_time=end_time,
            )
            self._validate_user_active_limit(user=user)

            validated_data['resource'] = resource
            validated_data['user'] = user
            validated_data['priority'] = _booking_priority(user)
            # Guests have no company; set company only for company-bound users.
            if user.role != 'guest':
                validated_data['company'] = user.company or resource.assigned_company
            booking = super().create(validated_data)

            from .qr_image import ensure_capsule_booking_qr
            ensure_capsule_booking_qr(booking)

            # Cancel any displaced lower-priority bookings and notify their owners.
            for displaced_booking in self._bookings_to_displace:
                displaced_booking.status = 'cancelled'
                displaced_booking.cancel_reason = 'displaced_by_priority_booking'
                displaced_booking.cancelled_by = user
                displaced_booking.save(update_fields=['status', 'cancel_reason', 'cancelled_by'])
                create_notification(
                    user=displaced_booking.user,
                    notification_type='booking_cancelled',
                    title='Бронирование отменено',
                    message=(
                        f'Ваше бронирование {displaced_booking.resource.name} было отменено '
                        f'в пользу пользователя с более высоким приоритетом.'
                    ),
                    link=f'/bookings/{displaced_booking.id}',
                )

            # In-app confirmation for the booking owner (preferences + DND via helper)
            create_notification(
                user=user,
                notification_type='booking_confirmed',
                title=f'Бронирование подтверждено: {resource.name}',
                message=(
                    f'{timezone.localtime(start_time):%d.%m.%Y %H:%M} — '
                    f'{timezone.localtime(end_time):%H:%M}'
                ),
                link=f'/bookings/{booking.id}',
            )

            # Only create participants for meeting rooms
            if resource.resource_type == 'meeting_room' and participant_ids:
                participant_users = list(User.objects.filter(id__in=participant_ids))
                participant_by_id = {u.id: u for u in participant_users}
                for uid in participant_ids:
                    BookingParticipant.objects.create(booking=booking, user_id=uid)
                # Send notifications to participants (respects DND and per-type preferences)
                for uid in participant_ids:
                    participant_user = participant_by_id.get(uid)
                    if participant_user:
                        create_notification(
                            user=participant_user,
                            notification_type='booking_confirmed',
                            title=f'Вас добавили на встречу: {resource.name}',
                            message=(
                                f'{user.full_name} пригласил вас в '
                                f'{resource.name} на {start_time:%Y-%m-%d %H:%M}.'
                            ),
                            link=f'/bookings/{booking.id}',
                        )
                        send_notification_email.delay(
                            participant_user.id,
                            'booking_confirmed',
                            {
                                'subject': f'Вы добавлены на встречу: {resource.name}',
                                'resource_name': resource.name,
                                'start_time': timezone.localtime(start_time).strftime('%d.%m.%Y %H:%M'),
                                'end_time': timezone.localtime(end_time).strftime('%d.%m.%Y %H:%M'),
                                'action_url': f'/bookings/{booking.id}',
                            },
                        )

        # Send booking confirmation email to the booking owner
        try:
            send_notification_email.delay(
                user.id,
                'booking_confirmed',
                {
                    'subject': 'Ваше бронирование подтверждено',
                    'resource_name': resource.name,
                    'start_time': timezone.localtime(start_time).strftime('%d.%m.%Y %H:%M'),
                    'end_time': timezone.localtime(end_time).strftime('%d.%m.%Y %H:%M'),
                    'action_url': f'/bookings/{booking.id}',
                },
            )
        except Exception:
            logging.getLogger(__name__).warning('Failed to enqueue notification email', exc_info=True)

        return booking


class BookingUserSerializer(serializers.ModelSerializer):
    """Read-only nested user snapshot embedded in booking responses."""
    full_name = serializers.CharField(read_only=True)
    avatar = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'full_name', 'avatar', 'position', 'role']
        read_only_fields = fields

    def get_avatar(self, obj):
        if not obj.avatar:
            return None
        request = self.context.get('request')
        url = obj.avatar.url
        if request:
            return request.build_absolute_uri(url)
        return url


class BookingValidateQrSerializer(serializers.Serializer):
    qr_code = serializers.UUIDField()


class BookingSerializer(serializers.ModelSerializer):
    resource_name = serializers.CharField(source='resource.name', read_only=True)
    resource_type = serializers.CharField(source='resource.resource_type', read_only=True)
    capsule_zone = serializers.CharField(source='resource.capsule_zone', read_only=True)
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    booked_by = serializers.SerializerMethodField()
    participants = serializers.SerializerMethodField()
    recurring_booking_id = serializers.IntegerField(read_only=True)
    qr_image = serializers.ImageField(read_only=True)
    cancel_reason = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = [
            'id', 'resource', 'resource_name', 'resource_type', 'capsule_zone',
            'user', 'user_name', 'booked_by', 'company',
            'start_time', 'end_time', 'status', 'description',
            'cancelled_by', 'cancel_reason', 'participants', 'recurring_booking_id',
            'checked_in_at', 'qr_code', 'qr_image', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'user', 'company', 'checked_in_at', 'qr_code', 'qr_image',
            'created_at', 'updated_at',
        ]

    def get_cancel_reason(self, obj):
        reason = obj.cancel_reason
        if not reason:
            return reason
        i18n_key = f'{_CANCEL_REASON_I18N_PREFIX}{reason}'
        lang = get_lang(self.context.get('request'))
        translated = translate(i18n_key, lang)
        # translate() falls back to common.server_error text when the key is missing;
        # if the translated value differs from the i18n_key itself we have a real translation.
        # Additionally guard against the server_error fallback by comparing to it.
        server_error_text = translate('common.server_error', lang)
        if translated == server_error_text:
            return reason
        return translated

    def get_booked_by(self, obj):
        return BookingUserSerializer(obj.user, context=self.context).data

    def get_participants(self, obj):
        return [
            {
                'id': p.user.id,
                'email': p.user.email,
                'full_name': f'{p.user.first_name} {p.user.last_name}'.strip(),
            }
            for p in obj.participants.select_related('user').all()
        ]


class RecurringBookingSerializer(serializers.ModelSerializer):
    resource_id = serializers.IntegerField(read_only=True)
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    user_role = serializers.CharField(source='user.role', read_only=True)

    class Meta:
        model = RecurringBooking
        fields = [
            'id',
            'resource',
            'resource_id',
            'user',
            'user_name',
            'user_role',
            'company',
            'recurrence_type',
            'day_of_week',
            'start_time',
            'end_time',
            'is_active',
            'valid_from',
            'valid_until',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'user', 'company', 'created_at', 'updated_at']


class RecurringBookingCreateSerializer(serializers.Serializer):
    resource_id = serializers.IntegerField()
    recurrence_type = serializers.ChoiceField(
        choices=[('weekly', 'Weekly'), ('daily', 'Daily')],
        default='weekly',
    )
    # Required for 'weekly'; omit or set null for 'daily'.
    day_of_week = serializers.IntegerField(min_value=0, max_value=6, required=False, allow_null=True)
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    repeat_until = serializers.DateField()

    def validate_resource_id(self, value):
        try:
            return Resource.objects.get(pk=value)
        except Resource.DoesNotExist:
            raise_validation_error('resource_id', 'booking.resource_not_found')

    def validate(self, attrs):
        resource = attrs['resource_id']
        request = self.context['request']
        user = request.user
        today = timezone.localdate()
        recurrence_type = attrs.get('recurrence_type', 'weekly')

        if attrs['start_time'] >= attrs['end_time']:
            raise_validation_error('end_time', 'booking.start_time_before_end_time')

        if attrs['repeat_until'] < today:
            raise_validation_error('repeat_until', 'booking.repeat_until_in_past')

        # day_of_week is required for weekly recurrence.
        if recurrence_type == 'weekly' and attrs.get('day_of_week') is None:
            raise_validation_error('day_of_week', 'booking.day_of_week_required_for_weekly')

        if not user.is_superadmin():
            # Cross-company block: resource is locked to a different company.
            if resource.assigned_company_id and resource.assigned_company_id != user.company_id:
                raise_validation_error('resource_id', 'booking.resource_wrong_company')
            # Plan-based block: basic/free companies may not book assigned resources.
            company = getattr(user, 'company', None)
            plan = getattr(company, 'plan', 'basic') if company else 'basic'
            if resource.assigned_company_id and plan not in PLANS_WITH_ASSIGNED_RESOURCES:
                raise_validation_error('resource_id', 'booking.resource_requires_premium')

        if recurrence_type == 'weekly':
            available_days = resource.available_days or list(range(7))
            if attrs['day_of_week'] not in available_days:
                raise_validation_error('day_of_week', 'booking.unavailable_day')

        if (
            attrs['start_time'] < resource.available_from
            or attrs['end_time'] > resource.available_until
        ):
            raise_validation_error('detail', 'booking.outside_operating_hours')

        # Duplicate-series guard: one active series per resource+slot combination.
        # For weekly: match on day_of_week + time. For daily: match on time only.
        duplicate_qs = RecurringBooking.objects.filter(
            resource=resource,
            is_active=True,
            recurrence_type=recurrence_type,
            start_time=attrs['start_time'],
            end_time=attrs['end_time'],
            valid_from__lte=attrs['repeat_until'],
        ).filter(
            Q(valid_until__isnull=True) | Q(valid_until__gte=today)
        )
        if recurrence_type == 'weekly':
            duplicate_qs = duplicate_qs.filter(day_of_week=attrs['day_of_week'])
        if duplicate_qs.exists():
            raise_validation_error('detail', 'booking.recurring_slot_conflict')

        from apps.bookings.tasks import recurring_has_creatable_occurrence

        if not recurring_has_creatable_occurrence(
            day_of_week=attrs.get('day_of_week'),
            end_time=attrs['end_time'],
            repeat_until=attrs['repeat_until'],
            base_date=today,
            recurrence_type=recurrence_type,
        ):
            raise_validation_error('repeat_until', 'booking.recurring_no_creatable_dates')

        attrs['resource'] = resource
        return attrs


class ParticipantPickerUserSerializer(serializers.ModelSerializer):
    """Lightweight user snapshot for the participant picker autocomplete."""
    full_name = serializers.CharField(read_only=True)
    avatar = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'email', 'full_name', 'avatar', 'position']
        read_only_fields = fields

    def get_avatar(self, obj):
        if not obj.avatar:
            return None
        request = self.context.get('request')
        url = obj.avatar.url
        if request:
            return request.build_absolute_uri(url)
        return url


class ResourceBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceBlock
        fields = '__all__'
        read_only_fields = ['id', 'blocked_by', 'created_at', 'updated_at']

    def validate(self, attrs):
        start_time = attrs.get('start_time')
        end_time = attrs.get('end_time')
        if start_time and end_time and start_time >= end_time:
            raise_validation_error('detail', 'booking.start_time_before_end_time')
        return attrs


class BulkCancelSerializer(serializers.Serializer):
    booking_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        min_length=1,
        max_length=50,
    )
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class AuditCancelledBySerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'full_name']
        read_only_fields = ['id', 'full_name']


class BookingCancellationAuditSerializer(serializers.ModelSerializer):
    booking_id = serializers.IntegerField(read_only=True)
    cancelled_by = AuditCancelledBySerializer(read_only=True)

    class Meta:
        model = BookingCancellationAudit
        fields = ['id', 'booking_id', 'cancelled_by', 'cancel_reason', 'cancelled_at']
        read_only_fields = fields
