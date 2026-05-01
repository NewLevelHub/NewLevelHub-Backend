from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from apps.bookings.models import Booking
from .models import Floor, MapPoint, ServiceRequest, Announcement

SOON_AVAILABLE_MINUTES = 30

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
    resource_status = serializers.SerializerMethodField()
    resource_name = serializers.CharField(source='resource.name', read_only=True, allow_null=True)
    company_name = serializers.CharField(source='company.name', read_only=True, allow_null=True)

    class Meta:
        model = MapPoint
        fields = [
            'id', 'floor', 'point_type', 'label', 'x', 'y',
            'resource', 'resource_name', 'resource_status',
            'company', 'company_name',
        ]
        read_only_fields = ['id', 'resource_name', 'resource_status', 'company_name']

    def get_resource_status(self, obj):
        if obj.resource_id is None or obj.point_type not in _RESOURCE_POINT_TYPES:
            return None

        now = self.context.get('now') or timezone.now()

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
            return 'free'

        soon_threshold = now + timedelta(minutes=SOON_AVAILABLE_MINUTES)
        if active_booking.end_time <= soon_threshold:
            return 'soon_available'

        return 'occupied'


class FloorListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views — omits map_points."""
    plan_image_url = serializers.SerializerMethodField()

    class Meta:
        model = Floor
        fields = ['id', 'number', 'name', 'plan_image', 'plan_image_url', 'created_at', 'updated_at']
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

    class Meta:
        model = ServiceRequest
        fields = [
            'id', 'created_by', 'created_by_name', 'company',
            'request_type', 'status', 'urgency',
            'floor', 'location', 'description', 'photo',
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
                    errors[field] = 'This field is required.'
            if errors:
                raise serializers.ValidationError(errors)
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
                raise serializers.ValidationError(
                    f'Invalid status transition: {instance.status} → {value}. '
                    f'Expected next status: {allowed_next}.'
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
    author_name = serializers.CharField(source='author.full_name', read_only=True)
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = Announcement
        fields = [
            'id', 'scope', 'company', 'author', 'author_name',
            'title', 'body', 'category', 'image',
            'is_pinned', 'notify_email', 'is_read',
            'created_at',
        ]
        read_only_fields = ['id', 'author', 'created_at']

    def get_is_read(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        return obj.reads.filter(user=request.user).exists()
