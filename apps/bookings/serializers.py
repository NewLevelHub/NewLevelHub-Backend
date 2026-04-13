from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from apps.companies.models import Company
from .models import Resource, Booking, BookingParticipant, RecurringBooking, ResourceBlock

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
    participant_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=[])

    class Meta:
        model = Booking
        fields = ['resource', 'start_time', 'end_time', 'description', 'participant_ids']

    def validate(self, attrs):
        # TODO: проверка конфликтов (overlap), рабочее время ресурса,
        #       advance_booking_days, min/max duration, лимит активных бронирований
        if attrs['start_time'] >= attrs['end_time']:
            raise serializers.ValidationError('start_time must be before end_time')
        return attrs

    def create(self, validated_data):
        participant_ids = validated_data.pop('participant_ids', [])
        user = self.context['request'].user
        validated_data['user'] = user
        validated_data['company'] = user.company
        booking = super().create(validated_data)
        for uid in participant_ids:
            BookingParticipant.objects.create(booking=booking, user_id=uid)
        # TODO: отправить уведомление участникам
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
