from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException

from apps.companies.models import Company
from apps.notifications.models import Notification
from .models import Resource, Booking, BookingParticipant, RecurringBooking, ResourceBlock
from .schedule import busy_slots_for_resource, seven_day_range_from_today

# Type-specific validation constants
_DESK_MAX_ADVANCE_DAYS = 14
_MEETING_ROOM_MIN_MINUTES = 30
_MEETING_ROOM_MAX_MINUTES = 240  # 4 hours
_PARKING_MAX_ADVANCE_DAYS = 7
_CAPSULE_MIN_MINUTES = 60
_CAPSULE_MAX_MINUTES = 480  # 8 hours

# Минут до освобождения, после которых статус «soon_available» вместо «occupied».
SOON_AVAILABLE_MINUTES = 30

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


class ResourceSerializer(serializers.ModelSerializer):
    """
    API uses ``type`` (maps to ``resource_type``), ``parking_type`` (regular/vip → is_vip),
    and ``equipment`` JSON for meeting rooms.
    """

    type = serializers.ChoiceField(
        source='resource_type',
        choices=[c[0] for c in Resource.TYPE_CHOICES],
    )
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
            'availability_start',
            'availability_end',
            'availability_days',
            'parking_type',
            'capsule_zone',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

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
                    raise serializers.ValidationError(
                        {'capacity': 'This field is required for meeting_room.'}
                    )
            if data.get('capacity') is not None and data['capacity'] < 1:
                raise serializers.ValidationError(
                    {'capacity': 'Capacity must be at least 1.'}
                )

        if rtype == 'parking' and instance is None and 'parking_type' not in data:
            raise serializers.ValidationError(
                {'parking_type': 'This field is required for parking.'}
            )

        if rtype == 'capsule' and instance is None:
            zone = data.get('capsule_zone', '') or ''
            if zone not in ('quiet', 'regular'):
                raise serializers.ValidationError(
                    {'capsule_zone': 'Must be "quiet" or "regular" for capsule.'}
                )

        equipment = data.get('equipment', serializers.empty)
        if equipment is not serializers.empty and equipment is not None:
            if not isinstance(equipment, dict):
                raise serializers.ValidationError({'equipment': 'Must be a JSON object.'})
            if rtype != 'meeting_room':
                raise serializers.ValidationError(
                    {'equipment': 'Equipment JSON is only used for meeting_room resources.'}
                )

        availability_start = data.get(
            'available_from',
            instance.available_from if instance else None,
        )
        availability_end = data.get(
            'available_until',
            instance.available_until if instance else None,
        )
        if availability_start and availability_end and availability_start >= availability_end:
            raise serializers.ValidationError(
                {'availability_start': 'availability_start must be earlier than availability_end.'}
            )

        return data

    def create(self, validated_data):
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
    """Занятый интервал в ответе schedule / поле schedule в карточке ресурса."""

    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    booking_id = serializers.IntegerField(allow_null=True)
    user_name = serializers.CharField(allow_null=True)


class ResourceDetailSerializer(ResourceSerializer):
    """GET retrieve: поля ресурса + занятые интервалы на 7 календарных дней (локальная дата)."""

    schedule = serializers.SerializerMethodField()

    class Meta(ResourceSerializer.Meta):
        fields = list(ResourceSerializer.Meta.fields) + ['schedule']
        read_only_fields = list(ResourceSerializer.Meta.read_only_fields) + ['schedule']

    def get_schedule(self, obj):
        start, end = seven_day_range_from_today()
        return ResourceScheduleSlotSerializer(
            busy_slots_for_resource(obj.pk, start, end),
            many=True,
        ).data


class ResourceListSerializer(serializers.ModelSerializer):
    """Лёгкий сериализатор для каталога."""

    type = serializers.CharField(source='resource_type', read_only=True)
    parking_type = serializers.SerializerMethodField()
    photo_url = serializers.SerializerMethodField()
    equipment = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    available_at = serializers.SerializerMethodField()

    class Meta:
        model = Resource
        fields = [
            'id',
            'type',
            'name',
            'floor',
            'zone',
            'photo',
            'photo_url',
            'capacity',
            'equipment',
            'is_active',
            'is_hot_desk',
            'parking_type',
            'capsule_zone',
            'status',
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

    def get_status(self, obj):
        now = self.context.get('catalog_now') or timezone.now()
        if not obj.is_active:
            return 'occupied'
        window_end = self._availability_window(obj)
        if window_end is None:
            return 'free'
        soon_before = window_end - timedelta(minutes=SOON_AVAILABLE_MINUTES)
        if now >= soon_before:
            return 'soon_available'
        return 'occupied'

    def get_available_at(self, obj):
        now = self.context.get('catalog_now') or timezone.now()
        window_end = self._availability_window(obj)
        if window_end is None:
            return None
        soon_before = window_end - timedelta(minutes=SOON_AVAILABLE_MINUTES)
        if not obj.is_active or now < soon_before:
            return None
        return window_end


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
    class BookingConflictException(APIException):
        status_code = 409
        default_detail = 'Selected time slot is already occupied.'
        default_code = 'booking_conflict'

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
            raise serializers.ValidationError({'resource_id': 'This field is required.'})
        try:
            return Resource.objects.get(pk=resource_id)
        except Resource.DoesNotExist as exc:
            raise serializers.ValidationError({'resource_id': 'Resource does not exist.'}) from exc

    def _validate_access(self, *, resource, user):
        if resource.assigned_company_id and resource.assigned_company_id != user.company_id:
            raise serializers.ValidationError(
                {'resource': 'Resource is assigned to another company.'}
            )

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
            raise serializers.ValidationError(
                {'detail': 'Booking must be within a single day.'}
            )

        available_days = resource.available_days or list(range(7))
        weekday = local_start.weekday()
        if weekday not in available_days:
            raise serializers.ValidationError(
                {'detail': 'Booking is outside resource availability days.'}
            )

        # Skip availability-hours check for whole-day parking bookings
        if not is_parking_whole_day:
            start_local_time = local_start.time()
            end_local_time = local_end.time()
            if start_local_time < resource.available_from or end_local_time > resource.available_until:
                raise serializers.ValidationError(
                    {'detail': 'Booking is outside resource availability hours.'}
                )

    def _validate_type_specific_rules(self, *, resource, start_time, end_time):
        """Validate type-specific booking rules per resource type."""
        rtype = resource.resource_type
        duration = end_time - start_time
        duration_minutes = duration.total_seconds() / 60
        local_start = timezone.localtime(start_time)
        local_end = timezone.localtime(end_time)

        if rtype == 'desk':
            max_start = timezone.now() + timedelta(days=_DESK_MAX_ADVANCE_DAYS)
            if start_time > max_start:
                raise serializers.ValidationError(
                    {'detail': 'Desk booking must start within 14 days from now.'}
                )

        elif rtype == 'meeting_room':
            if duration_minutes < _MEETING_ROOM_MIN_MINUTES:
                raise serializers.ValidationError(
                    {'detail': 'Meeting room booking minimum duration is 30 minutes.'}
                )
            if duration_minutes > _MEETING_ROOM_MAX_MINUTES:
                raise serializers.ValidationError(
                    {'detail': 'Meeting room booking maximum duration is 4 hours.'}
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
                raise serializers.ValidationError(
                    {'detail': 'Parking booking must be whole-day only '
                               '(start 00:00, end 23:59 or next day 00:00).'}
                )
            max_start = timezone.now() + timedelta(days=_PARKING_MAX_ADVANCE_DAYS)
            if start_time > max_start:
                raise serializers.ValidationError(
                    {'detail': 'Parking booking must start within 7 days from now.'}
                )

        elif rtype == 'capsule':
            if duration_minutes < _CAPSULE_MIN_MINUTES:
                raise serializers.ValidationError(
                    {'detail': 'Capsule booking minimum duration is 1 hour.'}
                )
            if duration_minutes > _CAPSULE_MAX_MINUTES:
                raise serializers.ValidationError(
                    {'detail': 'Capsule booking maximum duration is 8 hours.'}
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
            raise serializers.ValidationError(
                {'detail': f'Minimum booking duration is {resource.min_duration_minutes} minutes.'}
            )
        if resource.max_duration_minutes and duration_minutes > resource.max_duration_minutes:
            raise serializers.ValidationError(
                {'detail': f'Maximum booking duration is {resource.max_duration_minutes} minutes.'}
            )

    def _validate_user_active_limit(self, *, user):
        active_limit = int(getattr(settings, 'MAX_ACTIVE_BOOKINGS_PER_USER', 5))
        active_count = Booking.objects.filter(
            user=user,
            status='confirmed',
            end_time__gt=timezone.now(),
        ).count()
        if active_count >= active_limit:
            raise serializers.ValidationError(
                {'detail': f'Active booking limit exceeded ({active_limit}).'}
            )

    def _ensure_no_conflicts(self, *, resource, start_time, end_time):
        has_booking_overlap = Booking.objects.filter(
            resource=resource,
            status='confirmed',
            start_time__lt=end_time,
            end_time__gt=start_time,
        ).exists()
        if has_booking_overlap:
            raise self.BookingConflictException()

        has_block_overlap = ResourceBlock.objects.filter(
            resource=resource,
            start_time__lt=end_time,
            end_time__gt=start_time,
        ).exists()
        if has_block_overlap:
            raise self.BookingConflictException()

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
                raise serializers.ValidationError(
                    {field_name: 'Datetime must include timezone info (e.g. 2025-04-16T10:00:00+05:00).'}
                )

    def validate(self, attrs):
        self._check_timezone_aware('start_time')
        self._check_timezone_aware('end_time')
        attrs['resource'] = self._resolve_resource(attrs)
        attrs.pop('resource_id', None)
        if attrs['start_time'] >= attrs['end_time']:
            raise serializers.ValidationError('start_time must be before end_time')
        return attrs

    def create(self, validated_data):
        validated_data.pop('resource_id', None)
        participant_ids = validated_data.pop('participant_ids', [])
        user = self.context['request'].user
        start_time = validated_data['start_time']
        end_time = validated_data['end_time']
        resource_id = validated_data['resource'].id

        with transaction.atomic():
            resource = Resource.objects.select_for_update().get(pk=resource_id)
            self._validate_access(resource=resource, user=user)
            self._validate_availability_window(
                resource=resource,
                start_time=start_time,
                end_time=end_time,
            )
            self._validate_type_specific_rules(
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
            self._ensure_no_conflicts(
                resource=resource,
                start_time=start_time,
                end_time=end_time,
            )

            validated_data['resource'] = resource
            validated_data['user'] = user
            validated_data['company'] = user.company
            booking = super().create(validated_data)

            # Only create participants for meeting rooms
            if resource.resource_type == 'meeting_room' and participant_ids:
                for uid in participant_ids:
                    BookingParticipant.objects.create(booking=booking, user_id=uid)
                # Send notifications to participants
                for uid in participant_ids:
                    Notification.objects.create(
                        user_id=uid,
                        notification_type='booking_confirmed',
                        title=f'You have been added to a meeting: {resource.name}',
                        body=(
                            f'{user.full_name} invited you to '
                            f'{resource.name} on {start_time:%Y-%m-%d %H:%M}.'
                        ),
                        url='',
                    )

        return booking


class BookingSerializer(serializers.ModelSerializer):
    resource_name = serializers.CharField(source='resource.name', read_only=True)
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    participants = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = [
            'id', 'resource', 'resource_name', 'user', 'user_name', 'company',
            'start_time', 'end_time', 'status', 'description',
            'cancelled_by', 'cancel_reason', 'participants',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'user', 'company', 'created_at', 'updated_at']

    def get_participants(self, obj):
        return list(obj.participants.values_list('user__email', flat=True))


class RecurringBookingSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecurringBooking
        fields = '__all__'
        read_only_fields = ['id', 'user', 'company', 'created_at', 'updated_at']


class ResourceBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceBlock
        fields = '__all__'
        read_only_fields = ['id', 'blocked_by', 'created_at', 'updated_at']
