from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.crm.models import Board, Column, Task
from apps.users.models import User


def directory_url(company_id):
    return f"/api/v1/companies/{company_id}/directory/"


def directory_profile_url(company_id, user_id):
    return f"/api/v1/companies/{company_id}/directory/{user_id}/"


def auth(client, user):
    client.force_authenticate(user=user)


def unwrap_results(response):
    if isinstance(response.data, dict) and "results" in response.data:
        return response.data["results"]
    return response.data


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return Company.objects.create(name="Alpha Corp", plan="basic", max_employees=50)


@pytest.fixture
def company_b(db):
    return Company.objects.create(name="Beta Corp", plan="basic", max_employees=50)


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email="superadmin@test.com",
        password="pass",
        first_name="Super",
        last_name="Admin",
        role="superadmin",
    )


@pytest.fixture
def company_admin_a(db, company_a):
    return User.objects.create_user(
        email="admin.alpha@test.com",
        password="pass",
        first_name="Alice",
        last_name="Admin",
        role="company_admin",
        company=company_a,
        position="Head of Ops",
        phone="+70000000001",
    )


@pytest.fixture
def employee_a(db, company_a):
    return User.objects.create_user(
        email="employee.alpha@test.com",
        password="pass",
        first_name="Eve",
        last_name="Employee",
        role="employee",
        company=company_a,
        position="Designer",
        phone="+70000000002",
    )


@pytest.fixture
def company_admin_b(db, company_b):
    return User.objects.create_user(
        email="admin.beta@test.com",
        password="pass",
        first_name="Bob",
        last_name="Admin",
        role="company_admin",
        company=company_b,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email="guest@test.com",
        password="pass",
        first_name="Gary",
        last_name="Guest",
        role="guest",
    )


@pytest.mark.django_db
class TestCompanyDirectoryListAccess:
    def test_unauthenticated_returns_401(self, api_client, company_a):
        response = api_client.get(directory_url(company_a.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_returns_403(self, api_client, company_a, guest_user):
        auth(api_client, guest_user)
        response = api_client.get(directory_url(company_a.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_from_same_company_has_access(self, api_client, company_a, employee_a):
        auth(api_client, employee_a)
        response = api_client.get(directory_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK

    def test_company_admin_from_same_company_has_access(self, api_client, company_a, company_admin_a):
        auth(api_client, company_admin_a)
        response = api_client.get(directory_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK

    def test_superadmin_has_access(self, api_client, company_a, superadmin):
        auth(api_client, superadmin)
        response = api_client.get(directory_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK

    def test_employee_from_other_company_gets_403(self, api_client, company_b, employee_a):
        auth(api_client, employee_a)
        response = api_client.get(directory_url(company_b.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestCompanyDirectoryListContract:
    def test_returns_required_fields(
        self, api_client, company_a, company_admin_a, employee_a
    ):
        auth(api_client, company_admin_a)
        response = api_client.get(directory_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK

        rows = unwrap_results(response)
        assert len(rows) >= 1
        required = {
            "id",
            "avatar",
            "full_name",
            "position",
            "email",
            "phone",
            "role",
            "is_active",
            "last_login",
        }
        assert required.issubset(set(rows[0].keys()))

    def test_scopes_members_to_company(
        self, api_client, superadmin, company_a, company_b, company_admin_a, company_admin_b
    ):
        auth(api_client, superadmin)
        response = api_client.get(directory_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK
        ids = {row["id"] for row in unwrap_results(response)}
        assert company_admin_a.id in ids
        assert company_admin_b.id not in ids


@pytest.mark.django_db
class TestCompanyDirectoryListSearchFilterOrder:
    def test_search_by_first_name(self, api_client, company_a, company_admin_a):
        target = User.objects.create_user(
            email="john.one@test.com",
            password="pass",
            first_name="John",
            last_name="Wick",
            role="employee",
            company=company_a,
            position="Engineer",
        )
        auth(api_client, company_admin_a)
        response = api_client.get(directory_url(company_a.id), {"search": "John"})
        assert response.status_code == status.HTTP_200_OK
        ids = {row["id"] for row in unwrap_results(response)}
        assert target.id in ids

    def test_search_by_last_name(self, api_client, company_a, company_admin_a):
        target = User.objects.create_user(
            email="kate.two@test.com",
            password="pass",
            first_name="Kate",
            last_name="Harrison",
            role="employee",
            company=company_a,
            position="Engineer",
        )
        auth(api_client, company_admin_a)
        response = api_client.get(directory_url(company_a.id), {"search": "Harrison"})
        assert response.status_code == status.HTTP_200_OK
        ids = {row["id"] for row in unwrap_results(response)}
        assert target.id in ids

    def test_search_by_email(self, api_client, company_a, company_admin_a):
        target = User.objects.create_user(
            email="mark.three@test.com",
            password="pass",
            first_name="Mark",
            last_name="Stone",
            role="employee",
            company=company_a,
            position="Engineer",
        )
        auth(api_client, company_admin_a)
        response = api_client.get(directory_url(company_a.id), {"search": "mark.three"})
        assert response.status_code == status.HTTP_200_OK
        ids = {row["id"] for row in unwrap_results(response)}
        assert target.id in ids

    def test_filter_by_position(self, api_client, company_a, company_admin_a):
        designer = User.objects.create_user(
            email="designer@test.com",
            password="pass",
            first_name="Diana",
            last_name="Pixel",
            role="employee",
            company=company_a,
            position="Designer",
        )
        User.objects.create_user(
            email="analyst@test.com",
            password="pass",
            first_name="Andrew",
            last_name="Data",
            role="employee",
            company=company_a,
            position="Analyst",
        )

        auth(api_client, company_admin_a)
        response = api_client.get(directory_url(company_a.id), {"position": "Designer"})
        assert response.status_code == status.HTTP_200_OK
        ids = {row["id"] for row in unwrap_results(response)}
        assert designer.id in ids

    def test_filter_by_role(self, api_client, company_a, company_admin_a):
        employee = User.objects.create_user(
            email="member@test.com",
            password="pass",
            first_name="Member",
            last_name="One",
            role="employee",
            company=company_a,
            position="Engineer",
        )
        auth(api_client, company_admin_a)
        response = api_client.get(directory_url(company_a.id), {"role": "employee"})
        assert response.status_code == status.HTTP_200_OK
        rows = unwrap_results(response)
        assert rows
        assert all(row["role"] == "employee" for row in rows)
        ids = {row["id"] for row in rows}
        assert employee.id in ids

    def test_ordering_by_full_name(self, api_client, company_a, company_admin_a):
        User.objects.create_user(
            email="zeta@test.com",
            password="pass",
            first_name="Zeta",
            last_name="Smith",
            role="employee",
            company=company_a,
            position="Engineer",
        )
        User.objects.create_user(
            email="alpha@test.com",
            password="pass",
            first_name="Alpha",
            last_name="Brown",
            role="employee",
            company=company_a,
            position="Engineer",
        )
        auth(api_client, company_admin_a)
        response = api_client.get(directory_url(company_a.id), {"ordering": "full_name"})
        assert response.status_code == status.HTTP_200_OK
        names = [row["full_name"] for row in unwrap_results(response)]
        assert names == sorted(names)

    def test_ordering_by_date_joined_desc(self, api_client, company_a, company_admin_a):
        older = User.objects.create_user(
            email="older@test.com",
            password="pass",
            first_name="Old",
            last_name="User",
            role="employee",
            company=company_a,
            position="Engineer",
        )
        newer = User.objects.create_user(
            email="newer@test.com",
            password="pass",
            first_name="New",
            last_name="User",
            role="employee",
            company=company_a,
            position="Engineer",
        )
        older.date_joined = timezone.now() - timedelta(days=10)
        newer.date_joined = timezone.now()
        older.save(update_fields=["date_joined"])
        newer.save(update_fields=["date_joined"])

        auth(api_client, company_admin_a)
        response = api_client.get(directory_url(company_a.id), {"ordering": "-date_joined"})
        assert response.status_code == status.HTTP_200_OK
        ids = [row["id"] for row in unwrap_results(response)]
        assert ids.index(newer.id) < ids.index(older.id)


@pytest.mark.django_db
class TestCompanyDirectoryProfileAccess:
    def test_unauthenticated_returns_401(self, api_client, company_a, employee_a):
        response = api_client.get(directory_profile_url(company_a.id, employee_a.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_returns_403(self, api_client, company_a, employee_a, guest_user):
        auth(api_client, guest_user)
        response = api_client.get(directory_profile_url(company_a.id, employee_a.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_from_same_company_has_access(self, api_client, company_a, employee_a):
        auth(api_client, employee_a)
        response = api_client.get(directory_profile_url(company_a.id, employee_a.id))
        assert response.status_code == status.HTTP_200_OK

    def test_employee_from_other_company_gets_403(self, api_client, company_b, employee_a):
        auth(api_client, employee_a)
        response = api_client.get(directory_profile_url(company_b.id, employee_a.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_has_access(self, api_client, company_a, employee_a, superadmin):
        auth(api_client, superadmin)
        response = api_client.get(directory_profile_url(company_a.id, employee_a.id))
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestCompanyDirectoryProfileContract:
    def test_returns_contacts_and_activity_metrics(
        self, api_client, company_a, company_admin_a, employee_a
    ):
        board = Board.objects.create(company=company_a, name="Team Board", created_by=company_admin_a)
        column = Column.objects.create(board=board, name="Todo", position=0)

        Task.objects.create(
            column=column,
            title="Task A",
            assignee=employee_a,
            created_by=company_admin_a,
            is_archived=False,
        )
        Task.objects.create(
            column=column,
            title="Task B",
            assignee=employee_a,
            created_by=company_admin_a,
            is_archived=True,
        )

        resource = Resource.objects.create(name="Desk A1", resource_type="desk")
        now = timezone.now()
        Booking.objects.create(
            resource=resource,
            user=employee_a,
            company=company_a,
            start_time=now - timedelta(days=2),
            end_time=now - timedelta(days=2) + timedelta(hours=1),
            status="confirmed",
        )
        Booking.objects.create(
            resource=resource,
            user=employee_a,
            company=company_a,
            start_time=now - timedelta(days=40),
            end_time=now - timedelta(days=40) + timedelta(hours=1),
            status="confirmed",
        )

        auth(api_client, company_admin_a)
        response = api_client.get(directory_profile_url(company_a.id, employee_a.id))
        assert response.status_code == status.HTTP_200_OK

        required = {
            "id",
            "avatar",
            "full_name",
            "position",
            "email",
            "phone",
            "role",
            "is_active",
            "last_login",
            "tasks_count",
            "bookings_last_30_days",
        }
        assert required.issubset(set(response.data.keys()))
        assert response.data["tasks_count"] == 2
        assert response.data["bookings_last_30_days"] == 1
