"""
Reusable DRF permission classes for the New Level Hub platform.

Role hierarchy (highest to lowest):
  superadmin  — full access to everything, cross-company
  company_admin — manages their own company
  employee     — member of a company, limited write access
  guest        — read-only public areas; blocked from CRM / HR / internal

Authentication contract:
  - Any permission that subclasses _AuthenticatedPermission will return 401
    for unauthenticated requests (DRF converts this to HTTP 401 when the
    WWW-Authenticate header is set, which SimpleJWT does automatically).
  - Authenticated-but-unauthorised requests get 403.
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS


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

    Guests and company-less users are denied with 403.
    """

    def _has_role_permission(self, request, view):
        user = request.user
        if user.role == 'superadmin':
            return True
        return user.role in ('company_admin', 'employee') and user.company_id is not None


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

    message = 'Email not verified'

    def _has_role_permission(self, request, view):
        user = request.user
        if user.role == 'superadmin':
            return True
        return bool(user.is_email_verified)


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
