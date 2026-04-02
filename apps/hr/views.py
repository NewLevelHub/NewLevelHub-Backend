from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.core.permissions import IsCompanyAdmin, IsCompanyMember
from apps.core.mixins import CompanyQuerySetMixin
from .models import LeaveRequest, LeaveBalance, OnboardingTemplate, OnboardingStep, UserOnboardingProgress
from .serializers import (
    LeaveRequestSerializer, LeaveRequestReviewSerializer, LeaveBalanceSerializer,
    OnboardingTemplateSerializer, OnboardingStepSerializer, UserOnboardingProgressSerializer,
)


@extend_schema_view(
    list=extend_schema(tags=['HR'], summary='List leave requests'),
    create=extend_schema(tags=['HR'], summary='Submit leave request'),
    retrieve=extend_schema(tags=['HR'], summary='Get leave request details'),
)
class LeaveRequestViewSet(CompanyQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = LeaveRequestSerializer
    permission_classes = [IsCompanyMember]
    http_method_names = ['get', 'post']
    filterset_fields = ['status', 'leave_type', 'user']

    def get_queryset(self):
        return LeaveRequest.objects.select_related('user', 'reviewed_by')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, company=self.request.user.company)

    @extend_schema(tags=['HR'], summary='Approve / reject leave request')
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

    @extend_schema(tags=['HR'], summary='My leave balance')
    @action(detail=False, methods=['get'], url_path='balance')
    def balance(self, request):
        bal, _ = LeaveBalance.objects.get_or_create(user=request.user)
        return Response(LeaveBalanceSerializer(bal).data)


@extend_schema_view(
    list=extend_schema(tags=['HR'], summary='List onboarding templates'),
    create=extend_schema(tags=['HR'], summary='Create onboarding template'),
    partial_update=extend_schema(tags=['HR'], summary='Update onboarding template'),
)
class OnboardingTemplateViewSet(CompanyQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = OnboardingTemplateSerializer
    permission_classes = [IsCompanyAdmin]

    def get_queryset(self):
        return OnboardingTemplate.objects.prefetch_related('steps')

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


@extend_schema_view(
    list=extend_schema(tags=['HR'], summary='My onboarding progress'),
)
class UserOnboardingProgressViewSet(viewsets.ModelViewSet):
    serializer_class = UserOnboardingProgressSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch']

    def get_queryset(self):
        return UserOnboardingProgress.objects.filter(user=self.request.user)

    @extend_schema(tags=['HR'], summary='Mark onboarding step as completed')
    @action(detail=True, methods=['post'], url_path='complete')
    def complete_step(self, request, pk=None):
        progress = self.get_object()
        progress.is_completed = True
        progress.completed_at = timezone.now()
        progress.save()
        return Response(UserOnboardingProgressSerializer(progress).data)
