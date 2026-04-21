from django.utils import timezone
from rest_framework import serializers
from .models import LeaveRequest, LeaveBalance, OnboardingTemplate, OnboardingStep, UserOnboardingProgress


class LeaveRequestSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    duration_days = serializers.IntegerField(read_only=True)
    reviewer = serializers.PrimaryKeyRelatedField(source='reviewed_by', read_only=True)

    class Meta:
        model = LeaveRequest
        fields = [
            'id', 'user', 'user_name', 'company', 'leave_type', 'status',
            'start_date', 'end_date', 'duration_days', 'comment',
            'reviewed_by', 'reviewer', 'review_comment', 'reviewed_at',
            'created_at',
        ]
        read_only_fields = [
            'id', 'user', 'company', 'status',
            'reviewed_by', 'review_comment', 'reviewed_at', 'created_at',
        ]

    def validate(self, attrs):
        start_date = attrs.get('start_date')
        end_date = attrs.get('end_date')

        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError({'start_date': 'start_date must be less than or equal to end_date.'})

        if start_date and start_date < timezone.localdate():
            raise serializers.ValidationError({'start_date': 'start_date must be today or later.'})

        if start_date and end_date:
            user = self.instance.user if self.instance else self.context['request'].user
            overlap_qs = LeaveRequest.objects.filter(
                user=user,
                status='approved',
                start_date__lte=end_date,
                end_date__gte=start_date,
            )
            if self.instance:
                overlap_qs = overlap_qs.exclude(pk=self.instance.pk)
            if overlap_qs.exists():
                raise serializers.ValidationError(
                    {'non_field_errors': ['Cannot request leave on dates overlapping with approved leave.']}
                )

        return attrs


class LeaveRequestReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['approved', 'rejected'])
    review_comment = serializers.CharField(required=False, default='')


class LeaveBalanceSerializer(serializers.ModelSerializer):
    remaining_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = LeaveBalance
        fields = ['total_days', 'used_days', 'remaining_days']


class OnboardingStepSerializer(serializers.ModelSerializer):
    order = serializers.IntegerField(source='position')

    class Meta:
        model = OnboardingStep
        fields = ['id', 'title', 'description', 'url', 'order']
        read_only_fields = ['id']


class OnboardingTemplateSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='title')
    steps = OnboardingStepSerializer(many=True)

    class Meta:
        model = OnboardingTemplate
        fields = ['id', 'name', 'is_active', 'steps', 'created_at']
        read_only_fields = ['id', 'created_at']

    def create(self, validated_data):
        steps_data = validated_data.pop('steps', [])
        template = OnboardingTemplate.objects.create(**validated_data)
        for step_data in steps_data:
            OnboardingStep.objects.create(template=template, **step_data)
        return template

    def update(self, instance, validated_data):
        steps_data = validated_data.pop('steps', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if steps_data is not None:
            instance.steps.all().delete()
            for step_data in steps_data:
                OnboardingStep.objects.create(template=instance, **step_data)
        return instance


class UserOnboardingProgressSerializer(serializers.ModelSerializer):
    step_title = serializers.CharField(source='step.title', read_only=True)

    class Meta:
        model = UserOnboardingProgress
        fields = ['id', 'step', 'step_title', 'is_completed', 'completed_at']
        read_only_fields = ['id', 'completed_at']
