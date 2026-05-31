from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError as DRFValidationError
from apps.companies.models import CompanySettings
from apps.core.exceptions import raise_validation_error
from apps.users.models import User
from .models import LeaveRequest, LeaveBalance, OnboardingTemplate, OnboardingStep, UserOnboardingProgress


class LeaveRequestSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    duration_days = serializers.IntegerField(read_only=True)
    reviewer = serializers.PrimaryKeyRelatedField(source='reviewed_by', read_only=True)
    assigned_reviewer_name = serializers.CharField(
        source='assigned_reviewer.full_name', read_only=True, default=None,
    )

    class Meta:
        model = LeaveRequest
        fields = [
            'id', 'user', 'user_name', 'company', 'leave_type', 'status',
            'start_date', 'end_date', 'duration_days', 'comment',
            'assigned_reviewer', 'assigned_reviewer_name',
            'reviewed_by', 'reviewer', 'review_comment', 'reviewed_at',
            'created_at',
        ]
        read_only_fields = [
            'id', 'user', 'company', 'status',
            'reviewed_by', 'review_comment', 'reviewed_at', 'created_at',
        ]

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
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    remaining_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = LeaveBalance
        fields = ['user_id', 'user_name', 'year', 'total_days', 'used_days', 'remaining_days']


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
        fields = ['id', 'name', 'is_active', 'is_default', 'steps', 'created_at']
        read_only_fields = ['id', 'is_default', 'created_at']

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
