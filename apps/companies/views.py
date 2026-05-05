import logging
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets, filters
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiResponse,
    OpenApiParameter,
    OpenApiTypes,
)

from apps.bookings.models import Booking
from apps.access.models import GuestPass
from apps.companies.limits import get_company_storage_used_bytes
from apps.core.permissions import IsSuperAdmin, IsCompanyAdmin, IsCompanyMember
from apps.crm.models import Board, Task
from apps.hr.models import LeaveRequest
from apps.users.models import User
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from .filters import CompanyFilter, CompanyMemberFilter, CompanyDirectoryFilter
from .models import Company, CompanySettings, Invitation
from .serializers import (
    CompanySerializer,
    CompanyDetailSerializer,
    CompanyCreateSerializer,
    CompanyUpdateSerializer,
    CompanyAdminUpdateSerializer,
    CompanySettingsSerializer,
    InvitationCreateSerializer,
    InvitationListSerializer,
    CompanyMemberSerializer,
    CompanyDirectoryListSerializer,
    CompanyDirectoryDetailSerializer,
    CompanyMemberActivitySerializer,
    MemberDeactivateSerializer,
    MemberRemoveSerializer,
    OnboardingStatusSerializer,
)
from .tasks import send_invitation_email

logger = logging.getLogger(__name__)


CALENDAR_EVENT_TYPES = {'booking', 'task_deadline', 'leave', 'guest_visit'}


def _blacklist_user_tokens(user):
    """Blacklist all outstanding refresh tokens for the given user."""
    outstanding = OutstandingToken.objects.filter(user=user).exclude(
        blacklistedtoken__isnull=False
    )
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=t) for t in outstanding],
        ignore_conflicts=True,
    )


def _parse_bool_query_param(raw_value, field_name):
    if raw_value in (None, ''):
        return None
    normalized = str(raw_value).strip().lower()
    if normalized in ('true', '1'):
        return True
    if normalized in ('false', '0'):
        return False
    raise ValidationError({field_name: 'Must be a boolean: true/false.'})


def _parse_date_query_param(raw_value, field_name):
    if not raw_value:
        raise ValidationError({field_name: 'This query parameter is required (YYYY-MM-DD).'})
    try:
        return datetime.strptime(str(raw_value), '%Y-%m-%d').date()
    except (TypeError, ValueError) as exc:
        raise ValidationError({field_name: 'Invalid date format. Use YYYY-MM-DD.'}) from exc


def _day_bounds(local_day):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(local_day, time.min), tz)
    end = timezone.make_aware(datetime.combine(local_day + timedelta(days=1), time.min), tz)
    return start, end


def _resolve_calendar_company(request, company_id):
    company_qs = Company.objects.all()
    if request.user.role in ('company_admin', 'employee'):
        if request.user.company_id != company_id:
            raise PermissionDenied('You can only access calendar of your own company.')
        company_qs = company_qs.filter(id=request.user.company_id)
    return get_object_or_404(company_qs, id=company_id)


def _serialize_calendar_user(user):
    return {'id': user.id, 'full_name': user.full_name}


def _build_company_calendar_events(*, company, date_from, date_to, user_id=None, event_type=None):
    range_start, _ = _day_bounds(date_from)
    _, range_end = _day_bounds(date_to)
    events = []

    if event_type in (None, 'booking'):
        bookings = Booking.objects.filter(
            company_id=company.id,
            status='confirmed',
            start_time__lt=range_end,
            end_time__gt=range_start,
        ).select_related('resource', 'user')
        if user_id is not None:
            bookings = bookings.filter(user_id=user_id)
        for booking in bookings:
            events.append({
                'type': 'booking',
                'title': booking.resource.name,
                'start': booking.start_time.isoformat(),
                'end': booking.end_time.isoformat(),
                'user': _serialize_calendar_user(booking.user),
            })

    if event_type in (None, 'task_deadline'):
        tasks = Task.objects.filter(
            column__board__company_id=company.id,
            is_archived=False,
            deadline__isnull=False,
            deadline__gte=range_start,
            deadline__lt=range_end,
        ).select_related('assignee', 'created_by')
        if user_id is not None:
            # "My" task deadlines in calendar are tied to the current assignee.
            # A task must disappear from "Только мои" after reassignment.
            tasks = tasks.filter(assignee_id=user_id)
        for task in tasks:
            task_user = task.assignee or task.created_by
            if task_user is None:
                continue
            events.append({
                'type': 'task_deadline',
                'title': task.title,
                'start': task.deadline.isoformat(),
                'end': task.deadline.isoformat(),
                'user': _serialize_calendar_user(task_user),
            })

    if event_type in (None, 'leave'):
        leaves = LeaveRequest.objects.filter(
            company_id=company.id,
            status='approved',
            start_date__lte=date_to,
            end_date__gte=date_from,
        ).select_related('user')
        if user_id is not None:
            leaves = leaves.filter(user_id=user_id)
        for leave in leaves:
            leave_start, _ = _day_bounds(leave.start_date)
            _, leave_end = _day_bounds(leave.end_date)
            events.append({
                'type': 'leave',
                'title': f'{leave.user.full_name} — {leave.leave_type}',
                'start': leave_start.isoformat(),
                'end': leave_end.isoformat(),
                'user': _serialize_calendar_user(leave.user),
            })

    if event_type in (None, 'guest_visit'):
        guest_passes = GuestPass.objects.filter(
            company_id=company.id,
            valid_from__lt=range_end,
            valid_until__gt=range_start,
        ).select_related('created_by')
        if user_id is not None:
            guest_passes = guest_passes.filter(created_by_id=user_id)
        for guest_pass in guest_passes:
            events.append({
                'type': 'guest_visit',
                'title': guest_pass.guest_name,
                'start': guest_pass.valid_from.isoformat(),
                'end': guest_pass.valid_until.isoformat(),
                'user': _serialize_calendar_user(guest_pass.created_by),
            })

    return sorted(events, key=lambda item: (item['start'], item['end'], item['type']))


@extend_schema_view(
    list=extend_schema(
        tags=['Companies'],
        summary='List companies',
        responses={
            200: CompanySerializer(many=True),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(
                description=(
                    'Guest: forbidden. employee / company_admin without company_id: '
                    'wrapped error detail includes code `company_not_assigned`.'
                ),
            ),
        },
    ),
    retrieve=extend_schema(
        tags=['Companies'],
        summary='Get company details',
        responses={
            200: CompanyDetailSerializer,
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Company not found'),
        },
    ),
    create=extend_schema(
        tags=['Companies'],
        summary='Create company (superadmin only)',
        # Explicit multipart/form-data so Swagger renders logo as a file picker
        # (type: string, format: binary) instead of a plain text field.
        request={'multipart/form-data': CompanyCreateSerializer},
        responses={
            201: CompanyDetailSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Superadmin only'),
        },
    ),
    update=extend_schema(
        tags=['Companies'],
        summary='Full update company',
        # multipart/form-data required because the body may contain a logo file.
        # Superadmin surface shown; company_admin is restricted to the
        # CompanyAdminUpdateSerializer subset at runtime.
        request={'multipart/form-data': CompanyUpdateSerializer},
        responses={
            200: CompanyDetailSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Forbidden'),
        },
    ),
    partial_update=extend_schema(
        tags=['Companies'],
        summary='Partial update company',
        # multipart/form-data required because the body may contain a logo file.
        # Superadmin surface shown; company_admin is restricted to the
        # CompanyAdminUpdateSerializer subset at runtime.
        request={'multipart/form-data': CompanyUpdateSerializer},
        responses={
            200: CompanyDetailSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Forbidden'),
        },
    ),
    destroy=extend_schema(
        tags=['Companies'],
        summary='Delete company (superadmin) — requires ?confirm=true',
        parameters=[
            OpenApiParameter(
                name='confirm',
                location=OpenApiParameter.QUERY,
                description='Must be "true" to confirm hard deletion.',
                required=False,
                type=str,
            ),
        ],
        responses={
            204: OpenApiResponse(description='Deleted'),
            400: OpenApiResponse(description='Confirmation required'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='Company not found'),
        },
    ),
)
class CompanyViewSet(viewsets.ModelViewSet):
    queryset = Company.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = CompanyFilter
    search_fields = ['name']
    ordering_fields = ['name', 'created_at', 'plan']

    def get_permissions(self):
        if self.action in ('create', 'destroy', 'deactivate', 'activate'):
            return [IsSuperAdmin()]
        if self.action in ('update', 'partial_update',
                           'deactivate_member', 'activate_member', 'remove_member'):
            # Both superadmin and company_admin may perform these actions; the
            # views themselves enforce additional role-based checks (e.g.
            # only superadmin can remove another company_admin).
            return [IsCompanyAdmin()]
        if self.action in ('onboarding_status', 'skip_onboarding'):
            # company_admin (own company) and superadmin; employees/guests blocked
            return [IsCompanyAdmin()]
        # list / retrieve / custom actions — company members only; guests get 403
        return [IsCompanyMember()]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return Company.objects.all().order_by('-id')
        if user.company_id:
            return Company.objects.filter(id=user.company_id).order_by('-id')
        return Company.objects.none().order_by('-id')

    def get_serializer_class(self):
        if self.action == 'create':
            return CompanyCreateSerializer
        if self.action == 'retrieve':
            return CompanyDetailSerializer
        if self.action in ('update', 'partial_update'):
            # get_serializer() selects the correct class per role;
            # this fallback covers superadmin.
            return CompanyUpdateSerializer
        return CompanySerializer

    def get_serializer(self, *args, **kwargs):
        """
        For PATCH/PUT:
          - company_admin  → CompanyAdminUpdateSerializer (restricted fields)
          - superadmin     → CompanyUpdateSerializer (all writable Company fields)
        For all other actions the default dispatch via get_serializer_class() applies.
        """
        if self.action in ('update', 'partial_update'):
            user = self.request.user
            kwargs.setdefault('context', self.get_serializer_context())
            if user.role == 'company_admin':
                return CompanyAdminUpdateSerializer(*args, **kwargs)
            # superadmin (IsCompanyAdmin also passes superadmin)
            return CompanyUpdateSerializer(*args, **kwargs)
        return super().get_serializer(*args, **kwargs)

    def create(self, request, *args, **kwargs):
        """
        Override to return CompanyDetailSerializer in the 201 response body
        instead of the write-only CompanyCreateSerializer.
        """
        write_serializer = self.get_serializer(data=request.data)
        write_serializer.is_valid(raise_exception=True)
        self.perform_create(write_serializer)
        instance = write_serializer.instance
        read_serializer = CompanyDetailSerializer(
            instance, context=self.get_serializer_context()
        )
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        """
        Override to return CompanyDetailSerializer in the response body regardless
        of which write serializer was used for validation/save.
        """
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        write_serializer = self.get_serializer(instance, data=request.data, partial=partial)
        write_serializer.is_valid(raise_exception=True)
        self.perform_update(write_serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

        read_serializer = CompanyDetailSerializer(
            instance, context=self.get_serializer_context()
        )
        return Response(read_serializer.data)

    # ------------------------------------------------------------------
    # Custom actions
    # ------------------------------------------------------------------

    @extend_schema(
        tags=['Companies'],
        summary='Get or update company settings',
        request=CompanySettingsSerializer,
        responses={
            200: CompanySettingsSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Company not found'),
        },
    )
    @action(detail=True, methods=['get', 'patch'], url_path='settings',
            permission_classes=[IsCompanyMember])
    def company_settings(self, request, pk=None):
        company = self.get_object()

        if request.method == 'PATCH':
            # Only company_admin of the same company or superadmin may write.
            user = request.user
            if user.role == 'employee':
                raise PermissionDenied('Employees cannot update company settings.')
            if user.role == 'company_admin' and user.company_id != int(pk):
                raise PermissionDenied('You can only update settings for your own company.')

        settings_obj, _ = CompanySettings.objects.get_or_create(company=company)

        if request.method == 'PATCH':
            serializer = CompanySettingsSerializer(settings_obj, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            settings_obj.refresh_from_db()

        return Response(CompanySettingsSerializer(settings_obj).data)

    @extend_schema(
        tags=['Companies'],
        summary='List company members',
        parameters=[
            OpenApiParameter(
                name='role',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter by role: company_admin, employee, guest. '
                            'Global superadmin users are never included in the company roster.',
            ),
            OpenApiParameter(
                name='is_active',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter by active status.',
            ),
            OpenApiParameter(
                name='search',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Search by name or email.',
            ),
            OpenApiParameter(
                name='ordering',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Order by: date_joined, last_login, full_name '
                            '(prefix with - for descending).',
            ),
        ],
        responses={
            200: CompanyMemberSerializer(many=True),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Company not found'),
        },
    )
    @action(detail=True, methods=['get'], url_path='members',
            permission_classes=[IsCompanyMember])
    def members(self, request, pk=None):
        # Resolve the company without letting global filter_backends interfere.
        # get_object() calls filter_queryset() which would apply SearchFilter
        # to the company queryset using the ?search= param intended for members.
        company_qs = Company.objects.all()
        if request.user.role in ('company_admin', 'employee'):
            if request.user.company_id != int(pk):
                raise PermissionDenied('You can only view members of your own company.')
            company_qs = company_qs.filter(id=request.user.company_id)
        company = get_object_or_404(company_qs, pk=pk)

        qs = company.members.exclude(role='superadmin')

        # Apply filters
        member_filter = CompanyMemberFilter(request.query_params, queryset=qs)
        qs = member_filter.qs

        # Ordering: support date_joined, last_login; full_name requires annotation
        ordering_param = request.query_params.get('ordering', '')
        allowed_ordering = {
            'date_joined': 'date_joined',
            '-date_joined': '-date_joined',
            'last_login': 'last_login',
            '-last_login': '-last_login',
        }
        if ordering_param in allowed_ordering:
            qs = qs.order_by(allowed_ordering[ordering_param])
        elif ordering_param in ('full_name', '-full_name'):
            # Approximate ordering by first name then last name
            prefix = '-' if ordering_param.startswith('-') else ''
            qs = qs.order_by(f'{prefix}first_name', f'{prefix}last_name')

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = CompanyMemberSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = CompanyMemberSerializer(qs, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=['Companies'],
        summary='Get company plan limits and current usage',
        responses={
            200: OpenApiResponse(description='Company limits with usage'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Company not found'),
        },
    )
    @action(detail=True, methods=['get'], url_path='limits',
            permission_classes=[IsCompanyMember])
    def limits(self, request, pk=None):
        company = self.get_object()
        storage_used_bytes = get_company_storage_used_bytes(company)
        used_gb = (Decimal(storage_used_bytes) / Decimal(1024 ** 3)).quantize(
            Decimal('0.0001'), rounding=ROUND_HALF_UP
        )

        return Response({
            'employees': {
                'current': company.employee_count,
                'max': company.max_employees,
            },
            'boards': {
                'current': company.boards.filter(is_archived=False).count(),
                'max': company.max_boards,
            },
            'storage': {
                'used_gb': float(used_gb),
                'used_bytes': storage_used_bytes,
                'limit_gb': company.storage_limit_gb,
            },
        })

    @extend_schema(
        tags=['Companies'],
        summary='Deactivate company and all its members (superadmin)',
        request=None,
        responses={
            200: CompanySerializer,
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='Company not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='deactivate',
            permission_classes=[IsSuperAdmin])
    def deactivate(self, request, pk=None):
        company = self.get_object()
        with transaction.atomic():
            company_qs = Company.objects.select_for_update().filter(pk=company.pk)
            company_qs.update(is_active=False)

            User.objects.select_for_update().filter(company=company).update(is_active=False)

            Booking.objects.select_for_update().filter(
                company=company,
                status='confirmed',
            ).update(status='cancelled')

        company.refresh_from_db()
        return Response(CompanySerializer(company).data)

    @extend_schema(
        tags=['Companies'],
        summary='Activate company and all its members (superadmin)',
        request=None,
        responses={
            200: CompanySerializer,
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='Company not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='activate',
            permission_classes=[IsSuperAdmin])
    def activate(self, request, pk=None):
        company = self.get_object()
        with transaction.atomic():
            Company.objects.select_for_update().filter(pk=company.pk).update(is_active=True)
            User.objects.select_for_update().filter(company=company).update(is_active=True)

        company.refresh_from_db()
        return Response(CompanySerializer(company).data)

    @extend_schema(
        tags=['Companies'],
        summary='Deactivate a company member (company_admin / superadmin)',
        request=MemberDeactivateSerializer,
        responses={
            200: OpenApiResponse(description='User deactivated successfully'),
            400: OpenApiResponse(description='Cannot deactivate yourself / not a member'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin or superadmin only'),
            404: OpenApiResponse(description='Company or user not found'),
        },
        parameters=[
            OpenApiParameter(
                name='user_id',
                location=OpenApiParameter.PATH,
                type=OpenApiTypes.INT,
                description='ID of the user to deactivate.',
            ),
        ],
    )
    @action(
        detail=True,
        methods=['post'],
        url_path='members/(?P<user_id>[0-9]+)/deactivate',
        url_name='member-deactivate',
    )
    def deactivate_member(self, request, pk=None, user_id=None):
        company = self.get_object()
        target = get_object_or_404(User, pk=user_id, company=company)

        if target.pk == request.user.pk:
            return Response(
                {'detail': 'Cannot deactivate yourself'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            target.is_active = False
            target.save(update_fields=['is_active'])
            _blacklist_user_tokens(target)

        return Response({'detail': 'User deactivated successfully'}, status=status.HTTP_200_OK)

    @extend_schema(
        tags=['Companies'],
        summary='Activate a company member (company_admin / superadmin)',
        request=None,
        responses={
            200: OpenApiResponse(description='User activated successfully'),
            400: OpenApiResponse(description='Not a member of this company'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin or superadmin only'),
            404: OpenApiResponse(description='Company or user not found'),
        },
        parameters=[
            OpenApiParameter(
                name='user_id',
                location=OpenApiParameter.PATH,
                type=OpenApiTypes.INT,
                description='ID of the user to activate.',
            ),
        ],
    )
    @action(
        detail=True,
        methods=['post'],
        url_path='members/(?P<user_id>[0-9]+)/activate',
        url_name='member-activate',
    )
    def activate_member(self, request, pk=None, user_id=None):
        company = self.get_object()
        # Allow inactive members to be looked up so they can be re-activated.
        target = get_object_or_404(User.objects.filter(company=company), pk=user_id)

        with transaction.atomic():
            target.is_active = True
            target.save(update_fields=['is_active'])

        return Response({'detail': 'User activated successfully'}, status=status.HTTP_200_OK)

    @extend_schema(
        tags=['Companies'],
        summary='Remove a member from the company (company_admin / superadmin)',
        request=MemberRemoveSerializer,
        responses={
            200: OpenApiResponse(description='User removed from company'),
            400: OpenApiResponse(
                description='Cannot remove yourself / reassign_to is invalid'
            ),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Only superadmin can remove a company admin'),
            404: OpenApiResponse(description='Company or user not found'),
        },
        parameters=[
            OpenApiParameter(
                name='user_id',
                location=OpenApiParameter.PATH,
                type=OpenApiTypes.INT,
                description='ID of the user to remove.',
            ),
            OpenApiParameter(
                name='reassign_to',
                location=OpenApiParameter.QUERY,
                type=OpenApiTypes.INT,
                required=False,
                description='User ID to reassign tasks to. Omit to leave tasks unassigned.',
            ),
        ],
    )
    @action(
        detail=True,
        methods=['delete'],
        url_path='members/(?P<user_id>[0-9]+)',
        url_name='member-remove',
    )
    def remove_member(self, request, pk=None, user_id=None):
        company = self.get_object()
        target = get_object_or_404(User, pk=user_id, company=company)

        if target.pk == request.user.pk:
            return Response(
                {'detail': 'Cannot remove yourself'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Only superadmin may remove a company_admin.
        if target.role == 'company_admin' and request.user.role != 'superadmin':
            return Response(
                {'detail': 'Only superadmin can remove a company admin'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Validate optional reassign_to parameter.
        reassign_to_id = request.query_params.get('reassign_to')
        reassign_to_user = None
        if reassign_to_id is not None:
            try:
                reassign_to_id = int(reassign_to_id)
            except (ValueError, TypeError):
                return Response(
                    {'detail': 'reassign_to must be a valid user ID'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            reassign_to_user = User.objects.filter(
                pk=reassign_to_id, company=company, is_active=True,
            ).first()
            if reassign_to_user is None:
                return Response(
                    {'detail': 'reassign_to must be an active member of the same company'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            # Reassign or unassign tasks in this company's boards.
            tasks_qs = Task.objects.filter(
                assignee=target,
                column__board__company=company,
            )
            tasks_count = tasks_qs.count()
            tasks_qs.update(assignee=reassign_to_user)

            # Strip the user from the company.
            target.company = None
            target.is_active = False
            target.role = 'guest'
            target.save(update_fields=['company', 'is_active', 'role'])

            _blacklist_user_tokens(target)

        return Response(
            {'detail': 'User removed from company', 'tasks_reassigned': tasks_count},
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------
    # Onboarding actions
    # ------------------------------------------------------------------

    @extend_schema(
        tags=['Companies'],
        summary='Get onboarding status for a company',
        responses={
            200: OnboardingStatusSerializer,
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin or superadmin only'),
            404: OpenApiResponse(description='Company not found'),
        },
    )
    @action(detail=True, methods=['get'], url_path='onboarding-status',
            url_name='onboarding-status')
    def onboarding_status(self, request, pk=None):
        company = self._get_company_for_onboarding(request, pk)
        steps = self._compute_onboarding_steps(company)
        settings_obj, _ = CompanySettings.objects.get_or_create(company=company)

        # Auto-complete onboarding when all steps are done and not yet marked.
        all_done = all(step['completed'] for step in steps)
        if all_done and not settings_obj.onboarding_completed:
            settings_obj.onboarding_completed = True
            settings_obj.save(update_fields=['onboarding_completed'])

        data = {
            'completed': settings_obj.onboarding_completed,
            'steps': steps,
        }
        serializer = OnboardingStatusSerializer(data)
        return Response(serializer.data)

    @extend_schema(
        tags=['Companies'],
        summary='Skip onboarding (mark as completed)',
        request=None,
        responses={
            200: OnboardingStatusSerializer,
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin or superadmin only'),
            404: OpenApiResponse(description='Company not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='onboarding-status/skip',
            url_name='onboarding-skip')
    def skip_onboarding(self, request, pk=None):
        company = self._get_company_for_onboarding(request, pk)
        settings_obj, _ = CompanySettings.objects.get_or_create(company=company)
        settings_obj.onboarding_completed = True
        settings_obj.save(update_fields=['onboarding_completed'])
        return Response({'completed': True})

    def _get_company_for_onboarding(self, request, pk):
        """Resolve company and enforce cross-company access for company_admin."""
        if request.user.role == 'company_admin' and request.user.company_id != int(pk):
            raise PermissionDenied('You can only manage onboarding for your own company.')
        return get_object_or_404(Company, pk=pk)

    @staticmethod
    def _compute_onboarding_steps(company):
        """Return the four onboarding step dicts with their completion status."""
        return [
            {
                'key': 'upload_logo',
                'title': 'Upload company logo',
                'completed': bool(company.logo),
            },
            {
                'key': 'fill_description',
                'title': 'Fill company description',
                'completed': bool(company.description),
            },
            {
                'key': 'create_first_board',
                'title': 'Create first board',
                'completed': Board.objects.filter(company=company).exists(),
            },
            {
                'key': 'invite_first_employee',
                'title': 'Invite first employee',
                'completed': User.objects.filter(company=company, role='employee').exists(),
            },
        ]

    def destroy(self, request, *args, **kwargs):
        if request.query_params.get('confirm') != 'true':
            raise ValidationError(
                'Confirmation required. Pass ?confirm=true to proceed.'
            )
        return super().destroy(request, *args, **kwargs)


@extend_schema_view(
    list=extend_schema(
        tags=['Companies'],
        summary='List invitations',
        parameters=[
            OpenApiParameter(
                name='is_used',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter invitations by used status (true/false).',
            ),
            OpenApiParameter(
                name='is_expired',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter invitations by expiration status (true/false).',
            ),
        ],
        responses={
            200: InvitationListSerializer(many=True),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin or superadmin only'),
        },
    ),
    create=extend_schema(
        tags=['Companies'],
        summary='Create invitation',
        request=InvitationCreateSerializer,
        responses={
            201: InvitationListSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin or superadmin only'),
        },
    ),
)
class InvitationViewSet(viewsets.ModelViewSet):
    serializer_class = InvitationListSerializer
    permission_classes = [IsCompanyAdmin]
    filter_backends = []
    http_method_names = ['get', 'post']
    lookup_url_kwarg = 'id'

    @staticmethod
    def _parse_bool_param(raw_value, field_name):
        if raw_value is None:
            return None
        normalized = str(raw_value).strip().lower()
        if normalized in ('true', '1'):
            return True
        if normalized in ('false', '0'):
            return False
        raise ValidationError({field_name: 'Must be a boolean: true/false.'})

    def _get_company(self):
        company_id = self.kwargs.get('company_id')
        if company_id is None:
            raise ValidationError({'company': 'company_id is required in URL.'})

        base_qs = Company.objects.all()
        if self.request.user.role != 'superadmin':
            base_qs = base_qs.filter(id=self.request.user.company_id)
        return get_object_or_404(base_qs, id=company_id)

    def get_queryset(self):
        company = self._get_company()
        qs = Invitation.objects.filter(company=company).select_related('invited_by')

        is_used = self._parse_bool_param(self.request.query_params.get('is_used'), 'is_used')
        if is_used is not None:
            qs = qs.filter(is_used=is_used)

        is_expired = self._parse_bool_param(self.request.query_params.get('is_expired'), 'is_expired')
        if is_expired is not None:
            if is_expired:
                qs = qs.filter(expires_at__lte=timezone.now())
            else:
                qs = qs.filter(expires_at__gt=timezone.now())
        return qs

    def get_serializer_class(self):
        if self.action == 'create':
            return InvitationCreateSerializer
        return InvitationListSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['company'] = self._get_company()
        return context

    def perform_create(self, serializer):
        serializer.save()

    def create(self, request, *args, **kwargs):
        company = self.get_serializer_context()['company']
        if company.employee_count >= company.max_employees:
            return Response({'detail': 'Employee limit reached'}, status=status.HTTP_400_BAD_REQUEST)
        return super().create(request, *args, **kwargs)

    @extend_schema(
        tags=['Companies'],
        summary='Revoke invitation',
        request=None,
        responses={
            200: OpenApiResponse(description='Invitation revoked'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin or superadmin only'),
            404: OpenApiResponse(description='Invitation not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='revoke')
    def revoke(self, request, *args, **kwargs):
        invitation = self.get_object()
        invitation.is_used = True
        invitation.used_at = timezone.now()
        invitation.save(update_fields=['is_used', 'used_at'])
        return Response({'detail': 'Invitation revoked'})

    @extend_schema(
        tags=['Companies'],
        summary='Resend invitation email',
        request=None,
        responses={
            200: OpenApiResponse(description='Invitation email resent'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin or superadmin only'),
            404: OpenApiResponse(description='Invitation not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='resend')
    def resend(self, request, *args, **kwargs):
        old_invitation = self.get_object()
        if old_invitation.is_used:
            raise ValidationError({'detail': 'Used/revoked invitation cannot be resent.'})

        with transaction.atomic():
            old_invitation.is_used = True
            old_invitation.used_at = timezone.now()
            old_invitation.save(update_fields=['is_used', 'used_at'])

            new_invitation = Invitation.objects.create(
                company=old_invitation.company,
                email=old_invitation.email,
                invited_by=request.user,
                role=old_invitation.role,
            )

        send_invitation_email.delay(new_invitation.id)
        return Response({'detail': 'Invitation resent'})


@extend_schema(
    tags=['Companies'],
    summary='Company employees directory',
    parameters=[
        OpenApiParameter(
            name='search',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description='Search by first_name, last_name, or email.',
        ),
        OpenApiParameter(
            name='position',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description='Filter by position (icontains).',
        ),
        OpenApiParameter(
            name='role',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description='Filter by role.',
        ),
        OpenApiParameter(
            name='ordering',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description='Order by full_name or date_joined.',
        ),
    ],
    responses={
        200: CompanyDirectoryListSerializer(many=True),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Forbidden'),
        404: OpenApiResponse(description='Company not found'),
    },
)
class CompanyDirectoryView(GenericAPIView):
    """
    GET /api/v1/companies/<company_id>/directory/

    Returns company members for Team page cards.
    Access: company members of the same company and superadmin.
    """

    permission_classes = [IsCompanyMember]
    serializer_class = CompanyDirectoryListSerializer

    def _get_company(self, request, company_id):
        company_qs = Company.objects.all()
        if request.user.role in ('company_admin', 'employee'):
            if request.user.company_id != company_id:
                raise PermissionDenied('You can only view directory of your own company.')
            company_qs = company_qs.filter(id=request.user.company_id)
        return get_object_or_404(company_qs, id=company_id)

    def get(self, request, company_id):
        company = self._get_company(request, company_id)

        qs = company.members.exclude(role='superadmin')
        directory_filter = CompanyDirectoryFilter(request.query_params, queryset=qs)
        qs = directory_filter.qs

        ordering_param = request.query_params.get('ordering', '')
        if ordering_param in ('date_joined', '-date_joined'):
            qs = qs.order_by(ordering_param)
        elif ordering_param in ('full_name', '-full_name'):
            prefix = '-' if ordering_param.startswith('-') else ''
            qs = qs.order_by(f'{prefix}first_name', f'{prefix}last_name')

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)


@extend_schema(
    tags=['Companies'],
    summary='Company directory profile',
    responses={
        200: CompanyDirectoryDetailSerializer,
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Forbidden'),
        404: OpenApiResponse(description='Company or user not found'),
    },
)
class CompanyDirectoryProfileView(APIView):
    """
    GET /api/v1/companies/<company_id>/directory/<user_id>/

    Returns contacts + tasks count + bookings for last 30 days + last_login.
    Access: company members of the same company and superadmin.
    """

    permission_classes = [IsCompanyMember]

    def _get_company(self, request, company_id):
        company_qs = Company.objects.all()
        if request.user.role in ('company_admin', 'employee'):
            if request.user.company_id != company_id:
                raise PermissionDenied('You can only view directory of your own company.')
            company_qs = company_qs.filter(id=request.user.company_id)
        return get_object_or_404(company_qs, id=company_id)

    def get(self, request, company_id, user_id):
        company = self._get_company(request, company_id)
        member = get_object_or_404(User, id=user_id, company=company)
        now = timezone.now()
        thirty_days_ahead = now + timedelta(days=30)

        tasks_count = Task.objects.filter(
            assignee=member,
            column__board__company=company,
        ).count()
        bookings_last_30_days = Booking.objects.filter(
            user=member,
            company=company,
            start_time__gte=now,
            start_time__lte=thirty_days_ahead,
        ).count()

        member.tasks_count = tasks_count
        member.bookings_last_30_days = bookings_last_30_days
        serializer = CompanyDirectoryDetailSerializer(member)
        return Response(serializer.data)


@extend_schema(
    tags=['Companies'],
    summary='Get member activity summary',
    responses={
        200: CompanyMemberActivitySerializer,
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Forbidden'),
        404: OpenApiResponse(description='Company or user not found'),
    },
)
class CompanyMemberActivityView(APIView):
    """
    GET /api/v1/companies/<company_id>/members/<user_id>/activity/

    Returns last_login, active task count, completed task count, and
    bookings in the last 30 days for the given member.

    Access: company_admin (own company only) and superadmin.
    """

    permission_classes = [IsCompanyAdmin]

    def get(self, request, company_id, user_id):
        # Resolve company with company isolation
        company_qs = Company.objects.all()
        if request.user.role == 'company_admin':
            if request.user.company_id != company_id:
                raise PermissionDenied('You can only view members of your own company.')
            company_qs = company_qs.filter(id=request.user.company_id)

        company = get_object_or_404(company_qs, id=company_id)
        member = get_object_or_404(User, id=user_id, company=company)

        thirty_days_ago = timezone.now() - timedelta(days=30)

        # Tasks: active = not archived; completed = archived.
        # Tasks are scoped to the company via board.company.
        assigned_tasks = Task.objects.filter(
            assignee=member,
            column__board__company=company,
        )
        active_tasks_count = assigned_tasks.filter(is_archived=False).count()
        completed_tasks_count = assigned_tasks.filter(is_archived=True).count()

        bookings_count = Booking.objects.filter(
            user=member,
            company=company,
            start_time__gte=thirty_days_ago,
        ).count()

        data = {
            'last_login': member.last_login,
            'active_tasks_count': active_tasks_count,
            'completed_tasks_count': completed_tasks_count,
            'bookings_last_30_days': bookings_count,
        }
        serializer = CompanyMemberActivitySerializer(data)
        return Response(serializer.data)


@extend_schema(
    tags=['Companies'],
    summary='Aggregated company calendar events',
    responses={200: OpenApiResponse(description='List of company calendar events')},
)
class CompanyCalendarView(APIView):
    permission_classes = [IsCompanyMember]

    def get(self, request, company_id):
        company = _resolve_calendar_company(request, company_id)
        date_from = _parse_date_query_param(request.query_params.get('date_from'), 'date_from')
        date_to = _parse_date_query_param(request.query_params.get('date_to'), 'date_to')
        if date_from > date_to:
            raise ValidationError({'detail': 'date_from must be less than or equal to date_to.'})

        user_id = request.query_params.get('user_id')
        if user_id not in (None, ''):
            try:
                user_id = int(user_id)
            except (TypeError, ValueError) as exc:
                raise ValidationError({'user_id': 'Must be an integer.'}) from exc

        event_type = request.query_params.get('event_type')
        if event_type and event_type not in CALENDAR_EVENT_TYPES:
            raise ValidationError({'event_type': f'Unsupported value. Use one of: {sorted(CALENDAR_EVENT_TYPES)}'})

        my_only = _parse_bool_query_param(request.query_params.get('my'), 'my')
        if my_only:
            user_id = request.user.id

        events = _build_company_calendar_events(
            company=company,
            date_from=date_from,
            date_to=date_to,
            user_id=user_id,
            event_type=event_type,
        )
        return Response(events)


@extend_schema(
    tags=['Companies'],
    summary='Busy slots for user by day',
    responses={200: OpenApiResponse(description='Busy slots for selected user and day')},
)
class CompanyCalendarBusyView(APIView):
    permission_classes = [IsCompanyMember]

    def get(self, request, company_id):
        company = _resolve_calendar_company(request, company_id)
        user_id_raw = request.query_params.get('user_id')
        if user_id_raw in (None, ''):
            raise ValidationError({'user_id': 'This query parameter is required.'})
        try:
            user_id = int(user_id_raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError({'user_id': 'Must be an integer.'}) from exc

        if not User.objects.filter(id=user_id, company_id=company.id).exists():
            raise ValidationError({'user_id': 'User not found in this company.'})

        target_date = _parse_date_query_param(request.query_params.get('date'), 'date')
        events = _build_company_calendar_events(
            company=company,
            date_from=target_date,
            date_to=target_date,
            user_id=user_id,
        )
        slots = [{'start': item['start'], 'end': item['end'], 'type': item['type']} for item in events]
        return Response(slots)
