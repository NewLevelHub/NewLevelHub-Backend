"""
Integration tests for GET /api/v1/auth/me/activity/

Coverage:
  - 401 for unauthenticated request
  - 200 + correct shape for each role
  - guests receive tasks=[]
  - reception / service_manager receive tasks=[] and passes=[]
  - limit: at most 5 items per list even when more exist
  - only own data is returned (bookings by user/participant, tasks by assignee, passes by created_by)
"""
import pytest
from django.utils import timezone
from rest_framework.test import APIClient

URL = '/api/v1/auth/me/activity/'


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def company(db):
    from apps.companies.models import Company
    return Company.objects.create(name='Test Co')


def _make_user(db_marker, email, role, company=None):
    from apps.users.models import User
    return User.objects.create_user(
        email=email, password='pass', first_name='A', last_name='B',
        role=role, company=company,
    )


@pytest.fixture
def employee(db, company):
    return _make_user(db, 'emp@test.com', 'employee', company)


@pytest.fixture
def admin(db, company):
    return _make_user(db, 'admin@test.com', 'company_admin', company)


@pytest.fixture
def guest(db):
    return _make_user(db, 'guest@test.com', 'guest')


@pytest.fixture
def superadmin(db):
    return _make_user(db, 'super@test.com', 'superadmin')


@pytest.fixture
def reception(db):
    return _make_user(db, 'recep@test.com', 'reception')


@pytest.fixture
def service_manager(db):
    return _make_user(db, 'sm@test.com', 'service_manager')


def _make_resource():
    from apps.bookings.models import Resource
    return Resource.objects.create(
        name='Room A', resource_type='meeting_room', capacity=4,
    )


def _make_booking(user, resource, company=None):
    from apps.bookings.models import Booking
    now = timezone.now()
    return Booking.objects.create(
        user=user, resource=resource, company=company or user.company,
        start_time=now, end_time=now + timezone.timedelta(hours=1),
    )


def _make_board_column(company):
    from apps.crm.models import Board, Column
    board = Board.objects.create(name='Board', company=company)
    column = Column.objects.create(name='Todo', board=board, position=0)
    return column


def _make_task(assignee, column):
    from apps.crm.models import Task
    return Task.objects.create(
        title='Task', column=column,
        created_by=assignee, assignee=assignee, position=0,
    )


def _make_pass(created_by, company=None):
    from apps.access.models import GuestPass
    now = timezone.now()
    return GuestPass.objects.create(
        created_by=created_by,
        company=company or created_by.company,
        guest_name='Guest Person',
        guest_email='gp@test.com',
        valid_from=now,
        valid_until=now + timezone.timedelta(days=1),
    )


# ── Tests ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_unauthenticated_returns_401(client):
    resp = client.get(URL)
    assert resp.status_code == 401


@pytest.mark.django_db
def test_response_shape_employee(client, employee, company):
    resource = _make_resource()
    _make_booking(employee, resource)
    column = _make_board_column(company)
    _make_task(employee, column)
    _make_pass(employee, company)

    client.force_authenticate(employee)
    resp = client.get(URL)

    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {'bookings', 'tasks', 'passes'}
    assert len(data['bookings']) == 1
    assert len(data['tasks']) == 1
    assert len(data['passes']) == 1

    # Verify field shapes
    booking = data['bookings'][0]
    assert {'id', 'resource_name', 'start_time', 'end_time', 'status'} <= set(booking.keys())

    task = data['tasks'][0]
    assert {'id', 'title', 'priority', 'deadline', 'board_name', 'board_id'} <= set(task.keys())

    gp = data['passes'][0]
    assert {'id', 'guest_name', 'status', 'valid_from', 'valid_until'} <= set(gp.keys())


@pytest.mark.django_db
def test_guest_receives_empty_tasks(client, guest, company):
    resource = _make_resource()
    _make_booking(guest, resource, company)
    _make_pass(guest, company=None)

    client.force_authenticate(guest)
    resp = client.get(URL)

    assert resp.status_code == 200
    data = resp.json()
    assert data['tasks'] == []
    assert len(data['bookings']) == 1
    assert len(data['passes']) == 1


@pytest.mark.django_db
def test_reception_receives_empty_tasks_and_passes(client, reception):
    client.force_authenticate(reception)
    resp = client.get(URL)

    assert resp.status_code == 200
    data = resp.json()
    assert data['tasks'] == []
    assert data['passes'] == []


@pytest.mark.django_db
def test_service_manager_receives_empty_tasks_and_passes(client, service_manager):
    client.force_authenticate(service_manager)
    resp = client.get(URL)

    assert resp.status_code == 200
    data = resp.json()
    assert data['tasks'] == []
    assert data['passes'] == []


@pytest.mark.django_db
def test_limit_enforced(client, employee, company):
    """At most 5 items per list even when more exist."""
    resource = _make_resource()
    column = _make_board_column(company)
    for _ in range(7):
        _make_booking(employee, resource)
        _make_task(employee, column)
        _make_pass(employee, company)

    client.force_authenticate(employee)
    resp = client.get(URL)

    data = resp.json()
    assert len(data['bookings']) <= 5
    assert len(data['tasks']) <= 5
    assert len(data['passes']) <= 5


@pytest.mark.django_db
def test_only_own_data_returned(client, employee, admin, company):
    """Employee should not see admin's tasks or passes."""
    resource = _make_resource()
    column = _make_board_column(company)
    _make_booking(admin, resource)
    _make_task(admin, column)
    _make_pass(admin, company)

    client.force_authenticate(employee)
    resp = client.get(URL)

    data = resp.json()
    assert data['bookings'] == []
    assert data['tasks'] == []
    assert data['passes'] == []


@pytest.mark.django_db
def test_booking_as_participant_included(client, employee, admin, company):
    """Bookings where the user is a participant (not owner) must appear."""
    from apps.bookings.models import BookingParticipant
    resource = _make_resource()
    booking = _make_booking(admin, resource)
    BookingParticipant.objects.create(booking=booking, user=employee)

    client.force_authenticate(employee)
    resp = client.get(URL)

    data = resp.json()
    ids = [b['id'] for b in data['bookings']]
    assert booking.id in ids


@pytest.mark.django_db
def test_company_admin_sees_all_company_passes(client, admin, employee, company):
    """company_admin should see passes created by any member of their company."""
    _make_pass(employee, company)

    client.force_authenticate(admin)
    resp = client.get(URL)

    data = resp.json()
    assert len(data['passes']) == 1


@pytest.mark.django_db
def test_superadmin_sees_own_data(client, superadmin, company):
    resource = _make_resource()
    _make_booking(superadmin, resource, company)
    column = _make_board_column(company)
    _make_task(superadmin, column)
    _make_pass(superadmin, company)

    client.force_authenticate(superadmin)
    resp = client.get(URL)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data['bookings']) == 1
    assert len(data['tasks']) == 1
    assert len(data['passes']) == 1
