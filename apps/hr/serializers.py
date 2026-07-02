from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError as DRFValidationError
from apps.companies.models import CompanySettings
from apps.core.exceptions import raise_validation_error
from apps.users.models import User
from apps.users.serializers import UserBriefSerializer
from .constants import SYSTEM_STEPS
from .models import (
    LeaveRequest, LeaveBalance, OnboardingTemplate, OnboardingStep,
    UserOnboardingProgress, OnboardingAssignment,
)


class LeaveRequestSerializer(serializers.ModelSerializer):
    # Nested read-only fields — overridden in to_representation; declared here
    # so DRF schema introspection picks up the output shape.
    user = UserBriefSerializer(read_only=True)
    reviewed_by = UserBriefSerializer(read_only=True, allow_null=True)

    # assigned_reviewer accepts an integer FK on write; to_representation swaps
    # it for the nested UserBriefSerializer object on read.
    assigned_reviewer = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        allow_null=True,
        required=False,
    )

    duration_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = LeaveRequest
        fields = [
            'id', 'user', 'company', 'leave_type', 'status',
            'start_date', 'end_date', 'duration_days', 'comment',
            'assigned_reviewer',
            'reviewed_by', 'review_comment', 'reviewed_at',
            'created_at',
        ]
        read_only_fields = [
            'id', 'user', 'company', 'status',
            'reviewed_by', 'review_comment', 'reviewed_at', 'created_at',
        ]

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        # Replace the integer PK with a nested user object for both reviewer fields.
        ret['assigned_reviewer'] = (
            UserBriefSerializer(instance.assigned_reviewer).data
            if instance.assigned_reviewer_id is not None
            else None
        )
        return ret

    def _default_total_days_for_user(self, user):
        if not user.company_id:
            return 24
        try:
            return CompanySettings.objects.get(company_id=user.company_id).vacation_days_per_year
        except CompanySettings.DoesNotExist:
            return 24

    def validate(self, attrs):
        # For partial updates, fall back to instance values for fields not in attrs
        if self.instance:
            start_date = attrs.get('start_date', self.instance.start_date)
            end_date = attrs.get('end_date', self.instance.end_date)
            leave_type = attrs.get('leave_type', self.instance.leave_type)
        else:
            start_date = attrs.get('start_date')
            end_date = attrs.get('end_date')
            leave_type = attrs.get('leave_type')

        author = self.instance.user if self.instance else self.context['request'].user
        assigned_reviewer = attrs.get(
            'assigned_reviewer',
            self.instance.assigned_reviewer if self.instance else None,
        )

        if author.role == 'company_admin':
            other_admins_qs = User.objects.filter(
                company_id=author.company_id,
                role='company_admin',
                is_active=True,
            ).exclude(pk=author.pk)
            if not other_admins_qs.exists():
                raise DRFValidationError(
                    {'non_field_errors': [{'_i18n': True, 'key': 'hr.leave_no_other_admin', 'params': {}}]}
                )
            if assigned_reviewer is None:
                raise DRFValidationError(
                    {'assigned_reviewer': [{'_i18n': True, 'key': 'hr.leave_reviewer_required', 'params': {}}]}
                )

        if assigned_reviewer is not None:
            if assigned_reviewer.pk == author.pk:
                raise DRFValidationError(
                    {'assigned_reviewer': [{'_i18n': True, 'key': 'hr.leave_reviewer_is_author', 'params': {}}]}
                )
            if (
                assigned_reviewer.role != 'company_admin'
                or assigned_reviewer.company_id != author.company_id
                or not assigned_reviewer.is_active
            ):
                raise DRFValidationError(
                    {'assigned_reviewer': [{'_i18n': True, 'key': 'hr.leave_reviewer_invalid', 'params': {}}]}
                )

        if start_date and end_date and start_date > end_date:
            raise_validation_error('start_date', 'hr.start_date_after_end_date')

        if start_date and start_date < timezone.localdate():
            raise_validation_error('start_date', 'hr.start_date_in_past')

        if start_date and end_date:
            user = author
            overlap_qs = LeaveRequest.objects.filter(
                user=user,
                status='approved',
                start_date__lte=end_date,
                end_date__gte=start_date,
            )
            if self.instance:
                overlap_qs = overlap_qs.exclude(pk=self.instance.pk)
            if overlap_qs.exists():
                raise DRFValidationError(
                    {'non_field_errors': [{'_i18n': True, 'key': 'hr.leave_dates_overlap', 'params': {}}]}
                )

            if leave_type in ('vacation', 'day_off'):
                duration_days = (end_date - start_date).days + 1
                balance = LeaveBalance.objects.filter(user=user, year=start_date.year).first()
                if balance is None:
                    total_days = self._default_total_days_for_user(user)
                    used_days = 0
                else:
                    total_days = balance.total_days
                    used_days = balance.used_days
                remaining_days = max(total_days - used_days, 0)
                if duration_days > remaining_days:
                    raise DRFValidationError(
                        {'non_field_errors': [{'_i18n': True, 'key': 'hr.leave_balance_insufficient', 'params': {}}]}
                    )

        return attrs


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
            raise_validation_error('user_id', 'hr.user_not_found')
        return value


class LeaveBalanceTeamSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)
    remaining_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = LeaveBalance
        fields = ['user', 'year', 'total_days', 'used_days', 'remaining_days']


class OnboardingStepSerializer(serializers.ModelSerializer):
    order = serializers.IntegerField(source='position')
    url = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='')

    def validate_url(self, value):
        return value or ''

    class Meta:
        model = OnboardingStep
        fields = ['id', 'title', 'description', 'url', 'order', 'is_system']
        read_only_fields = ['id', 'is_system']


class OnboardingTemplateSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='title')
    steps = OnboardingStepSerializer(many=True)

    class Meta:
        model = OnboardingTemplate
        fields = ['id', 'name', 'is_active', 'is_default', 'steps', 'created_at']
        read_only_fields = ['id', 'is_default', 'created_at']

    def create(self, validated_data):
        steps_data = validated_data.pop('steps', [])
        template = OnboardingTemplate.objects.create(**validated_data)
        for system_step in SYSTEM_STEPS:
            OnboardingStep.objects.create(template=template, is_system=True, **system_step)
        custom_position = len(SYSTEM_STEPS) + 1
        for step_data in steps_data:
            step_data['position'] = custom_position
            custom_position += 1
            OnboardingStep.objects.create(template=template, **step_data)
        return template

    def update(self, instance, validated_data):
        steps_data = validated_data.pop('steps', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if steps_data is not None:
            # Preserve system steps; only replace custom ones.
            instance.steps.filter(is_system=False).delete()
            custom_position = len(SYSTEM_STEPS) + 1
            for step_data in steps_data:
                step_data['position'] = custom_position
                custom_position += 1
                OnboardingStep.objects.create(template=instance, **step_data)
        return instance


class UserOnboardingProgressSerializer(serializers.ModelSerializer):
    step_title = serializers.CharField(source='step.title', read_only=True)

    class Meta:
        model = UserOnboardingProgress
        fields = ['id', 'step', 'step_title', 'is_completed', 'completed_at']
        read_only_fields = ['id', 'completed_at']


class OnboardingAssignmentCreateSerializer(serializers.Serializer):
    """Input serializer for POST /hr/onboarding/assignments/."""
    user_id = serializers.IntegerField()
    template_id = serializers.IntegerField()
    note = serializers.CharField(required=False, allow_blank=True, default='')


class OnboardingAssignmentDetailSerializer(serializers.ModelSerializer):
    """Output serializer for GET /hr/onboarding/assignments/ and detail."""
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    avatar = serializers.SerializerMethodField()
    position = serializers.CharField(source='user.position', read_only=True)
    template_id = serializers.IntegerField(source='template.id', read_only=True)
    template_name = serializers.CharField(source='template.title', read_only=True)
    assigned_at = serializers.DateTimeField(source='created_at', read_only=True)
    completed_steps = serializers.IntegerField(read_only=True)
    total_steps = serializers.IntegerField(read_only=True)

    class Meta:
        model = OnboardingAssignment
        fields = [
            'user_id', 'first_name', 'last_name', 'avatar', 'position',
            'template_id', 'template_name',
            'completed_steps', 'total_steps',
            'assigned_at', 'note',
        ]

    def get_avatar(self, obj):
        return obj.user.avatar.url if obj.user.avatar else None
