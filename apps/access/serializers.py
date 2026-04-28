from rest_framework import serializers
from datetime import timedelta
from io import BytesIO
from django.core.files.base import ContentFile
import qrcode

from .models import GuestPass, AccessLog
from . import tasks


class GuestPassCreateSerializer(serializers.ModelSerializer):
    purpose = serializers.CharField(source='visit_purpose')
    is_single_use = serializers.BooleanField(write_only=True)

    class Meta:
        model = GuestPass
        fields = [
            'guest_name', 'guest_email', 'guest_phone', 'purpose',
            'valid_from', 'valid_until', 'is_single_use',
        ]

    def validate(self, attrs):
        request = self.context['request']
        user = request.user
        valid_from = attrs['valid_from']
        valid_until = attrs['valid_until']

        if valid_until <= valid_from:
            raise serializers.ValidationError({'valid_until': 'Must be later than valid_from.'})

        if valid_until > valid_from + timedelta(days=30):
            raise serializers.ValidationError({'valid_until': 'Cannot be more than 30 days from valid_from.'})

        if attrs['guest_email'].strip().lower() == user.email.strip().lower():
            raise serializers.ValidationError({'guest_email': 'Cannot create a guest pass for yourself.'})

        active_count = GuestPass.objects.filter(
            company=user.company,
            guest_email=attrs['guest_email'],
            status='active',
        ).count()
        if active_count >= 2:
            raise serializers.ValidationError({'guest_email': 'Guest can have at most 2 active passes.'})

        if user.role == 'guest':
            raise serializers.ValidationError('Guests cannot create passes.')

        return attrs

    def _generate_qr_image(self, guest_pass):
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(str(guest_pass.qr_code))
        qr.make(fit=True)
        image = qr.make_image(fill_color='black', back_color='white')
        image_buffer = BytesIO()
        image.save(image_buffer, format='PNG')
        image_buffer.seek(0)
        image_name = f'{guest_pass.qr_code}.png'
        guest_pass.qr_image.save(image_name, ContentFile(image_buffer.read()), save=False)

    def create(self, validated_data):
        user = self.context['request'].user
        is_single_use = validated_data.pop('is_single_use')
        validated_data['usage_type'] = 'single' if is_single_use else 'multi'
        validated_data['created_by'] = user
        validated_data['company'] = user.company
        guest_pass = super().create(validated_data)

        self._generate_qr_image(guest_pass)
        guest_pass.save(update_fields=['qr_image'])

        tasks.send_guest_pass_email.delay(guest_pass.id)
        return guest_pass


class GuestPassSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True)
    is_valid = serializers.BooleanField(read_only=True)
    purpose = serializers.CharField(source='visit_purpose', read_only=True)
    is_single_use = serializers.SerializerMethodField()
    qr_image = serializers.ImageField(read_only=True)

    class Meta:
        model = GuestPass
        fields = [
            'id', 'created_by', 'created_by_name', 'company',
            'guest_name', 'guest_email', 'guest_phone', 'purpose',
            'qr_code', 'qr_image', 'status', 'usage_type', 'is_single_use', 'times_used',
            'valid_from', 'valid_until', 'is_valid',
            'created_at',
        ]
        read_only_fields = ['id', 'created_by', 'company', 'qr_code', 'qr_image', 'times_used', 'created_at']

    def get_is_single_use(self, obj):
        return obj.usage_type == 'single'


class GuestPassValidateSerializer(serializers.Serializer):
    qr_code = serializers.UUIDField()


class AccessLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccessLog
        fields = [
            'id', 'guest_pass', 'user', 'checked_by',
            'entry_point', 'method', 'is_entry', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']
