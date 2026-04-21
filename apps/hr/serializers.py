from rest_framework import serializers
from apps.users.models import User
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
        fields = ['year', 'total_days', 'used_days', 'remaining_days']


class LeaveBalanceSetSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    year = serializers.IntegerField(min_value=1900, max_value=3000)
    total_days = serializers.IntegerField(min_value=0)

    def validate_user_id(self, value):
        if not User.objects.filter(id=value).exists():
            raise serializers.ValidationError('User not found.')
        return value


class LeaveBalanceTeamSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    remaining_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = LeaveBalance
        fields = ['user_id', 'user_name', 'year', 'total_days', 'used_days', 'remaining_days']


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
