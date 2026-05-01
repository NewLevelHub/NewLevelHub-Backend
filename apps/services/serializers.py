from rest_framework import serializers
from .models import Floor, MapPoint, ServiceRequest, Announcement


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
    class Meta:
        model = MapPoint
        fields = ['id', 'point_type', 'label', 'x', 'y', 'resource', 'company']
        read_only_fields = ['id']


class FloorSerializer(serializers.ModelSerializer):
    points = MapPointSerializer(many=True, read_only=True)

    class Meta:
        model = Floor
        fields = ['id', 'number', 'name', 'plan_image', 'points']
        read_only_fields = ['id']


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
