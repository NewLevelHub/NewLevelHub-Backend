from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.core.exceptions import raise_validation_error
from apps.core.i18n import get_lang, translate

from apps.companies.models import Company
from apps.bookings.models import Booking, ResourceBlock
from .models import Floor, MapPoint, ServiceRequest, Announcement

SOON_AVAILABLE_MINUTES = 30
RESOURCE_STATUS_FREE = 'free'
RESOURCE_STATUS_SOON_AVAILABLE = 'soon_available'
RESOURCE_STATUS_OCCUPIED = 'occupied'
RESOURCE_STATUS_BLOCKED = 'blocked'

# Types that can be linked to a bookable resource
_RESOURCE_POINT_TYPES = {'desk', 'meeting_room', 'parking', 'capsule'}


class FloorPrimaryKeyOrNumberField(serializers.PrimaryKeyRelatedField):
    """Accept floor FK id and legacy floor number in write payloads."""

    def to_internal_value(self, data):
        try:
            return super().to_internal_value(data)
        except serializers.ValidationError:
            try:
                floor_number = int(data)
            except (TypeError, ValueError):
                raise

            floor = Floor.objects.filter(number=floor_number).first()
            if floor is not None:
                return floor
            raise


class MapPointSerializer(serializers.ModelSerializer):
    resource_status = serializers.SerializerMethodField(
        help_text=(
            'Current resource status at requested datetime. '
            'Possible values: free, soon_available, occupied, blocked, null. '
            'Precedence: blocked > occupied/soon_available > free; null for non-bookable points.'
        )
    )
    resource_status_reason = serializers.SerializerMethodField(
        help_text=(
            'Machine-readable reason for `resource_status`: '
            'active_block, active_booking, active_booking_ends_within_threshold, '
            'no_active_booking_or_block, not_a_bookable_resource.'
        )
    )
    next_free_at = serializers.SerializerMethodField(
        help_text='Datetime when point is expected to become free, or null if currently free/non-applicable.'
    )
    resource_name = serializers.CharField(source='resource.name', read_only=True, allow_null=True)
    company_name = serializers.CharField(source='company.name', read_only=True, allow_null=True)

    class Meta:
        model = MapPoint
        fields = [
            'id', 'floor', 'point_type', 'label', 'x', 'y', 'width', 'height',
            'resource', 'resource_name', 'resource_status', 'resource_status_reason', 'next_free_at',
            'company', 'company_name',
        ]
        read_only_fields = ['id', 'resource_name', 'resource_status', 'resource_status_reason', 'next_free_at',
                            'company_name']
        extra_kwargs = {
            'width': {'allow_null': True, 'required': False},
            'height': {'allow_null': True, 'required': False},
        }

    def _get_status_payload(self, obj):
        status_cache = self.context.setdefault('_resource_status_payload_cache', {})
        cached_payload = status_cache.get(obj.id)
        if cached_payload is not None:
            return cached_payload

        if obj.resource_id is None or obj.point_type not in _RESOURCE_POINT_TYPES:
            payload = {
                'status': None,
                'reason': 'not_a_bookable_resource',
                'next_free_at': None,
            }
            status_cache[obj.id] = payload
            return payload

        now = self.context.get('now') or timezone.now()
        active_block = (
            ResourceBlock.objects
            .filter(
                resource_id=obj.resource_id,
                start_time__lte=now,
                end_time__gt=now,
            )
            .order_by('end_time')
            .first()
        )
        # Blocks always win over booking-derived states.
        if active_block is not None:
            payload = {
                'status': RESOURCE_STATUS_BLOCKED,
                'reason': 'active_block',
                'next_free_at': active_block.end_time,
            }
            status_cache[obj.id] = payload
            return payload

        active_booking = (
            Booking.objects
            .filter(
                resource_id=obj.resource_id,
                status='confirmed',
                start_time__lte=now,
                end_time__gt=now,
            )
            .order_by('end_time')
            .first()
        )

        if active_booking is None:
            payload = {
                'status': RESOURCE_STATUS_FREE,
                'reason': 'no_active_booking_or_block',
                'next_free_at': None,
            }
            status_cache[obj.id] = payload
            return payload

        soon_threshold = now + timedelta(minutes=SOON_AVAILABLE_MINUTES)
        if active_booking.end_time <= soon_threshold:
            payload = {
                'status': RESOURCE_STATUS_SOON_AVAILABLE,
                'reason': 'active_booking_ends_within_threshold',
                'next_free_at': active_booking.end_time,
            }
            status_cache[obj.id] = payload
            return payload

        payload = {
            'status': RESOURCE_STATUS_OCCUPIED,
            'reason': 'active_booking',
            'next_free_at': active_booking.end_time,
        }
        status_cache[obj.id] = payload
        return payload

    def get_resource_status(self, obj):
        return self._get_status_payload(obj)['status']

    def get_resource_status_reason(self, obj):
        return self._get_status_payload(obj)['reason']

    def get_next_free_at(self, obj):
        return self._get_status_payload(obj)['next_free_at']

    def validate(self, attrs):
        # On partial updates some fields may be absent — fall back to instance values.
        instance = self.instance
        point_type = attrs.get('point_type', getattr(instance, 'point_type', None))
        resource = attrs.get('resource', getattr(instance, 'resource', None))
        company = attrs.get('company', getattr(instance, 'company', None))
        x = attrs.get('x', getattr(instance, 'x', None))
        y = attrs.get('y', getattr(instance, 'y', None))
        width = attrs.get('width', getattr(instance, 'width', None))
        height = attrs.get('height', getattr(instance, 'height', None))

        if x is not None and not (0.0 <= x <= 100.0):
            raise_validation_error('x', 'services.coordinate_out_of_range')
        if y is not None and not (0.0 <= y <= 100.0):
            raise_validation_error('y', 'services.coordinate_out_of_range')
        if width is not None and not (0.0 <= width <= 100.0):
            raise_validation_error('width', 'services.coordinate_out_of_range')
        if height is not None and not (0.0 <= height <= 100.0):
            raise_validation_error('height', 'services.coordinate_out_of_range')

        if point_type in _RESOURCE_POINT_TYPES and resource is None:
            raise_validation_error('resource', 'services.resource_required_for_type', {'point_type': point_type})
        if point_type == 'office' and company is None:
            raise_validation_error('company', 'services.company_required_for_office')

        floor = attrs.get('floor', getattr(self.instance, 'floor', None))
        if resource and floor:
            if resource.floor_fk_id and resource.floor_fk_id != floor.id:
                raise serializers.ValidationError({
                    'resource': translate(
                        'services.resource_floor_mismatch',
                        get_lang(self.context.get('request')),
                    )
                })

        return attrs


class MapPointSearchSerializer(serializers.ModelSerializer):
    floor_id = serializers.IntegerField(source='floor.id', read_only=True)
    floor_name = serializers.CharField(source='floor.name', read_only=True)
    resource_id = serializers.IntegerField(source='resource.id', read_only=True, allow_null=True)
    resource_name = serializers.CharField(source='resource.name', read_only=True, allow_null=True)

    class Meta:
        model = MapPoint
        fields = ['id', 'floor_id', 'floor_name', 'point_type', 'label', 'x', 'y', 'width', 'height',
                  'resource_id', 'resource_name']


class FloorListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views — omits map_points."""
    plan_image_url = serializers.SerializerMethodField()
    occupancy_pct = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Floor
        fields = ['id', 'number', 'name', 'plan_image', 'plan_image_url', 'occupancy_pct', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_plan_image_url(self, obj):
        if not obj.plan_image:
            return None
        request = self.context.get('request')
        if request is not None:
            return request.build_absolute_uri(obj.plan_image.url)
        return obj.plan_image.url


class FloorDetailSerializer(FloorListSerializer):
    """Detail serializer — includes nested map_points."""
    map_points = MapPointSerializer(source='points', many=True, read_only=True)

    class Meta(FloorListSerializer.Meta):
        fields = FloorListSerializer.Meta.fields + ['map_points']


# Keep FloorSerializer as an alias so existing view imports don't break
FloorSerializer = FloorListSerializer


class ServiceRequestSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True, allow_null=True)
    assigned_to_name = serializers.CharField(source='assigned_to.full_name', allow_null=True, read_only=True)
    photo = serializers.ImageField(use_url=True, required=False, allow_null=True)
    floor = FloorPrimaryKeyOrNumberField(queryset=Floor.objects.all(), required=False, allow_null=True)
    floor_number = serializers.IntegerField(source='floor.number', read_only=True, allow_null=True)
    floor_name = serializers.CharField(source='floor.name', read_only=True, allow_null=True)

    class Meta:
        model = ServiceRequest
        fields = [
            'id', 'created_by', 'created_by_name', 'company',
            'request_type', 'status', 'urgency',
            'floor', 'floor_number', 'floor_name', 'location', 'description', 'photo',
            'assigned_to', 'assigned_to_name', 'rating', 'completed_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'created_by', 'company', 'status', 'assigned_to',
            'completed_at', 'created_at', 'updated_at',
        ]

    def validate(self, attrs):
        request = self.context.get('request')
        if self.instance is None and request and request.method == 'POST':
            required_fields = ('floor', 'location', 'description', 'urgency')
            errors = {}
            for field in required_fields:
                value = self.initial_data.get(field, None)
                if value in (None, ''):
                    errors[field] = [{'_i18n': True, 'key': 'services.field_required', 'params': {}}]
            if errors:
                raise DRFValidationError(errors)
        return attrs


class ServiceRequestStatusSerializer(serializers.ModelSerializer):
    """Status update by company_admin or superadmin."""

    class Meta:
        model = ServiceRequest
        fields = ['status']

    def validate_status(self, value):
        instance = self.instance
        if instance and value != instance.status:
            _transitions = {
                'new': 'accepted',
                'accepted': 'in_progress',
                'in_progress': 'completed',
            }
            allowed_next = _transitions.get(instance.status)
            if value != allowed_next:
                raise_validation_error(
                    'status',
                    'services.invalid_status_transition',
                    {'current': instance.status, 'next_status': value, 'expected': allowed_next},
                )
        return value


class ServiceRequestRateSerializer(serializers.Serializer):
    rating = serializers.IntegerField(min_value=1, max_value=5)


class ServiceRequestUpdateSerializer(serializers.ModelSerializer):
    """Для суперадмина: смена статуса, назначение исполнителя."""

    class Meta:
        model = ServiceRequest
        fields = ['status', 'assigned_to']


class AnnouncementSerializer(serializers.ModelSerializer):
    """
    AC-aligned serializer (DEV-100):

      - body is exposed as ``text`` (per AC vocabulary)
      - ``company_id`` (null = building-wide / БЦ) is the only company input;
        the backend forces this for company_admin in ``perform_create``
      - ``scope`` is read-only — it's auto-derived from company on save
    """

    text = serializers.CharField(source='body')
    company_id = serializers.PrimaryKeyRelatedField(
        source='company',
        queryset=Company.objects.all(),
        allow_null=True,
        required=False,
    )
    author_name = serializers.CharField(source='author.full_name', read_only=True)
    is_read = serializers.SerializerMethodField()
    read_count = serializers.SerializerMethodField()

    class Meta:
        model = Announcement
        fields = [
            'id', 'scope', 'company_id', 'author', 'author_name',
            'title', 'text', 'category', 'image',
            'is_pinned', 'notify_email', 'is_read', 'read_count',
            'created_at',
        ]
        read_only_fields = ['id', 'scope', 'author', 'created_at']

    def get_is_read(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        return obj.reads.filter(user=request.user).exists()

    def get_read_count(self, obj):
        request = self.context.get('request')
        if request is None:
            return None
        user = request.user
        if user.role in ('superadmin', 'company_admin') or obj.author == user:
            return obj.reads.count()
        return None
