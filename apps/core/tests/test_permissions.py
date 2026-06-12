"""
Tests for apps.core.permissions and apps.core.mixins.

Coverage targets:
  - IsSuperAdmin
  - IsCompanyAdmin
  - IsCompanyMember
  - IsCompanyAdminOrReadOnly
  - IsOwnerOrAdmin (has_permission + has_object_permission)
  - CompanyIsolationMixin (get_queryset scoping)

Each permission is exercised for:
  - Unauthenticated request             → 401
  - Authenticated superadmin            → allowed
  - Authenticated company_admin         → allowed / denied per class
  - Authenticated employee              → allowed / denied per class
  - Authenticated guest                 → denied (403)
  - company_admin / employee without
    a company association               → PermissionDenied (company_not_assigned)
"""

from unittest.mock import MagicMock

import pytest
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory

from apps.core.permissions import (
    IsSuperAdmin,
    IsCompanyAdmin,
    IsCompanyMember,
    IsCompanyAdminOrReadOnly,
    IsOwnerOrAdmin,
    IsSuperAdminOrReception,
    IsCompanyPremium,
)
from apps.core.mixins import CompanyIsolationMixin, CompanyQuerySetMixin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

factory = APIRequestFactory()


def _make_user(role, company_id=None, authenticated=True, plan='basic'):
    """Return a lightweight mock user — no database required."""
    user = MagicMock()
    user.role = role
    user.company_id = company_id
    user.is_authenticated = authenticated
    if company_id is not None:
        user.company = MagicMock()
        user.company.pk = company_id
        user.company.plan = plan
    else:
        user.company = None
    return user


def _make_request(method='GET', user=None):
    """Wrap a request mock with the given user."""
    raw = getattr(factory, method.lower())('/')
    raw.user = user or MagicMock(is_authenticated=False)
    return raw


def _view():
    return MagicMock()


# ---------------------------------------------------------------------------
# IsSuperAdmin
# ---------------------------------------------------------------------------

class TestIsSuperAdmin:
    perm = IsSuperAdmin()

    def test_unauthenticated_returns_false(self):
        user = _make_user('superadmin', authenticated=False)
        request = _make_request(user=user)
        assert self.perm.has_permission(request, _view()) is False

    def test_superadmin_allowed(self):
        request = _make_request(user=_make_user('superadmin', company_id=None))
        assert self.perm.has_permission(request, _view()) is True

    def test_company_admin_denied(self):
        request = _make_request(user=_make_user('company_admin', company_id=1))
        assert self.perm.has_permission(request, _view()) is False

    def test_employee_denied(self):
        request = _make_request(user=_make_user('employee', company_id=1))
        assert self.perm.has_permission(request, _view()) is False

    def test_guest_denied(self):
        request = _make_request(user=_make_user('guest'))
        assert self.perm.has_permission(request, _view()) is False

    def test_anonymous_user_none_denied(self):
        request = _make_request()
        request.user = None
        assert self.perm.has_permission(request, _view()) is False


# ---------------------------------------------------------------------------
# IsCompanyAdmin
# ---------------------------------------------------------------------------

class TestIsCompanyAdmin:
    perm = IsCompanyAdmin()

    def test_unauthenticated_returns_false(self):
        user = _make_user('company_admin', authenticated=False)
        request = _make_request(user=user)
        assert self.perm.has_permission(request, _view()) is False

    def test_superadmin_allowed(self):
        """Superadmin must pass IsCompanyAdmin to avoid locking themselves out."""
        request = _make_request(user=_make_user('superadmin'))
        assert self.perm.has_permission(request, _view()) is True

    def test_company_admin_allowed(self):
        request = _make_request(user=_make_user('company_admin', company_id=1))
        assert self.perm.has_permission(request, _view()) is True

    def test_employee_denied(self):
        request = _make_request(user=_make_user('employee', company_id=1))
        assert self.perm.has_permission(request, _view()) is False

    def test_guest_denied(self):
        request = _make_request(user=_make_user('guest'))
        assert self.perm.has_permission(request, _view()) is False

    def test_anonymous_user_none_denied(self):
        request = _make_request()
        request.user = None
        assert self.perm.has_permission(request, _view()) is False


# ---------------------------------------------------------------------------
# IsCompanyMember
# ---------------------------------------------------------------------------

class TestIsCompanyMember:
    perm = IsCompanyMember()

    def test_unauthenticated_returns_false(self):
        user = _make_user('employee', company_id=1, authenticated=False)
        request = _make_request(user=user)
        assert self.perm.has_permission(request, _view()) is False

    def test_superadmin_allowed_without_company(self):
        """Superadmin has no company association but must still pass."""
        request = _make_request(user=_make_user('superadmin', company_id=None))
        assert self.perm.has_permission(request, _view()) is True

    def test_company_admin_with_company_allowed(self):
        request = _make_request(user=_make_user('company_admin', company_id=1))
        assert self.perm.has_permission(request, _view()) is True

    def test_employee_with_company_allowed(self):
        request = _make_request(user=_make_user('employee', company_id=1))
        assert self.perm.has_permission(request, _view()) is True

    def test_company_admin_without_company_raises_company_not_assigned(self):
        """A company_admin with no company gets an explicit API error code."""
        request = _make_request(user=_make_user('company_admin', company_id=None))
        with pytest.raises(PermissionDenied) as exc:
            self.perm.has_permission(request, _view())
        assert exc.value.detail['code'] == 'company_not_assigned'
        assert 'message' in exc.value.detail

    def test_employee_without_company_raises_company_not_assigned(self):
        request = _make_request(user=_make_user('employee', company_id=None))
        with pytest.raises(PermissionDenied) as exc:
            self.perm.has_permission(request, _view())
        assert exc.value.detail['code'] == 'company_not_assigned'

    def test_guest_denied(self):
        """Guest must NOT have access — core security requirement."""
        request = _make_request(user=_make_user('guest'))
        assert self.perm.has_permission(request, _view()) is False

    def test_guest_with_company_denied(self):
        """Even a guest linked to a company must be denied."""
        request = _make_request(user=_make_user('guest', company_id=1))
        assert self.perm.has_permission(request, _view()) is False

    def test_anonymous_user_none_denied(self):
        request = _make_request()
        request.user = None
        assert self.perm.has_permission(request, _view()) is False


# ---------------------------------------------------------------------------
# IsCompanyAdminOrReadOnly
# ---------------------------------------------------------------------------

class TestIsCompanyAdminOrReadOnly:
    perm = IsCompanyAdminOrReadOnly()

    # -- Unauthenticated (should always be False / 401) --

    def test_unauthenticated_get_returns_false(self):
        user = _make_user('company_admin', authenticated=False)
        request = _make_request('GET', user=user)
        assert self.perm.has_permission(request, _view()) is False

    def test_unauthenticated_post_returns_false(self):
        user = _make_user('employee', authenticated=False)
        request = _make_request('POST', user=user)
        assert self.perm.has_permission(request, _view()) is False

    # -- Safe methods (GET, HEAD, OPTIONS) --

    def test_employee_get_allowed(self):
        request = _make_request('GET', user=_make_user('employee', company_id=1))
        assert self.perm.has_permission(request, _view()) is True

    def test_guest_get_allowed(self):
        """Guest can read; higher-level views restrict further if needed."""
        request = _make_request('GET', user=_make_user('guest'))
        assert self.perm.has_permission(request, _view()) is True

    def test_company_admin_get_allowed(self):
        request = _make_request('GET', user=_make_user('company_admin', company_id=1))
        assert self.perm.has_permission(request, _view()) is True

    def test_options_allowed_for_employee(self):
        request = _make_request('OPTIONS', user=_make_user('employee', company_id=1))
        assert self.perm.has_permission(request, _view()) is True

    def test_head_allowed_for_employee(self):
        request = _make_request('HEAD', user=_make_user('employee', company_id=1))
        assert self.perm.has_permission(request, _view()) is True

    # -- Unsafe methods (POST, PUT, PATCH, DELETE) --

    def test_superadmin_post_allowed(self):
        request = _make_request('POST', user=_make_user('superadmin'))
        assert self.perm.has_permission(request, _view()) is True

    def test_company_admin_post_allowed(self):
        request = _make_request('POST', user=_make_user('company_admin', company_id=1))
        assert self.perm.has_permission(request, _view()) is True

    def test_company_admin_patch_allowed(self):
        request = _make_request('PATCH', user=_make_user('company_admin', company_id=1))
        assert self.perm.has_permission(request, _view()) is True

    def test_company_admin_delete_allowed(self):
        request = _make_request('DELETE', user=_make_user('company_admin', company_id=1))
        assert self.perm.has_permission(request, _view()) is True

    def test_employee_post_denied(self):
        request = _make_request('POST', user=_make_user('employee', company_id=1))
        assert self.perm.has_permission(request, _view()) is False

    def test_employee_put_denied(self):
        request = _make_request('PUT', user=_make_user('employee', company_id=1))
        assert self.perm.has_permission(request, _view()) is False

    def test_employee_delete_denied(self):
        request = _make_request('DELETE', user=_make_user('employee', company_id=1))
        assert self.perm.has_permission(request, _view()) is False

    def test_guest_post_denied(self):
        """Guest must NOT write — core security requirement."""
        request = _make_request('POST', user=_make_user('guest'))
        assert self.perm.has_permission(request, _view()) is False

    def test_guest_patch_denied(self):
        request = _make_request('PATCH', user=_make_user('guest'))
        assert self.perm.has_permission(request, _view()) is False


# ---------------------------------------------------------------------------
# IsOwnerOrAdmin
# ---------------------------------------------------------------------------

class TestIsOwnerOrAdmin:
    perm = IsOwnerOrAdmin()

    # -- has_permission (authentication gate only) --

    def test_unauthenticated_has_permission_false(self):
        user = _make_user('employee', company_id=1, authenticated=False)
        request = _make_request(user=user)
        assert self.perm.has_permission(request, _view()) is False

    def test_authenticated_has_permission_true(self):
        """Any authenticated user passes has_permission; object gate decides."""
        for role in ('superadmin', 'company_admin', 'employee', 'guest'):
            request = _make_request(user=_make_user(role, company_id=1))
            assert self.perm.has_permission(request, _view()) is True

    # -- has_object_permission --

    def _make_obj(self, owner_user, company_id=None):
        obj = MagicMock()
        obj.user = owner_user
        obj.company_id = company_id
        return obj

    def test_superadmin_allowed_on_any_object(self):
        superadmin = _make_user('superadmin')
        request = _make_request(user=superadmin)
        obj = self._make_obj(owner_user=_make_user('employee', company_id=99))
        assert self.perm.has_object_permission(request, _view(), obj) is True

    def test_owner_allowed(self):
        user = _make_user('employee', company_id=1)
        request = _make_request(user=user)
        obj = self._make_obj(owner_user=user, company_id=1)
        assert self.perm.has_object_permission(request, _view(), obj) is True

    def test_non_owner_employee_denied(self):
        user = _make_user('employee', company_id=1)
        other_user = _make_user('employee', company_id=1)
        request = _make_request(user=user)
        obj = self._make_obj(owner_user=other_user, company_id=1)
        assert self.perm.has_object_permission(request, _view(), obj) is False

    def test_company_admin_same_company_allowed(self):
        admin = _make_user('company_admin', company_id=5)
        other = _make_user('employee', company_id=5)
        request = _make_request(user=admin)
        obj = self._make_obj(owner_user=other, company_id=5)
        assert self.perm.has_object_permission(request, _view(), obj) is True

    def test_company_admin_different_company_denied(self):
        admin = _make_user('company_admin', company_id=5)
        other = _make_user('employee', company_id=99)
        request = _make_request(user=admin)
        obj = self._make_obj(owner_user=other, company_id=99)
        assert self.perm.has_object_permission(request, _view(), obj) is False

    def test_guest_not_owner_denied(self):
        guest = _make_user('guest')
        other = _make_user('employee', company_id=1)
        request = _make_request(user=guest)
        obj = self._make_obj(owner_user=other, company_id=1)
        assert self.perm.has_object_permission(request, _view(), obj) is False

    def test_obj_without_company_attr_does_not_raise(self):
        """Objects without a company attribute must not cause AttributeError."""
        admin = _make_user('company_admin', company_id=5)
        request = _make_request(user=admin)
        obj = MagicMock(spec=[])  # no attributes
        # company_admin is not the owner and obj has no company → should deny
        assert self.perm.has_object_permission(request, _view(), obj) is False

    def test_custom_owner_field(self):
        """owner_field can be customised on the view."""
        perm = IsOwnerOrAdmin()
        perm.owner_field = 'created_by'

        user = _make_user('employee', company_id=1)
        request = _make_request(user=user)
        obj = MagicMock()
        obj.created_by = user
        assert perm.has_object_permission(request, _view(), obj) is True


# ---------------------------------------------------------------------------
# IsSuperAdminOrReception
# ---------------------------------------------------------------------------

class TestIsSuperAdminOrReception:
    perm = IsSuperAdminOrReception()

    def test_unauthenticated_returns_false(self):
        user = _make_user('reception', authenticated=False)
        request = _make_request(user=user)
        assert self.perm.has_permission(request, _view()) is False

    def test_superadmin_allowed(self):
        request = _make_request(user=_make_user('superadmin'))
        assert self.perm.has_permission(request, _view()) is True

    def test_reception_allowed(self):
        request = _make_request(user=_make_user('reception', company_id=1))
        assert self.perm.has_permission(request, _view()) is True

    def test_employee_denied(self):
        request = _make_request(user=_make_user('employee', company_id=1))
        assert self.perm.has_permission(request, _view()) is False

    def test_company_admin_denied(self):
        request = _make_request(user=_make_user('company_admin', company_id=1))
        assert self.perm.has_permission(request, _view()) is False

    def test_guest_denied(self):
        request = _make_request(user=_make_user('guest'))
        assert self.perm.has_permission(request, _view()) is False

    def test_anonymous_user_none_denied(self):
        request = _make_request()
        request.user = None
        assert self.perm.has_permission(request, _view()) is False


# ---------------------------------------------------------------------------
# CompanyIsolationMixin
# ---------------------------------------------------------------------------

class _FakeQS:
    """Minimal queryset double with a filter tracker."""

    def __init__(self, items=None):
        self._items = list(items or [])
        self._filter_kwargs = None

    def filter(self, **kwargs):
        filtered = _FakeQS()
        filtered._filter_kwargs = kwargs
        filtered._items = [
            item for item in self._items
            if all(getattr(item, k.split('_id')[0] + '_id', getattr(item, k, None)) == v for k, v in kwargs.items())
        ]
        return filtered

    @classmethod
    def none(cls):
        qs = cls()
        qs._is_none = True
        return qs

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)


class _BaseView:
    def __init__(self, base_qs):
        self._base_qs = base_qs

    def get_queryset(self):
        return self._base_qs


class _IsolatedView(CompanyIsolationMixin, _BaseView):
    """Properly chained view for MRO testing."""

    def __init__(self, user, base_qs):
        _BaseView.__init__(self, base_qs)
        self.request = MagicMock()
        self.request.user = user


class TestCompanyIsolationMixin:

    def _item(self, company_id):
        obj = MagicMock()
        obj.company_id = company_id
        return obj

    def _qs(self, *company_ids):
        return _FakeQS([self._item(cid) for cid in company_ids])

    def test_superadmin_sees_all(self):
        user = _make_user('superadmin', company_id=None)
        base_qs = self._qs(1, 2, 3)
        view = _IsolatedView(user, base_qs)
        result = view.get_queryset()
        assert result is base_qs  # unchanged reference

    def test_company_admin_sees_own_company_only(self):
        user = _make_user('company_admin', company_id=2)
        base_qs = self._qs(1, 2, 2)

        view = _IsolatedView(user, base_qs)
        result = view.get_queryset()
        assert result._filter_kwargs == {'company': 2}

    def test_employee_sees_own_company_only(self):
        user = _make_user('employee', company_id=3)
        base_qs = self._qs(1, 2, 3)

        view = _IsolatedView(user, base_qs)
        result = view.get_queryset()
        assert result._filter_kwargs == {'company': 3}

    def test_user_without_company_gets_empty_queryset(self):
        user = _make_user('employee', company_id=None)
        base_qs = self._qs(1, 2)

        view = _IsolatedView(user, base_qs)
        result = view.get_queryset()
        assert hasattr(result, '_is_none') or len(result) == 0

    def test_guest_without_company_gets_empty_queryset(self):
        user = _make_user('guest', company_id=None)
        base_qs = self._qs(1, 2)

        view = _IsolatedView(user, base_qs)
        result = view.get_queryset()
        # Guests with no company get qs.none()
        assert hasattr(result, '_is_none') or len(result) == 0

    def test_custom_company_field(self):
        """company_field override must be respected."""
        user = _make_user('employee', company_id=7)
        base_qs = self._qs(7)

        class _CustomFieldView(CompanyIsolationMixin, _BaseView):
            company_field = 'organisation'

            def __init__(self, usr, qs):
                _BaseView.__init__(self, qs)
                self.request = MagicMock()
                self.request.user = usr

        view = _CustomFieldView(user, base_qs)
        result = view.get_queryset()
        assert result._filter_kwargs == {'organisation': 7}

    def test_backward_compat_alias(self):
        """CompanyQuerySetMixin must still resolve to CompanyIsolationMixin."""
        assert CompanyQuerySetMixin is CompanyIsolationMixin


# ---------------------------------------------------------------------------
# CompanyIsolationMixin — company_lookup (traversal FK chain)
# ---------------------------------------------------------------------------

class TestCompanyIsolationMixinLookup:
    """
    Tests for the ``company_lookup`` attribute that allows filtering via an ORM
    traversal path when the model has no direct ``company`` FK.
    """

    def _item_with_traversal(self, company_id):
        """Mock object whose traversal path ends in a company_id."""
        obj = MagicMock()
        # e.g. obj.board__company_id is not how Django works, but our _FakeQS
        # matches on the key passed to filter() — so we fake it via a flat attr
        # that the _FakeQS.filter helper can introspect.
        obj.board__company_id = company_id
        return obj

    def _qs_traversal(self, *company_ids):
        """Return a _FakeQS whose items carry a flat 'board__company_id' attr."""
        items = []
        for cid in company_ids:
            obj = MagicMock()
            # _FakeQS.filter uses getattr(item, k, None) so set the exact key
            # that will be passed: 'board__company_id'
            setattr(obj, 'board__company_id', cid)
            items.append(obj)
        return _FakeQS(items)

    def _make_lookup_view(self, user, base_qs, lookup='board__company'):
        class _LookupView(CompanyIsolationMixin, _BaseView):
            company_lookup = lookup

            def __init__(self, usr, qs):
                _BaseView.__init__(self, qs)
                self.request = MagicMock()
                self.request.user = usr

        return _LookupView(user, base_qs)

    def test_company_lookup_filters_by_traversal_path(self):
        """company_lookup causes filter(**{'<lookup>_id': company_id}) to be applied."""
        user = _make_user('employee', company_id=5)
        base_qs = self._qs_traversal(5, 5, 99)
        view = self._make_lookup_view(user, base_qs, lookup='board__company')
        result = view.get_queryset()
        # The filter kwargs must use the traversal path + '_id'
        assert result._filter_kwargs == {'board__company_id': 5}

    def test_company_lookup_takes_priority_over_company_field(self):
        """When company_lookup is set, company_field is ignored."""
        user = _make_user('employee', company_id=7)
        base_qs = self._qs_traversal(7)

        class _BothView(CompanyIsolationMixin, _BaseView):
            company_field = 'organisation'   # must be ignored
            company_lookup = 'board__company'

            def __init__(self, usr, qs):
                _BaseView.__init__(self, qs)
                self.request = MagicMock()
                self.request.user = usr

        view = _BothView(user, base_qs)
        result = view.get_queryset()
        assert result._filter_kwargs == {'board__company_id': 7}
        # company_field key must NOT appear in filter kwargs
        assert 'organisation' not in result._filter_kwargs

    def test_superadmin_sees_all_with_company_lookup(self):
        """Superadmin bypasses the lookup filter and gets the full queryset."""
        user = _make_user('superadmin', company_id=None)
        base_qs = self._qs_traversal(1, 2, 3)
        view = self._make_lookup_view(user, base_qs)
        result = view.get_queryset()
        assert result is base_qs

    def test_user_without_company_gets_empty_queryset_with_lookup(self):
        """Users with no company get qs.none() regardless of company_lookup."""
        user = _make_user('employee', company_id=None)
        base_qs = self._qs_traversal(1, 2)
        view = self._make_lookup_view(user, base_qs)
        result = view.get_queryset()
        assert hasattr(result, '_is_none') or len(result) == 0


# ---------------------------------------------------------------------------
# Guest role — explicit CRM / HR / internal access denial
# ---------------------------------------------------------------------------

class TestGuestAccessDenial:
    """
    Explicitly verifies the requirement that role='guest' is blocked from
    CRM, HR, and internal company announcements (protected endpoints).
    """

    def test_guest_denied_by_is_company_member(self):
        """IsCompanyMember is the gate for CRM / HR endpoints."""
        perm = IsCompanyMember()
        request = _make_request(user=_make_user('guest'))
        assert perm.has_permission(request, _view()) is False

    def test_guest_denied_by_is_company_admin(self):
        perm = IsCompanyAdmin()
        request = _make_request(user=_make_user('guest'))
        assert perm.has_permission(request, _view()) is False

    def test_guest_denied_write_by_is_company_admin_or_readonly(self):
        perm = IsCompanyAdminOrReadOnly()
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            request = _make_request(method, user=_make_user('guest'))
            assert perm.has_permission(request, _view()) is False, (
                f"Guest should be denied for {method}"
            )

    def test_guest_isolation_mixin_returns_empty_qs(self):
        """Even if a guest reaches a view, they get no data."""
        user = _make_user('guest', company_id=None)
        base_qs = _FakeQS([object(), object()])
        view = _IsolatedView(user, base_qs)
        result = view.get_queryset()
        assert hasattr(result, '_is_none') or len(result) == 0


# ---------------------------------------------------------------------------
# Unauthenticated 401 behaviour
# ---------------------------------------------------------------------------

class TestUnauthenticatedReturns401:
    """
    DRF returns HTTP 401 (not 403) when:
      1. The permission's has_permission returns False, AND
      2. The request has no authentication credentials
         (the WWW-Authenticate challenge is set by SimpleJWT).

    We verify here that all our permission classes return False for
    unauthenticated users so that DRF can issue the correct 401 response.
    """

    PERMISSION_CLASSES = [
        IsSuperAdmin(),
        IsCompanyAdmin(),
        IsCompanyMember(),
        IsCompanyAdminOrReadOnly(),
        IsOwnerOrAdmin(),
        IsSuperAdminOrReception(),
    ]

    def _unauthenticated_request(self, method='GET'):
        anon = MagicMock()
        anon.is_authenticated = False
        anon.role = None
        return _make_request(method, user=anon)

    def test_all_permissions_deny_unauthenticated_get(self):
        request = self._unauthenticated_request('GET')
        for perm in self.PERMISSION_CLASSES:
            result = perm.has_permission(request, _view())
            assert result is False, (
                f"{perm.__class__.__name__} should deny unauthenticated GET"
            )

    def test_all_permissions_deny_unauthenticated_post(self):
        request = self._unauthenticated_request('POST')
        for perm in self.PERMISSION_CLASSES:
            result = perm.has_permission(request, _view())
            assert result is False, (
                f"{perm.__class__.__name__} should deny unauthenticated POST"
            )


# ---------------------------------------------------------------------------
# IsCompanyPremium
# ---------------------------------------------------------------------------

class TestIsCompanyPremium:
    """
    IsCompanyPremium: passes only for superadmin OR authenticated users whose
    company.plan == 'premium'. All others receive 403 (PermissionDenied).

    No DB required — all users are constructed via MagicMock.
    """

    perm = IsCompanyPremium()

    def test_unauthenticated_returns_false(self):
        user = _make_user('company_admin', company_id=1, authenticated=False)
        request = _make_request(user=user)
        assert self.perm.has_permission(request, _view()) is False

    def test_none_user_returns_false(self):
        request = _make_request()
        request.user = None
        assert self.perm.has_permission(request, _view()) is False

    def test_superadmin_always_passes(self):
        """Superadmin bypasses the premium plan gate unconditionally."""
        request = _make_request(user=_make_user('superadmin', company_id=None))
        assert self.perm.has_permission(request, _view()) is True

    def test_company_admin_with_premium_plan_passes(self):
        request = _make_request(user=_make_user('company_admin', company_id=1, plan='premium'))
        assert self.perm.has_permission(request, _view()) is True

    def test_employee_with_premium_plan_passes(self):
        request = _make_request(user=_make_user('employee', company_id=1, plan='premium'))
        assert self.perm.has_permission(request, _view()) is True

    def test_company_admin_with_basic_plan_raises_permission_denied(self):
        request = _make_request(user=_make_user('company_admin', company_id=1, plan='basic'))
        with pytest.raises(PermissionDenied):
            self.perm.has_permission(request, _view())

    def test_company_admin_with_standard_plan_raises_permission_denied(self):
        request = _make_request(user=_make_user('company_admin', company_id=1, plan='standard'))
        with pytest.raises(PermissionDenied):
            self.perm.has_permission(request, _view())

    def test_employee_with_basic_plan_raises_permission_denied(self):
        request = _make_request(user=_make_user('employee', company_id=1, plan='basic'))
        with pytest.raises(PermissionDenied):
            self.perm.has_permission(request, _view())

    def test_guest_without_company_returns_false(self):
        """Guest has no company — returns False (not PermissionDenied)."""
        request = _make_request(user=_make_user('guest', company_id=None))
        assert self.perm.has_permission(request, _view()) is False

    def test_building_staff_without_company_returns_false(self):
        """reception / service_manager have no company — returns False."""
        for role in ('reception', 'service_manager'):
            request = _make_request(user=_make_user(role, company_id=None))
            assert self.perm.has_permission(request, _view()) is False, (
                f"Expected False for {role} without company"
            )
