from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse

from apps.companies.models import Company, CompanySettings
from apps.core.exceptions import LocalizedError, raise_validation_error
from apps.core.i18n import translate, get_lang
from apps.core.permissions import IsCompanyAdmin, IsCompanyMember
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from apps.notifications.utils import create_notification
from apps.users.models import User
from .models import (
    LeaveRequest, LeaveBalance, OnboardingTemplate, OnboardingStep,
    UserOnboardingProgress, OnboardingAssignment,
)
from .serializers import (
    LeaveRequestSerializer, LeaveRequestReviewSerializer, LeaveBalanceSerializer,
    LeaveBalanceSetSerializer, LeaveBalanceTeamSerializer,
    OnboardingStepSerializer, OnboardingTemplateSerializer,
    OnboardingAssignmentCreateSerializer,
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
    queryset = LeaveRequest.objects.select_related('user', 'reviewed_by', 'assigned_reviewer').order_by('-created_at')
    http_method_names = ['get', 'post', 'patch']
    filterset_fields = ['status', 'leave_type', 'user']

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.role == 'employee':
            qs = qs.filter(user=self.request.user)

        year = self.request.query_params.get('year')
        if year not in (None, ''):
            try:
                parsed_year = int(year)
            except (TypeError, ValueError):
                raise_validation_error('year', 'hr.year_not_integer')
            if parsed_year < 1900 or parsed_year > 3000:
                raise_validation_error('year', 'hr.year_out_of_range')
            qs = qs.filter(start_date__year=parsed_year)

        return qs

    def perform_create(self, serializer):
        leave_request = serializer.save(user=self.request.user, company=self.request.user.company)

        # Notify either the explicitly assigned reviewer, or all company admins.
        from apps.notifications.tasks import send_notification_email
        employee = self.request.user
        company = employee.company
        if company:
            if leave_request.assigned_reviewer_id:
                admins = User.objects.filter(pk=leave_request.assigned_reviewer_id, is_active=True)
            else:
                admins = User.objects.filter(
                    company=company,
                    role='company_admin',
                    is_active=True,
                ).exclude(pk=employee.pk)
            for admin in admins:
                create_notification(
                    user=admin,
                    notification_type='leave_review',
                    title=f'Заявка на отпуск от {employee.full_name}',
                    message=f'{employee.full_name} подал(а) заявку на отпуск.',
                    link='/hr/leaves',
                )
                send_notification_email.delay(
                    admin.id,
                    'leave_review',
                    {
                        'subject': 'Заявка на отпуск требует рассмотрения',
                        'employee_name': employee.full_name,
                        'leave_type': leave_request.leave_type,
                        'start_date': str(leave_request.start_date),
                        'end_date': str(leave_request.end_date),
                        'action_url': '/hr/leaves',
                    },
                )

    def _resolve_year(self, request):
        year = request.query_params.get('year') or request.data.get('year')
        if year in (None, ''):
            return timezone.now().year
        try:
            parsed_year = int(year)
        except (TypeError, ValueError):
            raise_validation_error('year', 'hr.year_not_integer')
        if parsed_year < 1900 or parsed_year > 3000:
            raise_validation_error('year', 'hr.year_out_of_range')
        return parsed_year

    def partial_update(self, request, *args, **kwargs):
        lang = get_lang(request)
        leave = self.get_object()

        if leave.user != request.user:
            raise PermissionDenied(translate('hr.leave_edit_own_only', lang))
        if leave.status != 'pending':
            raise PermissionDenied(translate('hr.leave_edit_pending_only', lang))

        ser = LeaveRequestSerializer(
            leave, data=request.data, partial=True, context={'request': request}
        )
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)

    @extend_schema(
        tags=['HR'],
        summary='Cancel own pending leave request',
        responses={
            200: LeaveRequestSerializer,
            403: OpenApiResponse(description='Not owner or not pending'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='cancel', permission_classes=[IsCompanyMember])
    def cancel(self, request, pk=None):
        lang = get_lang(request)
        leave = self.get_object()

        if leave.user != request.user:
            raise PermissionDenied(translate('hr.leave_edit_own_only', lang))
        if leave.status != 'pending':
            raise PermissionDenied(translate('hr.leave_edit_pending_only', lang))

        leave.status = 'cancelled'
        leave.save(update_fields=['status', 'updated_at'])
        return Response(LeaveRequestSerializer(leave).data)

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
            lang = get_lang(request)
            raise PermissionDenied(translate('hr.review_admin_only', lang))

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
                raise NotFound()

            lang = get_lang(request)
            if leave.user_id == request.user.id:
                raise PermissionDenied(translate('hr.leave_review_self_forbidden', lang))
            if (
                leave.assigned_reviewer_id is not None
                and leave.assigned_reviewer_id != request.user.id
            ):
                raise PermissionDenied(translate('hr.leave_review_not_assigned', lang))

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
                        {'non_field_errors': [{'_i18n': True, 'key': 'hr.leave_approve_overlap', 'params': {}}]}
                    )

                if leave.leave_type not in ('sick_leave', 'remote') and old_status != 'approved':
                    balance, _ = self._get_or_create_balance(leave.user, leave.start_date.year)
                    remaining_days = max(balance.total_days - balance.used_days, 0)
                    if leave.duration_days > remaining_days:
                        raise ValidationError(
                            {'non_field_errors': [
                                {'_i18n': True, 'key': 'hr.leave_approve_balance_insufficient', 'params': {}}
                            ]}
                        )

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

            create_notification(
                user=leave.user,
                notification_type=notif_type,
                title=f'Ваша заявка на отпуск {notif_status_text}',
                message=leave.review_comment,
                link='/leave',
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
            lang = get_lang(request)
            raise PermissionDenied(translate('hr.balance_own_company_only', lang))

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
    pagination_class = None

    def get_queryset(self):
        queryset = OnboardingTemplate.objects.prefetch_related('steps').order_by('id')
        if self.request.user.role == 'superadmin':
            company_id = self.request.query_params.get('company_id')
            if company_id and self.action == 'list':
                return queryset.filter(company_id=company_id)
            return queryset
        return queryset.filter(company=self.request.user.company)

    def perform_create(self, serializer):
        user = self.request.user
        if user.role != 'superadmin':
            serializer.save(company=user.company)
            return
        company_id = self.request.query_params.get('company_id')
        if not company_id:
            raise ValidationError({'company_id': 'Superadmin must provide company_id query parameter.'})
        try:
            company = Company.objects.get(pk=company_id)
        except Company.DoesNotExist:
            raise ValidationError({'company_id': 'Company not found.'})
        serializer.save(company=company)

    @extend_schema(
        tags=['HR'],
        summary='Set template as default',
        responses={
            200: OnboardingTemplateSerializer,
            403: OpenApiResponse(description='Company admin only'),
            404: OpenApiResponse(description='Template not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='set-default')
    @transaction.atomic
    def set_default(self, request, pk=None):
        template = self.get_object()
        template.set_as_default()
        return Response(self.get_serializer(template).data)


@extend_schema_view(
    list=extend_schema(tags=['HR'], summary='List onboarding steps for a template'),
    retrieve=extend_schema(tags=['HR'], summary='Get onboarding step'),
    create=extend_schema(
        tags=['HR'],
        summary='Add custom onboarding step',
        responses={201: OnboardingStepSerializer, 403: OpenApiResponse(description='Company admin only')},
    ),
    partial_update=extend_schema(
        tags=['HR'],
        summary='Update custom onboarding step',
        responses={
            200: OnboardingStepSerializer,
            400: OpenApiResponse(description='System steps are immutable'),
        },
    ),
    destroy=extend_schema(
        tags=['HR'],
        summary='Delete custom onboarding step',
        responses={
            204: OpenApiResponse(description='Deleted'),
            400: OpenApiResponse(description='System steps are immutable'),
        },
    ),
)
class OnboardingStepViewSet(CompanyIsolationMixin, viewsets.ModelViewSet):
    serializer_class = OnboardingStepSerializer
    permission_classes = [IsCompanyAdmin]
    pagination_class = None
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    company_lookup = 'template__company'
    queryset = OnboardingStep.objects.select_related('template').order_by('position')

    def get_queryset(self):
        return super().get_queryset().filter(
            template_id=self.kwargs['template_pk']
        )

    def perform_create(self, serializer):
        template_pk = self.kwargs['template_pk']
        template = get_object_or_404(
            OnboardingTemplate.objects.filter(company=self.request.user.company),
            pk=template_pk,
        )
        if template.is_system:
            raise LocalizedError(
                code='SYSTEM_STEP_IMMUTABLE',
                i18n_key='hr.onboarding_system_step_immutable',
                http_status=400,
            )
        serializer.save(template=template, is_system=False)
        # Backfill progress rows for all users who have this template assigned.
        for assignment in OnboardingAssignment.objects.filter(template=template).select_related('user'):
            initialize_progress_for_assignment(assignment)

    def _guard_system_step(self, instance):
        if instance.template.is_system:
            raise LocalizedError(
                code='SYSTEM_STEP_IMMUTABLE',
                i18n_key='hr.onboarding_system_step_immutable',
                http_status=400,
            )

    def perform_update(self, serializer):
        self._guard_system_step(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._guard_system_step(instance)
        instance.delete()


def _get_assigned_template(user):
    """Return the onboarding template assigned to this user via OnboardingAssignment (or None)."""
    assignment = OnboardingAssignment.objects.filter(user=user).select_related('template').first()
    return assignment.template if assignment else None


def initialize_progress_for_assignment(assignment):
    """Create UserOnboardingProgress rows for all (non-deleted) steps of the assigned template."""
    for step in assignment.template.steps.order_by('position'):
        UserOnboardingProgress.objects.get_or_create(
            user=assignment.user,
            step=step,
            defaults={'is_completed': False},
        )


def _build_progress_response(user):
    template = _get_assigned_template(user)
    if template is None:
        return {'assigned': False, 'completed': True, 'steps': []}

    progress_items = list(
        UserOnboardingProgress.objects.filter(user=user, step__template=template)
        .select_related('step')
        .order_by('step__position')
    )
    if not progress_items:
        return {'assigned': True, 'completed': True, 'steps': []}

    completed = all(item.is_completed for item in progress_items)
    steps = [
        {
            'id': item.step_id,
            'title': item.step.title,
            'is_completed': item.is_completed,
            'url': item.step.url or None,
        }
        for item in progress_items
    ]
    return {'assigned': True, 'completed': completed, 'steps': steps}


@extend_schema(
    tags=['HR'],
    summary='My onboarding progress',
    responses={200: OpenApiResponse(description='Onboarding progress summary')},
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def onboarding_progress(request):
    return Response(_build_progress_response(request.user))


@extend_schema(
    tags=['HR'],
    summary='Mark onboarding step as completed',
    responses={
        200: OpenApiResponse(description='Step marked completed'),
        401: OpenApiResponse(description='Not authenticated'),
        404: OpenApiResponse(description='Not found'),
    },
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def complete_onboarding_step(request, step_id):
    progress = get_object_or_404(
        UserOnboardingProgress.objects.filter(user=request.user),
        step_id=step_id,
    )
    if not progress.is_completed:
        progress.is_completed = True
        progress.completed_at = timezone.now()
        progress.save(update_fields=['is_completed', 'completed_at', 'updated_at'])
    return Response({'id': progress.step_id, 'is_completed': True})


@extend_schema(
    tags=['HR'],
    summary='Team onboarding progress',
    responses={
        200: OpenApiResponse(description='Company onboarding progress list'),
        403: OpenApiResponse(description='Company admin only'),
    },
)
@api_view(['GET'])
@permission_classes([IsCompanyAdmin])
def onboarding_team_progress(request):
    if not request.user.company_id:
        return Response([])

    # Build a lookup: user_id → assignment (with template pre-fetched)
    assignment_map = {
        a.user_id: a
        for a in OnboardingAssignment.objects.filter(
            user__company_id=request.user.company_id,
        ).select_related('template')
    }

    base_qs = request.user.company.members.exclude(role='superadmin').order_by('id')

    payload = []
    for member in base_qs:
        assignment = assignment_map.get(member.id)
        if assignment is None:
            payload.append({
                'user': member.id,
                'first_name': member.first_name,
                'last_name': member.last_name,
                'avatar': member.avatar.url if member.avatar else None,
                'role': member.role,
                'template_id': None,
                'template_name': None,
                'completed_steps': 0,
                'total_steps': 0,
            })
            continue

        template = assignment.template
        progress_qs = UserOnboardingProgress.objects.filter(
            user=member,
            step__template=template,
        )
        total = progress_qs.count()
        completed = progress_qs.filter(is_completed=True).count()
        payload.append({
            'user': member.id,
            'first_name': member.first_name,
            'last_name': member.last_name,
            'avatar': member.avatar.url if member.avatar else None,
            'role': member.role,
            'template_id': template.id,
            'template_name': template.title,
            'completed_steps': completed,
            'total_steps': total,
        })

    return Response(payload)


@extend_schema(
    tags=['HR'],
    summary='Team member onboarding progress drill-down',
    responses={
        200: OpenApiResponse(description='Step-by-step progress for a specific employee'),
        403: OpenApiResponse(description='Company admin only'),
        404: OpenApiResponse(description='Member not found in company'),
    },
)
@api_view(['GET'])
@permission_classes([IsCompanyAdmin])
def onboarding_team_member_progress(request, user_id):
    if not request.user.company_id:
        return Response({'completed': True, 'steps': []})

    member = get_object_or_404(
        request.user.company.members.exclude(role='superadmin'),
        pk=user_id,
    )

    template = _get_assigned_template(member)
    progress_qs = UserOnboardingProgress.objects.filter(user=member)
    if template is not None:
        progress_qs = progress_qs.filter(step__template=template)

    progress_items = list(
        progress_qs
        .select_related('step')
        .order_by('step__position')
    )

    if not progress_items:
        return Response({
            'user': {
                'id': member.id,
                'first_name': member.first_name,
                'last_name': member.last_name,
                'avatar': member.avatar.url if member.avatar else None,
            },
            'completed_steps': 0,
            'total_steps': 0,
            'steps': [],
        })

    completed_count = sum(1 for item in progress_items if item.is_completed)
    steps = [
        {
            'id': item.step_id,
            'title': item.step.title,
            'is_system': item.step.is_system,
            'is_completed': item.is_completed,
            'completed_at': item.completed_at,
        }
        for item in progress_items
    ]
    return Response({
        'user': {
            'id': member.id,
            'first_name': member.first_name,
            'last_name': member.last_name,
            'avatar': member.avatar.url if member.avatar else None,
        },
        'completed_steps': completed_count,
        'total_steps': len(progress_items),
        'steps': steps,
    })


# ---------------------------------------------------------------------------
# Onboarding assignment endpoints
# ---------------------------------------------------------------------------

@extend_schema(
    tags=['HR'],
    methods=['GET'],
    summary='List all company members with their onboarding assignment',
    responses={200: OpenApiResponse(description='List of members with assignment info')},
)
@extend_schema(
    tags=['HR'],
    methods=['POST'],
    summary='Assign or reassign an onboarding template to an employee',
    request=OnboardingAssignmentCreateSerializer,
    responses={
        201: OpenApiResponse(description='Assignment created/updated'),
        400: OpenApiResponse(description='Validation error'),
        403: OpenApiResponse(description='Company admin only or user not in company'),
    },
)
@api_view(['GET', 'POST'])
@permission_classes([IsCompanyAdmin])
def onboarding_assignments(request):
    """
    GET  /hr/onboarding/assignments/  — list all members with their assignment info
    POST /hr/onboarding/assignments/  — assign/reassign a template to a user
    """
    if request.method == 'GET':
        return _onboarding_assignments_list(request)
    return _onboarding_assignment_create(request)


def _onboarding_assignments_list(request):
    if not request.user.company_id:
        return Response([])

    assignment_map = {
        a.user_id: a
        for a in OnboardingAssignment.objects.filter(
            user__company_id=request.user.company_id,
        ).select_related('template')
    }

    members = request.user.company.members.exclude(role='superadmin').order_by('id')
    payload = []
    for member in members:
        assignment = assignment_map.get(member.id)
        if assignment is None:
            payload.append({
                'user_id': member.id,
                'first_name': member.first_name,
                'last_name': member.last_name,
                'avatar': member.avatar.url if member.avatar else None,
                'position': member.position,
                'template_id': None,
                'template_name': None,
                'completed_steps': 0,
                'total_steps': 0,
                'assigned_at': None,
            })
            continue

        template = assignment.template
        progress_qs = UserOnboardingProgress.objects.filter(user=member, step__template=template)
        total = progress_qs.count()
        completed = progress_qs.filter(is_completed=True).count()
        payload.append({
            'user_id': member.id,
            'first_name': member.first_name,
            'last_name': member.last_name,
            'avatar': member.avatar.url if member.avatar else None,
            'position': member.position,
            'template_id': template.id,
            'template_name': template.title,
            'completed_steps': completed,
            'total_steps': total,
            'assigned_at': assignment.created_at,
        })

    return Response(payload)


def _onboarding_assignment_create(request):
    """Shared logic for POST /hr/onboarding/assignments/."""
    lang = get_lang(request)
    ser = OnboardingAssignmentCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    user_id = ser.validated_data['user_id']
    template_id = ser.validated_data['template_id']
    note = ser.validated_data.get('note', '')

    # Resolve target user — must belong to the same company (superadmin sees all).
    if request.user.role == 'superadmin':
        target_user = get_object_or_404(User, pk=user_id)
    else:
        target_user = User.objects.filter(
            pk=user_id,
            company_id=request.user.company_id,
        ).first()
        if target_user is None:
            raise PermissionDenied(translate('hr.assignment_user_not_in_company', lang))

    # Resolve template — must belong to the same company and be active.
    company_id = target_user.company_id if request.user.role == 'superadmin' else request.user.company_id
    template = OnboardingTemplate.objects.filter(
        pk=template_id,
        company_id=company_id,
        is_active=True,
    ).first()
    if template is None:
        raise PermissionDenied(translate('hr.assignment_template_not_found', lang))

    assignment, _created = OnboardingAssignment.objects.update_or_create(
        user=target_user,
        defaults={
            'template': template,
            'assigned_by': request.user,
            'note': note,
        },
    )
    initialize_progress_for_assignment(assignment)

    progress_qs = UserOnboardingProgress.objects.filter(user=target_user, step__template=template)
    total = progress_qs.count()
    completed = progress_qs.filter(is_completed=True).count()

    return Response({
        'user_id': target_user.id,
        'first_name': target_user.first_name,
        'last_name': target_user.last_name,
        'avatar': target_user.avatar.url if target_user.avatar else None,
        'position': target_user.position,
        'template_id': template.id,
        'template_name': template.title,
        'completed_steps': completed,
        'total_steps': total,
        'assigned_at': assignment.created_at,
        'note': assignment.note,
    }, status=201)


@extend_schema(
    tags=['HR'],
    summary='Get onboarding assignment for a specific employee',
    responses={
        200: OpenApiResponse(description='Employee assignment with progress'),
        403: OpenApiResponse(description='Company admin only'),
        404: OpenApiResponse(description='User not found in company'),
    },
)
@api_view(['GET'])
@permission_classes([IsCompanyAdmin])
def onboarding_assignment_detail(request, user_id):
    """GET /hr/onboarding/assignments/{user_id}/"""
    lang = get_lang(request)

    if request.user.role == 'superadmin':
        member = get_object_or_404(User, pk=user_id)
    else:
        member = User.objects.filter(
            pk=user_id,
            company_id=request.user.company_id,
        ).first()
        if member is None:
            raise PermissionDenied(translate('hr.assignment_user_not_in_company', lang))

    assignment = OnboardingAssignment.objects.filter(user=member).select_related('template').first()
    if assignment is None:
        return Response({
            'user_id': member.id,
            'first_name': member.first_name,
            'last_name': member.last_name,
            'avatar': member.avatar.url if member.avatar else None,
            'position': member.position,
            'template_id': None,
            'template_name': None,
            'completed_steps': 0,
            'total_steps': 0,
            'assigned_at': None,
            'note': '',
        })

    template = assignment.template
    progress_qs = UserOnboardingProgress.objects.filter(user=member, step__template=template)
    total = progress_qs.count()
    completed = progress_qs.filter(is_completed=True).count()

    return Response({
        'user_id': member.id,
        'first_name': member.first_name,
        'last_name': member.last_name,
        'avatar': member.avatar.url if member.avatar else None,
        'position': member.position,
        'template_id': template.id,
        'template_name': template.title,
        'completed_steps': completed,
        'total_steps': total,
        'assigned_at': assignment.created_at,
        'note': assignment.note,
    })


@extend_schema(
    tags=['HR'],
    summary='My onboarding assignment and step-level progress',
    responses={
        200: OpenApiResponse(description='My assignment with steps'),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Company member only'),
    },
)
@api_view(['GET'])
@permission_classes([IsCompanyMember])
def my_onboarding_assignment(request):
    """
    GET /hr/onboarding/my-assignment/

    Returns the current user's assigned template and step-by-step progress.
    If no assignment exists returns {"assigned": false}.
    """
    assignment = OnboardingAssignment.objects.filter(
        user=request.user,
    ).select_related('template').first()

    if assignment is None:
        return Response({'assigned': False})

    template = assignment.template
    progress_items = list(
        UserOnboardingProgress.objects.filter(user=request.user, step__template=template)
        .select_related('step')
        .order_by('step__position')
    )
    total = len(progress_items)
    completed = sum(1 for p in progress_items if p.is_completed)
    steps = [
        {
            'id': p.step_id,
            'title': p.step.title,
            'is_system': p.step.is_system,
            'is_completed': p.is_completed,
            'completed_at': p.completed_at,
        }
        for p in progress_items
    ]
    return Response({
        'assigned': True,
        'template': {'id': template.id, 'name': template.title},
        'completed_steps': completed,
        'total_steps': total,
        'steps': steps,
    })
