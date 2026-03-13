from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    """
    Permission: Only admin or supermentor can access.
    """
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['admin', 'supermentor']


class IsTenant(BasePermission):
    """
    Permission: Tenant, admin, or supermentor can access.
    """
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['tenant', 'admin', 'supermentor']


class IsEmployee(BasePermission):
    """
    Permission: Employee, tenant, admin, or supermentor can access.
    """
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['employee', 'tenant', 'admin', 'supermentor']


class IsOwnerOrAdmin(BasePermission):
    """
    Permission: Object owner or admin can access.
    """
    def has_object_permission(self, request, view, obj):
        return request.user.is_authenticated and (
            obj.user == request.user or request.user.role in ['admin', 'supermentor']
        )
