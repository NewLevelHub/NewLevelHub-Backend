from rest_framework import serializers

from apps.companies.models import Company

from .models import Floor, MapPoint, ServiceRequest, Announcement


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

    class Meta:
        model = Announcement
        fields = [
            'id', 'scope', 'company_id', 'author', 'author_name',
            'title', 'text', 'category', 'image',
            'is_pinned', 'notify_email', 'is_read',
            'created_at',
        ]
        read_only_fields = ['id', 'scope', 'author', 'created_at']

    def get_is_read(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        return obj.reads.filter(user=request.user).exists()
