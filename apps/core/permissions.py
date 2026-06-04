"""
Reusable DRF permission classes for the New Level Hub platform.

Role hierarchy (highest to lowest):
  superadmin  — full access to everything, cross-company
  company_admin — manages their own company
  employee     — member of a company, limited write access
  guest        — personal bookings/storage/passes/services; blocked from CRM / HR / internal

Authentication contract:
  - Any permission that subclasses _AuthenticatedPermission will return 401
    for unauthenticated requests (DRF converts this to HTTP 401 when the
    WWW-Authenticate header is set, which SimpleJWT does automatically).
  - Authenticated-but-unauthorised requests get 403.
"""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.core.i18n import get_lang, translate


class _AuthenticatedPermission(BasePermission):
    """
    Internal base that returns 401 for unauthenticated requests and 403
    for authenticated-but-unauthorised requests.

    All public-facing permission classes in this module inherit from this
    base so the caller never has to stack IsAuthenticated manually.
    """

    def has_permission(self, request, view):
        # Unauthenticated  → 401
        if not request.user or not request.user.is_authenticated:
            return False
        return self._has_role_permission(request, view)

    def _has_role_permission(self, request, view):
        """Override in subclasses to check role-specific logic."""
        raise NotImplementedError


class IsSuperAdmin(_AuthenticatedPermission):
    """
    Allows access only to users with role='superadmin'.

    Returns 401 for unauthenticated requests and 403 for any other role.
    """

    def _has_role_permission(self, request, view):
        return request.user.role == 'superadmin'


class IsCompanyAdmin(_AuthenticatedPermission):
    """
    Allows access to company_admin and superadmin.

    Superadmin is included so that platform-level management never requires
    a secondary company_admin account.  Returns 401 / 403 for all others.
    """

    def _has_role_permission(self, request, view):
        return request.user.role in ('superadmin', 'company_admin')


class IsCompanyMember(_AuthenticatedPermission):
    """
    Allows access to company_admin and employee users who are associated
    with a company, plus superadmin (who operates across all companies).

    Guests are denied with a generic 403. company_admin / employee without
    a company receive 403 with ``detail.code`` = ``company_not_assigned`` so
    the client can show an onboarding / assignment screen instead of a blank error.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        user = request.user
        if user.role == 'superadmin':
            return True
        if user.role in ('company_admin', 'employee'):
            if user.company_id is None:
                lang = get_lang(request)
                raise PermissionDenied(
                    detail={
                        'code': 'company_not_assigned',
                        'message': translate('company.not_assigned', lang),
                    }
                )
            return True
        return False


class IsGuestOrCompanyMember(_AuthenticatedPermission):
    """
    Allows access to:
      - superadmin (cross-company),
      - company_admin / employee with a non-null company_id,
      - guest (no company required).

    Use this instead of IsCompanyMember on endpoints that guests should
    reach with their own data isolated by user (not by company).
    company_admin / employee without a company get 403 with
    ``detail.code`` = ``company_not_assigned``.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        user = request.user
        if user.role == 'superadmin':
            return True
        if user.role in ('company_admin', 'employee'):
            if user.company_id is None:
                lang = get_lang(request)
                raise PermissionDenied(
                    detail={
                        'code': 'company_not_assigned',
                        'message': translate('company.not_assigned', lang),
                    }
                )
            return True
        if user.role == 'guest':
            return True
        return False


class IsCompanyAdminOrReadOnly(_AuthenticatedPermission):
    """
    company_admin (and superadmin) may use any HTTP method.
    Authenticated employees and guests are limited to safe (read-only) methods.
    Unauthenticated requests receive 401.

    Typical usage: list/retrieve open to all company members, but
    create/update/delete restricted to admins.
    """

    def _has_role_permission(self, request, view):
        if request.method in SAFE_METHODS:
            # Any authenticated user may read; callers can further restrict
            # with IsCompanyMember if guests should also be blocked.
            return True
        return request.user.role in ('superadmin', 'company_admin')


class IsEmailVerifiedOrSuperAdmin(_AuthenticatedPermission):
    """
    Blocks unverified users from protected business actions.
    Superadmin is always allowed.
    """

    def _has_role_permission(self, request, view):
        user = request.user
        if user.role == 'superadmin':
            return True
        if not user.is_email_verified:
            raise PermissionDenied(translate('auth.email_not_verified_short', get_lang(request)))
        return True


class IsOwnerOrAdmin(_AuthenticatedPermission):
    """
    Object-level permission.

    Grants access when the requesting user:
      - is a superadmin (cross-company access), or
      - owns the object (obj.<owner_field> == request.user), or
      - is a company_admin whose company matches the object's company.

    ``owner_field`` can be overridden on the view class::

        class MyView(RetrieveUpdateAPIView):
            permission_classes = [IsOwnerOrAdmin]
            # default is 'user'; override if the FK is named differently:
            # owner_field = 'created_by'
    """

    owner_field = 'user'

    def _has_role_permission(self, request, view):
        # has_permission is satisfied for any authenticated user;
        # the real gate is has_object_permission below.
        return True

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.role == 'superadmin':
            return True
        owner = getattr(obj, self.owner_field, None)
        if owner == user:
            return True
        if user.role == 'company_admin' and hasattr(obj, 'company'):
            return obj.company_id == user.company_id
        return False


class IsServiceManager(_AuthenticatedPermission):
    """
    Allows access only to users with role='service_manager'.

    The service_manager is a building-wide role responsible for handling all
    service requests across companies. It is not bound to a single company.
    """

    def _has_role_permission(self, request, view):
        return request.user.role == 'service_manager'


class IsServiceRequestManager(_AuthenticatedPermission):
    """
    Allows access only to roles that may manage service requests
    (advance status, assign executors):
      - superadmin (platform-level)
      - service_manager (building-wide responsible person)

    company_admin is intentionally excluded — they can create requests on
    behalf of their company but cannot touch the service workflow.
    """

    def _has_role_permission(self, request, view):
        return request.user.role in ('superadmin', 'service_manager')


class IsSuperAdminOrReception(_AuthenticatedPermission):
    """
    Allows access only to superadmin and users with role='reception'.

    Used for endpoints operated at a physical reception desk (e.g. QR validation).
    Returns 401 for unauthenticated requests and 403 for all other roles.
    """

    def _has_role_permission(self, request, view):
        return request.user.role in ('superadmin', 'reception')


class IsOwnerOrSuperAdmin(_AuthenticatedPermission):
    """
    Object-level permission for actions that require physical presence or
    personal ownership — only the object owner or a superadmin may proceed.
    company_admin is intentionally excluded (unlike IsOwnerOrAdmin).

    Grants access when the requesting user:
      - is a superadmin (emergency / platform-level override), or
      - owns the object (obj.<owner_field> == request.user).

    ``owner_field`` can be overridden on the view class::

        class MyView(RetrieveUpdateAPIView):
            permission_classes = [IsOwnerOrSuperAdmin]
            # default is 'user'; override if the FK is named differently:
            # owner_field = 'created_by'
    """

    owner_field = 'user'

    def _has_role_permission(self, request, view):
        # has_permission is satisfied for any authenticated user;
        # the real gate is has_object_permission below.
        return True

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.role == 'superadmin':
            return True
        owner = getattr(obj, self.owner_field, None)
        return owner == user
