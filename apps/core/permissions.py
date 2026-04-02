from rest_framework.permissions import BasePermission


class IsSuperAdmin(BasePermission):
    """Только суперадмин БЦ."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'superadmin'
        )


class IsCompanyAdmin(BasePermission):
    """Админ компании или суперадмин."""
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return request.user.role in ('superadmin', 'company_admin')


class IsCompanyMember(BasePermission):
    """Пользователь, привязанный к компании (admin/employee), или суперадмин."""
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.role == 'superadmin':
            return True
        return request.user.role in ('company_admin', 'employee') and request.user.company_id is not None


class IsOwnerOrAdmin(BasePermission):
    """Владелец объекта, админ компании или суперадмин."""
    owner_field = 'user'

    def has_object_permission(self, request, view, obj):
        if request.user.role == 'superadmin':
            return True
        owner = getattr(obj, self.owner_field, None)
        if owner == request.user:
            return True
        if request.user.role == 'company_admin' and hasattr(obj, 'company'):
            return obj.company_id == request.user.company_id
        return False
