"""
Acceptance tests for tariff-based resource visibility and booking restrictions (DEV-tariff).

Plan matrix:
  basic / free  → may only see/book shared resources (assigned_company IS NULL)
  standard      → may see/book shared + own assigned resources
  premium       → same as standard
  superadmin    → sees everything, books everything
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Resource
from apps.companies.models import Company
from apps.users.models import User

# Patch targets for notifications triggered inside BookingCreateSerializer.create().
# create_notification handles in-app notifications; send_notification_email is the Celery email task.
# Both must be mocked to prevent DB errors from the missing notification_preferences column in test DB.
_NOTIFY_PATH = 'apps.bookings.serializers.create_notification'
_EMAIL_TASK_PATH = 'apps.notifications.tasks.send_notification_email'

RESOURCES_URL = '/api/v1/bookings/resources/'
RESERVATIONS_URL = '/api/v1/bookings/reservations/'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_monday_at(hour, minute=0):
    """Return the next Monday at the given local time (always in the future)."""
    now = timezone.localtime()
    days_to_add = (0 - now.weekday()) % 7
    if days_to_add == 0:
        days_to_add = 7
    target = now + timedelta(days=days_to_add)
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def basic_company(db):
    return Company.objects.create(name='Basic Corp', plan='basic')


@pytest.fixture
def standard_company(db):
    return Company.objects.create(name='Standard Corp', plan='standard')


@pytest.fixture
def premium_company(db):
    return Company.objects.create(name='Premium Corp', plan='premium')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Corp', plan='premium')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='superadmin@tariff.test',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def basic_admin(db, basic_company):
    return User.objects.create_user(
        email='basic_admin@tariff.test',
        password='pass',
        first_name='Basic',
        last_name='Admin',
        role='company_admin',
        company=basic_company,
        is_email_verified=True,
    )


@pytest.fixture
def basic_employee(db, basic_company):
    return User.objects.create_user(
        email='basic_emp@tariff.test',
        password='pass',
        first_name='Basic',
        last_name='Emp',
        role='employee',
        company=basic_company,
        is_email_verified=True,
    )


@pytest.fixture
def premium_admin(db, premium_company):
    return User.objects.create_user(
        email='premium_admin@tariff.test',
        password='pass',
        first_name='Premium',
        last_name='Admin',
        role='company_admin',
        company=premium_company,
        is_email_verified=True,
    )


@pytest.fixture
def premium_employee(db, premium_company):
    return User.objects.create_user(
        email='premium_emp@tariff.test',
        password='pass',
        first_name='Premium',
        last_name='Emp',
        role='employee',
        company=premium_company,
        is_email_verified=True,
    )


@pytest.fixture
def standard_employee(db, standard_company):
    return User.objects.create_user(
        email='standard_emp@tariff.test',
        password='pass',
        first_name='Standard',
        last_name='Emp',
        role='employee',
        company=standard_company,
        is_email_verified=True,
    )


@pytest.fixture
def shared_resource(db):
    """A resource with no assigned company — visible to all."""
    return Resource.objects.create(
        name='Shared Desk Tariff',
        resource_type='desk',
        assigned_company=None,
        available_days=[0, 1, 2, 3, 4],
        is_active=True,
    )


@pytest.fixture
def basic_assigned_resource(db, basic_company):
    """A resource assigned to the basic company."""
    return Resource.objects.create(
        name='Basic Co Desk',
        resource_type='desk',
        assigned_company=basic_company,
        available_days=[0, 1, 2, 3, 4],
        is_active=True,
    )


@pytest.fixture
def premium_assigned_resource(db, premium_company):
    """A resource assigned to the premium company."""
    return Resource.objects.create(
        name='Premium Co Desk',
        resource_type='desk',
        assigned_company=premium_company,
        available_days=[0, 1, 2, 3, 4],
        is_active=True,
    )


@pytest.fixture
def other_assigned_resource(db, other_company):
    """A resource assigned to a third company."""
    return Resource.objects.create(
        name='Other Co Desk',
        resource_type='desk',
        assigned_company=other_company,
        available_days=[0, 1, 2, 3, 4],
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Resource catalog visibility tests (GET /api/v1/bookings/resources/)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestResourceCatalogTariff:
    """Verify that the resource list is filtered per company plan."""

    def test_basic_admin_cannot_see_assigned_resource(
        self, api_client, basic_admin, shared_resource, basic_assigned_resource
    ):
        """basic plan: assigned resources are hidden — only shared ones appear."""
        api_client.force_authenticate(user=basic_admin)
        response = api_client.get(RESOURCES_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [r['id'] for r in response.json()['results']]
        assert shared_resource.id in ids
        assert basic_assigned_resource.id not in ids

    def test_basic_employee_cannot_see_any_assigned_resource(
        self, api_client, basic_employee, shared_resource,
        basic_assigned_resource, premium_assigned_resource
    ):
        """basic plan: no assigned resources visible regardless of which company they belong to."""
        api_client.force_authenticate(user=basic_employee)
        response = api_client.get(RESOURCES_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [r['id'] for r in response.json()['results']]
        assert shared_resource.id in ids
        assert basic_assigned_resource.id not in ids
        assert premium_assigned_resource.id not in ids

    def test_premium_admin_sees_shared_and_own_assigned(
        self, api_client, premium_admin, shared_resource,
        premium_assigned_resource, other_assigned_resource
    ):
        """premium plan: sees shared resources + own assigned resources, not other companies'."""
        api_client.force_authenticate(user=premium_admin)
        response = api_client.get(RESOURCES_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [r['id'] for r in response.json()['results']]
        assert shared_resource.id in ids
        assert premium_assigned_resource.id in ids
        assert other_assigned_resource.id not in ids

    def test_standard_employee_sees_shared_and_own_assigned(
        self, api_client, standard_employee, shared_resource,
        basic_assigned_resource
    ):
        """standard plan: sees shared resources; resources of other companies are hidden."""
        api_client.force_authenticate(user=standard_employee)
        response = api_client.get(RESOURCES_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [r['id'] for r in response.json()['results']]
        assert shared_resource.id in ids
        # basic_assigned_resource belongs to basic_company, not standard_company → hidden
        assert basic_assigned_resource.id not in ids

    def test_superadmin_sees_all_resources(
        self, api_client, superadmin, shared_resource,
        basic_assigned_resource, premium_assigned_resource
    ):
        """superadmin has no plan restriction — sees all resources."""
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(RESOURCES_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [r['id'] for r in response.json()['results']]
        assert shared_resource.id in ids
        assert basic_assigned_resource.id in ids
        assert premium_assigned_resource.id in ids


# ---------------------------------------------------------------------------
# Booking creation plan-gate tests (POST /api/v1/bookings/reservations/)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBookingTariffGate:
    """Verify that plan-based access control is enforced when creating bookings."""

    def test_basic_employee_cannot_book_assigned_resource(
        self, api_client, basic_employee, basic_assigned_resource
    ):
        """basic plan employee gets 400 trying to book a company-assigned resource."""
        api_client.force_authenticate(user=basic_employee)
        start = _next_monday_at(10)
        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': basic_assigned_resource.id,
                'start_time': start.isoformat(),
                'end_time': (start + timedelta(hours=1)).isoformat(),
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'resource_id' in response.json()['error']['details']

    def test_basic_employee_can_book_shared_resource(
        self, api_client, basic_employee, shared_resource
    ):
        """basic plan employee CAN book shared (unassigned) resources."""
        api_client.force_authenticate(user=basic_employee)
        start = _next_monday_at(11)
        with patch(_NOTIFY_PATH), patch(_EMAIL_TASK_PATH):
            response = api_client.post(
                RESERVATIONS_URL,
                {
                    'resource_id': shared_resource.id,
                    'start_time': start.isoformat(),
                    'end_time': (start + timedelta(hours=1)).isoformat(),
                },
                format='json',
            )
        assert response.status_code == status.HTTP_201_CREATED

    def test_premium_employee_can_book_own_assigned_resource(
        self, api_client, premium_employee, premium_assigned_resource
    ):
        """premium plan employee CAN book a resource assigned to their own company."""
        api_client.force_authenticate(user=premium_employee)
        start = _next_monday_at(12)
        with patch(_NOTIFY_PATH), patch(_EMAIL_TASK_PATH):
            response = api_client.post(
                RESERVATIONS_URL,
                {
                    'resource_id': premium_assigned_resource.id,
                    'start_time': start.isoformat(),
                    'end_time': (start + timedelta(hours=1)).isoformat(),
                },
                format='json',
            )
        assert response.status_code == status.HTTP_201_CREATED

    def test_premium_employee_cannot_book_other_company_assigned_resource(
        self, api_client, premium_employee, other_assigned_resource
    ):
        """premium plan employee gets 400 for a resource assigned to a different company."""
        api_client.force_authenticate(user=premium_employee)
        start = _next_monday_at(13)
        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': other_assigned_resource.id,
                'start_time': start.isoformat(),
                'end_time': (start + timedelta(hours=1)).isoformat(),
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'resource_id' in response.json()['error']['details']

    def test_superadmin_can_book_any_assigned_resource(
        self, api_client, superadmin, premium_assigned_resource
    ):
        """superadmin bypasses all plan and company restrictions."""
        api_client.force_authenticate(user=superadmin)
        start = _next_monday_at(14)
        with patch(_NOTIFY_PATH), patch(_EMAIL_TASK_PATH):
            response = api_client.post(
                RESERVATIONS_URL,
                {
                    'resource_id': premium_assigned_resource.id,
                    'start_time': start.isoformat(),
                    'end_time': (start + timedelta(hours=1)).isoformat(),
                },
                format='json',
            )
        assert response.status_code == status.HTTP_201_CREATED

    def test_standard_employee_can_book_own_assigned_resource(
        self, api_client, standard_employee, standard_company
    ):
        """standard plan employee CAN book a resource assigned to their own company."""
        resource = Resource.objects.create(
            name='Standard Co Desk',
            resource_type='desk',
            assigned_company=standard_company,
            available_days=[0, 1, 2, 3, 4],
            is_active=True,
        )
        api_client.force_authenticate(user=standard_employee)
        start = _next_monday_at(15)
        with patch(_NOTIFY_PATH), patch(_EMAIL_TASK_PATH):
            response = api_client.post(
                RESERVATIONS_URL,
                {
                    'resource_id': resource.id,
                    'start_time': start.isoformat(),
                    'end_time': (start + timedelta(hours=1)).isoformat(),
                },
                format='json',
            )
        assert response.status_code == status.HTTP_201_CREATED


# ---------------------------------------------------------------------------
# Resource assigned_company plan validation tests (POST/PATCH /api/v1/bookings/resources/)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestResourceAssignedCompanyValidation:
    """Verify that assigning a resource to a basic/free company is rejected at the serializer level."""

    _RESOURCE_PAYLOAD = {
        'type': 'desk',
        'name': 'Test Desk',
        'is_active': True,
    }

    def test_create_with_basic_company_returns_400(
        self, api_client, superadmin, basic_company
    ):
        """POST with assigned_company that has plan=basic → 400 with assigned_company field error."""
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            RESOURCES_URL,
            {**self._RESOURCE_PAYLOAD, 'name': 'AC Basic Desk', 'assigned_company': basic_company.id},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'assigned_company' in response.json()['error']['details']

    def test_create_with_free_company_returns_400(
        self, api_client, superadmin
    ):
        """POST with assigned_company that has plan=free → 400 with assigned_company field error."""
        free_company = Company.objects.create(name='Free Corp', plan='free')
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            RESOURCES_URL,
            {**self._RESOURCE_PAYLOAD, 'name': 'AC Free Desk', 'assigned_company': free_company.id},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'assigned_company' in response.json()['error']['details']

    def test_create_with_premium_company_returns_201(
        self, api_client, superadmin, premium_company
    ):
        """POST with assigned_company that has plan=premium → 201 Created."""
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            RESOURCES_URL,
            {**self._RESOURCE_PAYLOAD, 'name': 'AC Premium Desk', 'assigned_company': premium_company.id},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_create_with_standard_company_returns_201(
        self, api_client, superadmin, standard_company
    ):
        """POST with assigned_company that has plan=standard → 201 Created."""
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            RESOURCES_URL,
            {**self._RESOURCE_PAYLOAD, 'name': 'AC Standard Desk', 'assigned_company': standard_company.id},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_patch_with_basic_company_returns_400(
        self, api_client, superadmin, shared_resource, basic_company
    ):
        """PATCH an existing resource to assign a basic-plan company → 400."""
        api_client.force_authenticate(user=superadmin)
        response = api_client.patch(
            f'{RESOURCES_URL}{shared_resource.id}/',
            {'assigned_company': basic_company.id},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'assigned_company' in response.json()['error']['details']

    def test_patch_with_null_assigned_company_returns_200(
        self, api_client, superadmin, premium_assigned_resource
    ):
        """PATCH assigned_company=null (clearing the assignment) → 200 OK."""
        api_client.force_authenticate(user=superadmin)
        response = api_client.patch(
            f'{RESOURCES_URL}{premium_assigned_resource.id}/',
            {'assigned_company': None},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['assigned_company'] is None

    def test_superadmin_follows_plan_rules_same_as_others(
        self, api_client, superadmin, shared_resource, basic_company
    ):
        """Plan validation is data-level, not role-level: superadmin cannot bypass it."""
        api_client.force_authenticate(user=superadmin)
        response = api_client.patch(
            f'{RESOURCES_URL}{shared_resource.id}/',
            {'assigned_company': basic_company.id},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'assigned_company' in response.json()['error']['details']
