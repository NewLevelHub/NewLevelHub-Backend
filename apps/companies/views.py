from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse

from apps.core.permissions import IsSuperAdmin, IsCompanyAdmin, IsCompanyMember
from apps.users.models import User
from .filters import CompanyFilter
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
)


@extend_schema_view(
    list=extend_schema(
        tags=['Companies'],
        summary='List companies',
        responses={
            200: CompanySerializer(many=True),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Forbidden'),
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
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = CompanyFilter
    search_fields = ['name']
    ordering_fields = ['name', 'created_at', 'plan']

    def get_permissions(self):
        if self.action in ('create', 'destroy'):
            return [IsSuperAdmin()]
        if self.action in ('update', 'partial_update'):
            # Both superadmin and company_admin may update; field-level
            # restriction is enforced via get_serializer_class below.
            return [IsCompanyAdmin()]
        # list / retrieve / custom actions — company members only; guests get 403
        return [IsCompanyMember()]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return Company.objects.all()
        if user.company_id:
            return Company.objects.filter(id=user.company_id)
        return Company.objects.none()

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
            permission_classes=[IsCompanyAdmin])
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
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Company not found'),
        },
    )
    @action(detail=True, methods=['get'], url_path='members',
            permission_classes=[IsCompanyMember])
    def members(self, request, pk=None):
        company = self.get_object()
        qs = User.objects.filter(company=company, is_active=True)
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
        storage_used_bytes = company.files.aggregate(total=Sum('file_size'))['total'] or 0
        used_gb = (Decimal(storage_used_bytes) / Decimal(1024 ** 3)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        return Response({
            'employees': {
                'current': company.members.filter(is_active=True).count(),
                'max': company.max_employees,
            },
            'boards': {
                'current': company.boards.count(),
                'max': company.max_boards,
            },
            'storage': {
                'used_gb': float(used_gb),
                'limit_gb': company.storage_limit_gb,
            },
        })

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
    @action(detail=True, methods=['post'], url_path='deactivate',
            permission_classes=[IsSuperAdmin])
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
