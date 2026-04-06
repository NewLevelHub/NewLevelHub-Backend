"""
ViewSet permission integration tests for DEV-50.

Verifies that each ViewSet carries the correct permission_classes and that
CompanyIsolationMixin scopes querysets properly — all without touching the
database (MagicMock + APIRequestFactory pattern from test_permissions.py).
"""

from unittest.mock import MagicMock

from rest_framework.test import APIRequestFactory

from apps.core.permissions import (
    IsSuperAdmin,
    IsCompanyAdmin,
    IsCompanyMember,
    IsCompanyAdminOrReadOnly,
    IsOwnerOrAdmin,
)
from apps.core.mixins import CompanyIsolationMixin


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_permissions.py)
# ---------------------------------------------------------------------------

factory = APIRequestFactory()


def _make_user(role, company_id=None, authenticated=True):
    user = MagicMock()
    user.role = role
    user.company_id = company_id
    user.is_authenticated = authenticated
    user.company = MagicMock()
    user.company.pk = company_id
    return user


def _make_request(method='GET', user=None):
    raw = getattr(factory, method.lower())('/')
    raw.user = user or MagicMock(is_authenticated=False)
    return raw


def _view():
    return MagicMock()


# ---------------------------------------------------------------------------
# Minimal fake queryset for mixin tests
# ---------------------------------------------------------------------------

class _FakeQS:
    def __init__(self, items=None):
        self._items = list(items or [])
        self._filter_kwargs = None
        self._is_none = False

    def filter(self, **kwargs):
        result = _FakeQS()
        result._filter_kwargs = kwargs
        return result

    @classmethod
    def none(cls):
        qs = cls()
        qs._is_none = True
        return qs

    def __len__(self):
        return 0 if self._is_none else len(self._items)

    def __iter__(self):
        return iter([] if self._is_none else self._items)


class _BaseView:
    def __init__(self, base_qs):
        self._base_qs = base_qs

    def get_queryset(self):
        return self._base_qs


class _IsolatedView(CompanyIsolationMixin, _BaseView):
    def __init__(self, user, base_qs):
        _BaseView.__init__(self, base_qs)
        self.request = MagicMock()
        self.request.user = user


# ---------------------------------------------------------------------------
# TestIsCompanyMemberPermission
# ---------------------------------------------------------------------------

class TestIsCompanyMemberPermission:
    perm = IsCompanyMember()

    def test_anonymous_denied(self):
        user = _make_user('employee', company_id=1, authenticated=False)
        assert self.perm.has_permission(_make_request(user=user), _view()) is False

    def test_none_user_denied(self):
        request = _make_request()
        request.user = None
        assert self.perm.has_permission(request, _view()) is False

    def test_superadmin_allowed(self):
        assert self.perm.has_permission(
            _make_request(user=_make_user('superadmin')), _view()
        ) is True

    def test_company_admin_with_company_allowed(self):
        assert self.perm.has_permission(
            _make_request(user=_make_user('company_admin', company_id=1)), _view()
        ) is True

    def test_employee_with_company_allowed(self):
        assert self.perm.has_permission(
            _make_request(user=_make_user('employee', company_id=1)), _view()
        ) is True

    def test_employee_without_company_denied(self):
        assert self.perm.has_permission(
            _make_request(user=_make_user('employee', company_id=None)), _view()
        ) is False

    def test_guest_denied(self):
        assert self.perm.has_permission(
            _make_request(user=_make_user('guest', company_id=1)), _view()
        ) is False

    def test_guest_without_company_denied(self):
        assert self.perm.has_permission(
            _make_request(user=_make_user('guest')), _view()
        ) is False


# ---------------------------------------------------------------------------
# TestIsCompanyAdminPermission
# ---------------------------------------------------------------------------

class TestIsCompanyAdminPermission:
    perm = IsCompanyAdmin()

    def test_anonymous_denied(self):
        user = _make_user('company_admin', authenticated=False)
        assert self.perm.has_permission(_make_request(user=user), _view()) is False

    def test_superadmin_allowed(self):
        assert self.perm.has_permission(
            _make_request(user=_make_user('superadmin')), _view()
        ) is True

    def test_company_admin_allowed(self):
        assert self.perm.has_permission(
            _make_request(user=_make_user('company_admin', company_id=1)), _view()
        ) is True

    def test_employee_denied(self):
        assert self.perm.has_permission(
            _make_request(user=_make_user('employee', company_id=1)), _view()
        ) is False

    def test_guest_denied(self):
        assert self.perm.has_permission(
            _make_request(user=_make_user('guest')), _view()
        ) is False


# ---------------------------------------------------------------------------
# TestIsCompanyAdminOrReadOnlyPermission
# ---------------------------------------------------------------------------

class TestIsCompanyAdminOrReadOnlyPermission:
    perm = IsCompanyAdminOrReadOnly()

    def test_anonymous_get_denied(self):
        user = _make_user('company_admin', authenticated=False)
        assert self.perm.has_permission(_make_request('GET', user=user), _view()) is False

    def test_employee_get_allowed(self):
        assert self.perm.has_permission(
            _make_request('GET', user=_make_user('employee', company_id=1)), _view()
        ) is True

    def test_guest_get_allowed(self):
        assert self.perm.has_permission(
            _make_request('GET', user=_make_user('guest')), _view()
        ) is True

    def test_company_admin_post_allowed(self):
        assert self.perm.has_permission(
            _make_request('POST', user=_make_user('company_admin', company_id=1)), _view()
        ) is True

    def test_superadmin_post_allowed(self):
        assert self.perm.has_permission(
            _make_request('POST', user=_make_user('superadmin')), _view()
        ) is True

    def test_employee_post_denied(self):
        assert self.perm.has_permission(
            _make_request('POST', user=_make_user('employee', company_id=1)), _view()
        ) is False

    def test_guest_post_denied(self):
        assert self.perm.has_permission(
            _make_request('POST', user=_make_user('guest')), _view()
        ) is False

    def test_employee_delete_denied(self):
        assert self.perm.has_permission(
            _make_request('DELETE', user=_make_user('employee', company_id=1)), _view()
        ) is False

    def test_company_admin_delete_allowed(self):
        assert self.perm.has_permission(
            _make_request('DELETE', user=_make_user('company_admin', company_id=1)), _view()
        ) is True


# ---------------------------------------------------------------------------
# TestIsOwnerOrAdminPermission
# ---------------------------------------------------------------------------

class TestIsOwnerOrAdminPermission:
    perm = IsOwnerOrAdmin()

    def _obj(self, owner, company_id=None):
        obj = MagicMock()
        obj.user = owner
        obj.company_id = company_id
        return obj

    # has_permission

    def test_anonymous_has_permission_denied(self):
        user = _make_user('employee', authenticated=False)
        assert self.perm.has_permission(_make_request(user=user), _view()) is False

    def test_any_authenticated_has_permission_true(self):
        for role in ('superadmin', 'company_admin', 'employee', 'guest'):
            assert self.perm.has_permission(
                _make_request(user=_make_user(role, company_id=1)), _view()
            ) is True

    # has_object_permission

    def test_superadmin_allowed_any_object(self):
        superadmin = _make_user('superadmin')
        obj = self._obj(owner=_make_user('employee', company_id=99))
        assert self.perm.has_object_permission(_make_request(user=superadmin), _view(), obj) is True

    def test_owner_allowed(self):
        user = _make_user('employee', company_id=1)
        obj = self._obj(owner=user, company_id=1)
        assert self.perm.has_object_permission(_make_request(user=user), _view(), obj) is True

    def test_non_owner_employee_denied(self):
        user = _make_user('employee', company_id=1)
        other = _make_user('employee', company_id=1)
        obj = self._obj(owner=other, company_id=1)
        assert self.perm.has_object_permission(_make_request(user=user), _view(), obj) is False

    def test_company_admin_same_company_allowed(self):
        admin = _make_user('company_admin', company_id=5)
        other = _make_user('employee', company_id=5)
        obj = self._obj(owner=other, company_id=5)
        assert self.perm.has_object_permission(_make_request(user=admin), _view(), obj) is True

    def test_company_admin_different_company_denied(self):
        admin = _make_user('company_admin', company_id=5)
        other = _make_user('employee', company_id=99)
        obj = self._obj(owner=other, company_id=99)
        assert self.perm.has_object_permission(_make_request(user=admin), _view(), obj) is False

    def test_guest_not_owner_denied(self):
        guest = _make_user('guest')
        other = _make_user('employee', company_id=1)
        obj = self._obj(owner=other, company_id=1)
        assert self.perm.has_object_permission(_make_request(user=guest), _view(), obj) is False


# ---------------------------------------------------------------------------
# TestCompanyIsolationMixin
# ---------------------------------------------------------------------------

class TestCompanyIsolationMixin:

    def _qs(self, *items):
        return _FakeQS(list(items))

    def test_superadmin_sees_all(self):
        user = _make_user('superadmin', company_id=None)
        base_qs = self._qs('a', 'b')
        view = _IsolatedView(user, base_qs)
        result = view.get_queryset()
        assert result is base_qs

    def test_company_admin_filtered_by_company(self):
        user = _make_user('company_admin', company_id=7)
        base_qs = self._qs()
        view = _IsolatedView(user, base_qs)
        result = view.get_queryset()
        assert result._filter_kwargs == {'company': 7}

    def test_employee_filtered_by_company(self):
        user = _make_user('employee', company_id=3)
        base_qs = self._qs()
        view = _IsolatedView(user, base_qs)
        result = view.get_queryset()
        assert result._filter_kwargs == {'company': 3}

    def test_user_without_company_gets_none(self):
        user = _make_user('employee', company_id=None)
        view = _IsolatedView(user, self._qs('x'))
        result = view.get_queryset()
        assert result._is_none is True

    def test_guest_without_company_gets_none(self):
        user = _make_user('guest', company_id=None)
        view = _IsolatedView(user, self._qs('x'))
        result = view.get_queryset()
        assert result._is_none is True

    def test_guest_with_company_filtered(self):
        # Guest reaches here only if higher-level code allows it;
        # mixin still scopes by company_id regardless of role.
        user = _make_user('guest', company_id=4)
        base_qs = self._qs()
        view = _IsolatedView(user, base_qs)
        result = view.get_queryset()
        assert result._filter_kwargs == {'company': 4}

    def test_custom_company_field_respected(self):
        user = _make_user('employee', company_id=9)
        base_qs = self._qs()

        class _CustomView(CompanyIsolationMixin, _BaseView):
            company_field = 'organisation'

            def __init__(self, usr, qs):
                _BaseView.__init__(self, qs)
                self.request = MagicMock()
                self.request.user = usr

        view = _CustomView(user, base_qs)
        result = view.get_queryset()
        assert result._filter_kwargs == {'organisation': 9}


# ---------------------------------------------------------------------------
# ViewSet-level permission class assertions
# ---------------------------------------------------------------------------
# These tests import the real ViewSet classes and verify that the correct
# permission class types are present in permission_classes.  No database
# access is needed.

class TestViewSetPermissionClasses:
    """
    permission_classes on a ViewSet is a list of class references (not instances).
    We verify membership directly against the list.
    """

    def _perms(self, viewset_class):
        return viewset_class.permission_classes

    def test_booking_viewset_uses_is_company_member(self):
        from apps.bookings.views import BookingViewSet
        assert IsCompanyMember in self._perms(BookingViewSet)

    def test_resource_viewset_uses_is_company_member(self):
        from apps.bookings.views import ResourceViewSet
        assert IsCompanyMember in self._perms(ResourceViewSet)

    def test_board_viewset_uses_is_company_member(self):
        from apps.crm.views import BoardViewSet
        assert IsCompanyMember in self._perms(BoardViewSet)

    def test_label_viewset_uses_is_company_member(self):
        from apps.crm.views import LabelViewSet
        assert IsCompanyMember in self._perms(LabelViewSet)

    def test_folder_viewset_uses_is_company_member(self):
        from apps.storage.views import FolderViewSet
        assert IsCompanyMember in self._perms(FolderViewSet)

    def test_file_viewset_uses_is_company_member(self):
        from apps.storage.views import FileViewSet
        assert IsCompanyMember in self._perms(FileViewSet)

    def test_leave_request_viewset_uses_is_company_member(self):
        from apps.hr.views import LeaveRequestViewSet
        assert IsCompanyMember in self._perms(LeaveRequestViewSet)

    def test_guest_pass_viewset_uses_is_company_admin(self):
        from apps.access.views import GuestPassViewSet
        assert IsCompanyAdmin in self._perms(GuestPassViewSet)

    def test_service_request_viewset_uses_is_company_member(self):
        from apps.services.views import ServiceRequestViewSet
        assert IsCompanyMember in self._perms(ServiceRequestViewSet)

    def test_announcement_viewset_uses_is_company_admin_or_readonly(self):
        from apps.services.views import AnnouncementViewSet
        assert IsCompanyAdminOrReadOnly in self._perms(AnnouncementViewSet)

    def test_notification_viewset_uses_is_owner_or_admin(self):
        from apps.notifications.views import NotificationViewSet
        assert IsOwnerOrAdmin in self._perms(NotificationViewSet)


# ---------------------------------------------------------------------------
# ViewSet mixin presence checks
# ---------------------------------------------------------------------------

class TestViewSetMixinPresence:

    def _has_mixin(self, viewset_class, mixin_class):
        return issubclass(viewset_class, mixin_class)

    def test_booking_viewset_has_company_isolation_mixin(self):
        from apps.bookings.views import BookingViewSet
        assert self._has_mixin(BookingViewSet, CompanyIsolationMixin)

    def test_board_viewset_has_company_isolation_mixin(self):
        from apps.crm.views import BoardViewSet
        assert self._has_mixin(BoardViewSet, CompanyIsolationMixin)

    def test_label_viewset_has_company_isolation_mixin(self):
        from apps.crm.views import LabelViewSet
        assert self._has_mixin(LabelViewSet, CompanyIsolationMixin)

    def test_leave_request_viewset_has_company_isolation_mixin(self):
        from apps.hr.views import LeaveRequestViewSet
        assert self._has_mixin(LeaveRequestViewSet, CompanyIsolationMixin)

    def test_guest_pass_viewset_has_company_isolation_mixin(self):
        from apps.access.views import GuestPassViewSet
        assert self._has_mixin(GuestPassViewSet, CompanyIsolationMixin)


# ---------------------------------------------------------------------------
# Unauthenticated → False on all key permission classes
# ---------------------------------------------------------------------------

class TestAllPermissionsBlockUnauthenticated:
    PERMISSIONS = [
        IsSuperAdmin(),
        IsCompanyAdmin(),
        IsCompanyMember(),
        IsCompanyAdminOrReadOnly(),
        IsOwnerOrAdmin(),
    ]

    def _anon_request(self, method='GET'):
        anon = MagicMock()
        anon.is_authenticated = False
        anon.role = None
        return _make_request(method, user=anon)

    def test_all_deny_unauthenticated_get(self):
        request = self._anon_request('GET')
        for perm in self.PERMISSIONS:
            assert perm.has_permission(request, _view()) is False, (
                f'{perm.__class__.__name__} must deny unauthenticated GET'
            )

    def test_all_deny_unauthenticated_post(self):
        request = self._anon_request('POST')
        for perm in self.PERMISSIONS:
            assert perm.has_permission(request, _view()) is False, (
                f'{perm.__class__.__name__} must deny unauthenticated POST'
            )


# ---------------------------------------------------------------------------
# Guest role blocked from unsafe actions on company-member-gated viewsets
# ---------------------------------------------------------------------------

class TestGuestBlockedFromCompanyMemberEndpoints:

    def test_guest_denied_on_is_company_member(self):
        perm = IsCompanyMember()
        for method in ('GET', 'POST', 'PATCH', 'DELETE'):
            request = _make_request(method, user=_make_user('guest', company_id=1))
            assert perm.has_permission(request, _view()) is False, (
                f'Guest must be denied for {method} via IsCompanyMember'
            )

    def test_guest_denied_write_on_is_company_admin_or_readonly(self):
        perm = IsCompanyAdminOrReadOnly()
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            request = _make_request(method, user=_make_user('guest'))
            assert perm.has_permission(request, _view()) is False

    def test_guest_read_allowed_on_is_company_admin_or_readonly(self):
        perm = IsCompanyAdminOrReadOnly()
        request = _make_request('GET', user=_make_user('guest'))
        assert perm.has_permission(request, _view()) is True
