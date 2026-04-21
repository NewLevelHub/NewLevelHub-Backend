from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse

from apps.core.permissions import IsCompanyAdmin, IsCompanyMember
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from .models import LeaveRequest, LeaveBalance, OnboardingTemplate, UserOnboardingProgress
from .serializers import (
    LeaveRequestSerializer, LeaveRequestReviewSerializer, LeaveBalanceSerializer,
    OnboardingTemplateSerializer,
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
            return qs.filter(user=self.request.user)
        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, company=self.request.user.company)

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
    @action(detail=True, methods=['post'], url_path='review', permission_classes=[IsCompanyAdmin])
    def review(self, request, pk=None):
        leave = self.get_object()
        ser = LeaveRequestReviewSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        leave.status = ser.validated_data['status']
        leave.review_comment = ser.validated_data.get('review_comment', '')
        leave.reviewed_by = request.user
        leave.reviewed_at = timezone.now()
        leave.save()
        # TODO: если approved — обновить LeaveBalance, добавить событие в календарь
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
        bal, _ = LeaveBalance.objects.get_or_create(user=request.user)
        return Response(LeaveBalanceSerializer(bal).data)


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
            return queryset
        return queryset.filter(company=self.request.user.company)

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


def _build_progress_response(user):
    progress_items = list(
        UserOnboardingProgress.objects.filter(user=user)
        .select_related('step')
        .order_by('step__position')
    )
    if not progress_items:
        return {'completed': True, 'steps': []}

    completed = all(item.is_completed for item in progress_items)
    steps = [
        {
            'id': item.step_id,
            'title': item.step.title,
            'is_completed': item.is_completed,
        }
        for item in progress_items
    ]
    return {'completed': completed, 'steps': steps}


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

    team_members = (
        request.user.company.members
        .annotate(
            completed_steps=Count(
                'onboarding_progress',
                filter=Q(onboarding_progress__is_completed=True),
            ),
            total_steps=Count('onboarding_progress'),
        )
        .order_by('id')
    )
    payload = [
        {
            'user': member.id,
            'completed_steps': member.completed_steps,
            'total_steps': member.total_steps,
        }
        for member in team_members
    ]
    return Response(payload)
