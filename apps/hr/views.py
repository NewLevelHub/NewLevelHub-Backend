from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse

from apps.companies.models import CompanySettings
from apps.core.permissions import IsCompanyAdmin, IsCompanyMember
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from apps.notifications.models import Notification
from apps.users.models import User
from .models import LeaveRequest, LeaveBalance, OnboardingTemplate, UserOnboardingProgress
from .serializers import (
    LeaveRequestSerializer, LeaveRequestReviewSerializer, LeaveBalanceSerializer,
    LeaveBalanceSetSerializer, LeaveBalanceTeamSerializer,
    OnboardingTemplateSerializer, UserOnboardingProgressSerializer,
)


@extend_schema_view(
    list=extend_schema(
        tags=['HR'],
        summary='List leave requests',
        responses={200: LeaveRequestSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['HR'],
        summary='Submit leave request',
        request=LeaveRequestSerializer,
        responses={
            201: LeaveRequestSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company members only'),
        },
    ),
    retrieve=extend_schema(
        tags=['HR'],
        summary='Get leave request details',
        responses={200: LeaveRequestSerializer, 404: OpenApiResponse(description='Not found')},
    ),
)
class LeaveRequestViewSet(CompanyIsolationMixin, SetCompanyOnCreateMixin, viewsets.ModelViewSet):
    serializer_class = LeaveRequestSerializer
    permission_classes = [IsCompanyMember]
    queryset = LeaveRequest.objects.select_related('user', 'reviewed_by').order_by('-created_at')
    http_method_names = ['get', 'post']
    filterset_fields = ['status', 'leave_type', 'user']

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.role == 'employee':
            qs = qs.filter(user=self.request.user)

        year = self.request.query_params.get('year')
        if year not in (None, ''):
            try:
                parsed_year = int(year)
            except (TypeError, ValueError) as exc:
                raise ValidationError({'year': 'Year must be an integer.'}) from exc
            if parsed_year < 1900 or parsed_year > 3000:
                raise ValidationError({'year': 'Year must be between 1900 and 3000.'})
            qs = qs.filter(start_date__year=parsed_year)

        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, company=self.request.user.company)

    def _resolve_year(self, request):
        year = request.query_params.get('year') or request.data.get('year')
        if year in (None, ''):
            return timezone.now().year
        try:
            parsed_year = int(year)
        except (TypeError, ValueError) as exc:
            raise ValidationError({'year': 'Year must be an integer.'}) from exc
        if parsed_year < 1900 or parsed_year > 3000:
            raise ValidationError({'year': 'Year must be between 1900 and 3000.'})
        return parsed_year

    def _default_total_days_for_user(self, user):
        if not user.company_id:
            return 24
        try:
            return CompanySettings.objects.get(company_id=user.company_id).vacation_days_per_year
        except CompanySettings.DoesNotExist:
            pass
        return 24

    def _get_or_create_balance(self, user, year):
        return LeaveBalance.objects.get_or_create(
            user=user,
            year=year,
            defaults={'total_days': self._default_total_days_for_user(user)},
        )

    @extend_schema(
        tags=['HR'],
        summary='Approve / reject leave request',
        request=LeaveRequestReviewSerializer,
        responses={
            200: LeaveRequestSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin only'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='review', permission_classes=[IsAuthenticated])
    def review(self, request, pk=None):
        if request.user.role != 'company_admin':
            raise PermissionDenied('Only company_admin can review leave requests.')

        ser = LeaveRequestReviewSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        new_status = ser.validated_data['status']

        with transaction.atomic():
            leave = (
                self.get_queryset()
                .select_related(None)
                .select_for_update()
                .select_related('user')
                .filter(pk=pk)
                .first()
            )
            if leave is None:
                raise NotFound('Not found.')
            old_status = leave.status

            if new_status == 'approved':
                # Lock the user row to serialize concurrent approvals for the same employee.
                User.objects.select_for_update().only('id').get(pk=leave.user_id)
                overlap_exists = LeaveRequest.objects.filter(
                    user_id=leave.user_id,
                    status='approved',
                    start_date__lte=leave.end_date,
                    end_date__gte=leave.start_date,
                ).exclude(pk=leave.pk).exists()
                if overlap_exists:
                    raise ValidationError(
                        {'non_field_errors': ['Cannot approve leave on dates overlapping with approved leave.']}
                    )

                if leave.leave_type not in ('sick_leave', 'remote') and old_status != 'approved':
                    balance, _ = self._get_or_create_balance(leave.user, leave.start_date.year)
                    remaining_days = max(balance.total_days - balance.used_days, 0)
                    if leave.duration_days > remaining_days:
                        raise ValidationError({'non_field_errors': ['Not enough leave balance for selected dates.']})

            leave.status = new_status
            leave.review_comment = ser.validated_data.get('review_comment', '')
            leave.reviewed_by = request.user
            leave.reviewed_at = timezone.now()
            leave.save()

            if new_status == 'approved':
                notif_type = 'leave_approved'
                notif_status_text = 'одобрена'
            else:
                notif_type = 'leave_rejected'
                notif_status_text = 'отклонена'

            Notification.objects.create(
                user=leave.user,
                notification_type=notif_type,
                title=f'Ваша заявка на отпуск {notif_status_text}',
                body=leave.review_comment,
                url='/leave',
            )

            if leave.leave_type not in ('sick_leave', 'remote'):
                balance, _ = self._get_or_create_balance(leave.user, leave.start_date.year)
                if old_status != 'approved' and new_status == 'approved':
                    balance.used_days += leave.duration_days
                    balance.save(update_fields=['used_days', 'updated_at'])
                elif old_status == 'approved' and new_status != 'approved':
                    balance.used_days = max(balance.used_days - leave.duration_days, 0)
                    balance.save(update_fields=['used_days', 'updated_at'])

        return Response(LeaveRequestSerializer(leave).data)

    @extend_schema(
        tags=['HR'],
        summary='My leave balance',
        responses={
            200: LeaveBalanceSerializer,
            401: OpenApiResponse(description='Not authenticated'),
        },
    )
    @action(detail=False, methods=['get'], url_path='balance')
    def balance(self, request):
        year = self._resolve_year(request)
        bal, _ = self._get_or_create_balance(request.user, year)
        return Response(LeaveBalanceSerializer(bal).data)

    @extend_schema(
        tags=['HR'],
        summary='Set employee yearly leave balance',
        request=LeaveBalanceSetSerializer,
        responses={
            200: LeaveBalanceTeamSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin only'),
        },
    )
    @action(detail=False, methods=['post'], url_path='balance/set', permission_classes=[IsCompanyAdmin])
    def set_balance(self, request):
        ser = LeaveBalanceSetSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        target_user_id = ser.validated_data['user_id']
        year = ser.validated_data['year']
        total_days = ser.validated_data['total_days']
        target_user = User.objects.select_related('company').get(id=target_user_id)

        if request.user.role != 'superadmin' and target_user.company_id != request.user.company_id:
            raise PermissionDenied('You can only manage balances for your company employees.')

        balance, _ = self._get_or_create_balance(target_user, year)
        balance.total_days = total_days
        balance.save(update_fields=['total_days', 'updated_at'])
        return Response(LeaveBalanceTeamSerializer(balance).data)

    @extend_schema(
        tags=['HR'],
        summary='Team leave balances',
        responses={
            200: LeaveBalanceTeamSerializer(many=True),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin only'),
        },
    )
    @action(detail=False, methods=['get'], url_path='balance/team', permission_classes=[IsCompanyAdmin])
    def team_balance(self, request):
        year = self._resolve_year(request)
        if request.user.role == 'superadmin':
            employees = User.objects.filter(role='employee')
        else:
            employees = User.objects.filter(
                company_id=request.user.company_id,
                role='employee',
            )

        balances = []
        for employee in employees.select_related('company'):
            bal, _ = self._get_or_create_balance(employee, year)
            balances.append(bal)

        return Response(LeaveBalanceTeamSerializer(balances, many=True).data)


@extend_schema_view(
    list=extend_schema(
        tags=['HR'],
        summary='List onboarding templates',
        responses={200: OnboardingTemplateSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['HR'],
        summary='Create onboarding template',
        request=OnboardingTemplateSerializer,
        responses={
            201: OnboardingTemplateSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Company admin only'),
        },
    ),
    partial_update=extend_schema(
        tags=['HR'],
        summary='Update onboarding template',
        request=OnboardingTemplateSerializer,
        responses={200: OnboardingTemplateSerializer, 400: OpenApiResponse(description='Validation error')},
    ),
)
class OnboardingTemplateViewSet(CompanyIsolationMixin, viewsets.ModelViewSet):
    serializer_class = OnboardingTemplateSerializer
    permission_classes = [IsCompanyAdmin]

    def get_queryset(self):
        return OnboardingTemplate.objects.prefetch_related('steps')

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


@extend_schema_view(
    list=extend_schema(
        tags=['HR'],
        summary='My onboarding progress',
        responses={200: UserOnboardingProgressSerializer(many=True)},
    ),
)
class UserOnboardingProgressViewSet(viewsets.ModelViewSet):
    serializer_class = UserOnboardingProgressSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch']

    def get_queryset(self):
        return UserOnboardingProgress.objects.filter(user=self.request.user)

    @extend_schema(
        tags=['HR'],
        summary='Mark onboarding step as completed',
        request=None,
        responses={
            200: UserOnboardingProgressSerializer,
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='complete')
    def complete_step(self, request, pk=None):
        progress = self.get_object()
        progress.is_completed = True
        progress.completed_at = timezone.now()
        progress.save()
        return Response(UserOnboardingProgressSerializer(progress).data)
