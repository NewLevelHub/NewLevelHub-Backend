from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse

from apps.core.permissions import IsSuperAdmin, IsCompanyAdmin
from apps.users.models import User
from .models import Company, CompanySettings, Invitation
from .serializers import (
    CompanySerializer,
    CompanySettingsSerializer,
    InvitationCreateSerializer,
    InvitationListSerializer,
    CompanyMemberSerializer,
)


@extend_schema_view(
    list=extend_schema(
        tags=['Companies'],
        summary='List companies',
        responses={
            200: CompanySerializer(many=True),
            401: OpenApiResponse(description='Not authenticated'),
        },
    ),
    retrieve=extend_schema(
        tags=['Companies'],
        summary='Get company details',
        responses={
            200: CompanySerializer,
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Company not found'),
        },
    ),
    create=extend_schema(
        tags=['Companies'],
        summary='Create company (superadmin)',
        request=CompanySerializer,
        responses={
            201: CompanySerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Superadmin only'),
        },
    ),
    update=extend_schema(
        tags=['Companies'],
        summary='Update company',
        request=CompanySerializer,
        responses={
            200: CompanySerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin or superadmin only'),
        },
    ),
    partial_update=extend_schema(
        tags=['Companies'],
        summary='Partial update company',
        request=CompanySerializer,
        responses={
            200: CompanySerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Company admin or superadmin only'),
        },
    ),
    destroy=extend_schema(
        tags=['Companies'],
        summary='Delete company (superadmin)',
        responses={
            204: OpenApiResponse(description='Deleted'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='Company not found'),
        },
    ),
)
class CompanyViewSet(viewsets.ModelViewSet):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer

    def get_permissions(self):
        if self.action in ('create', 'destroy'):
            return [IsSuperAdmin()]
        return [IsCompanyAdmin()]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return Company.objects.all()
        if user.company_id:
            return Company.objects.filter(id=user.company_id)
        return Company.objects.none()

    @extend_schema(
        tags=['Companies'],
        summary='Get or update company settings',
        request=CompanySettingsSerializer,
        responses={
            200: CompanySettingsSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Company not found'),
        },
    )
    @action(detail=True, methods=['get', 'patch'], url_path='settings')
    def company_settings(self, request, pk=None):
        company = self.get_object()
        settings_obj, _ = CompanySettings.objects.get_or_create(company=company)
        if request.method == 'PATCH':
            serializer = CompanySettingsSerializer(settings_obj, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
        return Response(CompanySettingsSerializer(settings_obj).data)

    @extend_schema(
        tags=['Companies'],
        summary='List company members',
        responses={
            200: CompanyMemberSerializer(many=True),
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Company not found'),
        },
    )
    @action(detail=True, methods=['get'], url_path='members')
    def members(self, request, pk=None):
        company = self.get_object()
        members = User.objects.filter(company=company, is_active=True)
        serializer = CompanyMemberSerializer(members, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=['Companies'],
        summary='Deactivate company (superadmin)',
        request=None,
        responses={
            200: OpenApiResponse(description='Company deactivated'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='Company not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='deactivate')
    def deactivate(self, request, pk=None):
        company = self.get_object()
        company.is_active = False
        company.save(update_fields=['is_active'])
        # TODO: деактивировать всех сотрудников компании
        return Response({'detail': 'Company deactivated'})


@extend_schema_view(
    list=extend_schema(
        tags=['Companies'],
        summary='List invitations',
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

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return Invitation.objects.all()
        if user.company_id:
            return Invitation.objects.filter(company=user.company)
        return Invitation.objects.none()

    def get_serializer_class(self):
        if self.action == 'create':
            return InvitationCreateSerializer
        return InvitationListSerializer

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
    def revoke(self, request, pk=None):
        invitation = self.get_object()
        invitation.is_used = True
        invitation.save(update_fields=['is_used'])
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
    def resend(self, request, pk=None):
        self.get_object()
        # TODO: отправить повторный email
        return Response({'detail': 'Invitation resent'})
