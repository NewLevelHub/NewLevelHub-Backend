from rest_framework import serializers
from .models import Resource, Booking, BookingParticipant, RecurringBooking, ResourceBlock


class ResourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Resource
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class ResourceListSerializer(serializers.ModelSerializer):
    """Лёгкий сериализатор для каталога."""
    class Meta:
        model = Resource
        fields = [
            'id', 'name', 'resource_type', 'floor', 'zone', 'photo',
            'capacity', 'is_active', 'is_hot_desk', 'is_vip',
        ]


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
