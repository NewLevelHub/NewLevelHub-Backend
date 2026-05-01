from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from apps.bookings.models import Booking, ResourceBlock
from .models import Floor, MapPoint, ServiceRequest, Announcement

SOON_AVAILABLE_MINUTES = 30

# Types that can be linked to a bookable resource
_RESOURCE_POINT_TYPES = {'desk', 'meeting_room', 'parking', 'capsule'}


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
        if active_block is not None:
            return 'blocked'

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

    def validate(self, attrs):
        # On partial updates some fields may be absent — fall back to instance values.
        instance = self.instance
        point_type = attrs.get('point_type', getattr(instance, 'point_type', None))
        resource = attrs.get('resource', getattr(instance, 'resource', None))
        company = attrs.get('company', getattr(instance, 'company', None))
        x = attrs.get('x', getattr(instance, 'x', None))
        y = attrs.get('y', getattr(instance, 'y', None))

        if x is not None and not (0.0 <= x <= 100.0):
            raise serializers.ValidationError({'x': 'Must be between 0.0 and 100.0.'})
        if y is not None and not (0.0 <= y <= 100.0):
            raise serializers.ValidationError({'y': 'Must be between 0.0 and 100.0.'})

        if point_type in _RESOURCE_POINT_TYPES and resource is None:
            raise serializers.ValidationError(
                {'resource': f'resource is required for point_type "{point_type}".'}
            )
        if point_type == 'office' and company is None:
            raise serializers.ValidationError(
                {'company': 'company is required for point_type "office".'}
            )

        return attrs


class MapPointSearchSerializer(serializers.ModelSerializer):
    floor_id = serializers.IntegerField(source='floor.id', read_only=True)
    floor_name = serializers.CharField(source='floor.name', read_only=True)
    resource_id = serializers.IntegerField(source='resource.id', read_only=True, allow_null=True)
    resource_name = serializers.CharField(source='resource.name', read_only=True, allow_null=True)

    class Meta:
        model = MapPoint
        fields = ['id', 'floor_id', 'floor_name', 'point_type', 'label', 'x', 'y', 'resource_id', 'resource_name']


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
    user_name = serializers.CharField(source='user.full_name', read_only=True)

    class Meta:
        model = ServiceRequest
        fields = [
            'id', 'user', 'user_name', 'request_type', 'status', 'urgency',
            'floor', 'location', 'description', 'photo',
            'assigned_to', 'rating', 'completed_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'user', 'status', 'assigned_to', 'completed_at', 'created_at', 'updated_at']


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
