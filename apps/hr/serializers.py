from rest_framework import serializers
from .models import LeaveRequest, LeaveBalance, OnboardingTemplate, OnboardingStep, UserOnboardingProgress


class LeaveRequestSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    duration_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = LeaveRequest
        fields = [
            'id', 'user', 'user_name', 'company', 'leave_type', 'status',
            'start_date', 'end_date', 'duration_days', 'comment',
            'reviewed_by', 'review_comment', 'reviewed_at',
            'created_at',
        ]
        read_only_fields = [
            'id', 'user', 'company', 'status',
            'reviewed_by', 'review_comment', 'reviewed_at', 'created_at',
        ]


class LeaveRequestReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['approved', 'rejected'])
    review_comment = serializers.CharField(required=False, default='')


class LeaveBalanceSerializer(serializers.ModelSerializer):
    remaining_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = LeaveBalance
        fields = ['total_days', 'used_days', 'remaining_days']


class OnboardingStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = OnboardingStep
        fields = ['id', 'title', 'description', 'url', 'position']
        read_only_fields = ['id']


class OnboardingTemplateSerializer(serializers.ModelSerializer):
    steps = OnboardingStepSerializer(many=True, read_only=True)

    class Meta:
        model = OnboardingTemplate
        fields = ['id', 'title', 'is_active', 'steps', 'created_at']
        read_only_fields = ['id', 'created_at']


class UserOnboardingProgressSerializer(serializers.ModelSerializer):
    step_title = serializers.CharField(source='step.title', read_only=True)

    class Meta:
        model = UserOnboardingProgress
        fields = ['id', 'step', 'step_title', 'is_completed', 'completed_at']
        read_only_fields = ['id', 'completed_at']
