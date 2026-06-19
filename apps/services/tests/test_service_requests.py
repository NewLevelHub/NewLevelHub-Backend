import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.services.models import Floor, ServiceRequest
from apps.users.models import User

BASE_URL = '/api/v1/services/requests/'
QUICK_CLEANING_URL = '/api/v1/services/requests/quick-cleaning/'
LEGACY_CLEANING_URL = '/api/v1/services/requests/cleaning/'


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Acme Corp', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Corp', plan='basic')


@pytest.fixture
def floor(db):
    # Use a high number unlikely to conflict with leftover test data
    import random
    number = random.randint(9000, 9999)
    return Floor.objects.create(number=number, name='Test Floor')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='superadmin@test.com',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def admin(db, company):
    return User.objects.create_user(
        email='admin@test.com',
        password='pass',
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@test.com',
        password='pass',
        first_name='Em',
        last_name='Ployee',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def other_employee(db, other_company):
    return User.objects.create_user(
        email='other@test.com',
        password='pass',
        first_name='Other',
        last_name='User',
        role='employee',
        company=other_company,
        is_email_verified=True,
    )


@pytest.fixture
def guest(db):
    return User.objects.create_user(
        email='guest@test.com',
        password='pass',
        first_name='Gu',
        last_name='Est',
        role='guest',
        is_email_verified=True,
    )


@pytest.fixture
def service_request(db, employee, company, floor):
    return ServiceRequest.objects.create(
        created_by=employee,
        company=company,
        request_type='repair',
        urgency='medium',
        floor=floor,
        location='Room 101',
        description='The AC is broken',
        status='new',
    )


@pytest.fixture
def completed_request(db, employee, company, floor):
    return ServiceRequest.objects.create(
        created_by=employee,
        company=company,
        request_type='cleaning',
        urgency='low',
        floor=floor,
        description='Needs cleaning',
        status='completed',
        completed_at=timezone.now(),
    )


def auth(client, user):
    client.force_authenticate(user=user)
    return client


# ── POST /api/v1/services/requests/ ──────────────────────────────────

@pytest.mark.django_db
def test_create_service_request_employee(api_client, employee, floor):
    auth(api_client, employee)
    payload = {
        'request_type': 'repair',
        'urgency': 'high',
        'floor': floor.pk,
        'location': 'Room 202',
        'description': 'Broken window',
    }
    response = api_client.post(BASE_URL, payload, format='json')
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data['request_type'] == 'repair'
    assert data['urgency'] == 'high'
    assert data['status'] == 'new'
    assert isinstance(data['created_by'], dict)
    assert data['created_by']['id'] == employee.pk
    assert isinstance(data['company'], dict)
    assert data['company']['id'] == employee.company_id
    assert data['floor'] == floor.pk
    assert data['floor_number'] == floor.number
    assert data['floor_name'] == floor.name


@pytest.mark.django_db
def test_create_service_request_accepts_floor_number(api_client, employee, floor):
    auth(api_client, employee)
    payload = {
        'request_type': 'general',
        'urgency': 'normal',
        'floor': floor.number,
        'location': 'Room 12',
        'description': 'Needs assistance',
    }
    response = api_client.post(BASE_URL, payload, format='json')
    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()['floor'] == floor.pk


@pytest.mark.django_db
def test_create_service_request_sets_company_automatically(api_client, employee, floor):
    auth(api_client, employee)
    payload = {
        'request_type': 'cleaning',
        'urgency': 'low',
        'floor': floor.pk,
        'location': 'Lobby',
        'description': 'Need cleanup near reception',
    }
    response = api_client.post(BASE_URL, payload, format='json')
    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()['company']['id'] == employee.company_id


@pytest.mark.django_db
def test_create_service_request_missing_required_fields(api_client, employee):
    auth(api_client, employee)
    payload = {'request_type': 'cleaning'}
    response = api_client.post(BASE_URL, payload, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()['error']['details']
    assert 'floor' in data
    assert 'location' in data
    assert 'description' in data
    assert 'urgency' in data


@pytest.mark.django_db
def test_create_service_request_unauthenticated(api_client):
    response = api_client.post(BASE_URL, {'request_type': 'cleaning'}, format='json')
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_create_service_request_guest_reaches_endpoint(api_client, guest):
    # Guests may create service requests. Minimal payload → 400 (missing required fields), not 403.
    auth(api_client, guest)
    response = api_client.post(BASE_URL, {'request_type': 'cleaning'}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


# ── GET /api/v1/services/requests/ ───────────────────────────────────

@pytest.mark.django_db
def test_list_employee_sees_own_requests_only(api_client, employee, other_employee, floor, company, other_company):
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='repair', urgency='low',
    )
    ServiceRequest.objects.create(
        created_by=other_employee, company=other_company, request_type='cleaning', urgency='low',
    )
    auth(api_client, employee)
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_200_OK
    results = response.json()['results']
    assert len(results) == 1
    assert results[0]['created_by']['id'] == employee.pk


@pytest.mark.django_db
def test_list_superadmin_sees_all(api_client, superadmin, employee, other_employee, floor, company, other_company):
    before_count = ServiceRequest.objects.count()
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='repair', urgency='low',
    )
    ServiceRequest.objects.create(
        created_by=other_employee, company=other_company, request_type='cleaning', urgency='low',
    )
    auth(api_client, superadmin)
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_200_OK
    # Superadmin sees everything, including any pre-existing test rows
    assert response.json()['count'] >= before_count + 2


@pytest.mark.django_db
def test_list_unauthenticated_returns_401(api_client):
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_list_guest_sees_own_requests(api_client, guest):
    # Guests can list service requests — they see only their own.
    auth(api_client, guest)
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_list_filter_by_request_type(api_client, employee, company):
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='repair', urgency='low',
    )
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='cleaning', urgency='low',
    )
    auth(api_client, employee)
    response = api_client.get(BASE_URL + '?request_type=repair')
    assert response.status_code == status.HTTP_200_OK
    results = response.json()['results']
    assert all(r['request_type'] == 'repair' for r in results)


@pytest.mark.django_db
def test_list_filter_by_type_alias(api_client, employee, company):
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='repair', urgency='low',
    )
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='cleaning', urgency='low',
    )
    auth(api_client, employee)
    response = api_client.get(BASE_URL + '?type=cleaning')
    assert response.status_code == status.HTTP_200_OK
    results = response.json()['results']
    assert len(results) == 1
    assert results[0]['request_type'] == 'cleaning'


@pytest.mark.django_db
def test_list_filter_by_status(api_client, employee, company, floor):
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='repair',
        urgency='low', status='new',
    )
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='cleaning',
        urgency='low', status='completed',
    )
    auth(api_client, employee)
    response = api_client.get(BASE_URL + '?status=new')
    results = response.json()['results']
    assert all(r['status'] == 'new' for r in results)


@pytest.mark.django_db
def test_list_filter_by_floor(api_client, employee, company, floor):
    other_floor = Floor.objects.create(number=2, name='Second Floor')
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='repair',
        urgency='low', floor=floor,
    )
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='cleaning',
        urgency='low', floor=other_floor,
    )
    auth(api_client, employee)
    response = api_client.get(BASE_URL + f'?floor={floor.pk}')
    results = response.json()['results']
    assert len(results) == 1
    assert results[0]['floor'] == floor.pk


@pytest.mark.django_db
def test_list_filter_by_urgency(api_client, employee, company):
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='repair', urgency='high',
    )
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='cleaning', urgency='low',
    )
    auth(api_client, employee)
    response = api_client.get(BASE_URL + '?urgency=high')
    assert response.status_code == status.HTTP_200_OK
    results = response.json()['results']
    assert len(results) == 1
    assert results[0]['urgency'] == 'high'


@pytest.mark.django_db
def test_list_response_includes_photo_url(api_client, employee, company, floor):
    # Photo field should be present in GET response (even if null)
    ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='cleaning', urgency='low',
    )
    auth(api_client, employee)
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_200_OK
    result = response.json()['results'][0]
    assert 'photo' in result


@pytest.mark.django_db
def test_list_pagination(api_client, employee, company):
    for i in range(5):
        ServiceRequest.objects.create(
            created_by=employee, company=company,
            request_type='cleaning', urgency='low',
        )
    auth(api_client, employee)
    response = api_client.get(BASE_URL + '?page_size=3')
    data = response.json()
    assert 'count' in data
    assert 'results' in data
    assert len(data['results']) == 3


# ── POST /api/v1/services/requests/quick-cleaning/ ───────────────────

@pytest.mark.django_db
def test_quick_cleaning_with_floor_id(api_client, employee, floor):
    auth(api_client, employee)
    response = api_client.post(QUICK_CLEANING_URL, {'floor': floor.pk}, format='json')
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data['request_type'] == 'cleaning'
    assert data['floor'] == floor.pk
    assert data['created_by']['id'] == employee.pk


@pytest.mark.django_db
def test_quick_cleaning_uses_latest_booking_floor(api_client, employee, floor, company):
    resource = Resource.objects.create(
        name='Desk A', resource_type='desk', floor=floor.number,
        available_days=[0, 1, 2, 3, 4],
    )
    now = timezone.now()
    Booking.objects.create(
        resource=resource,
        user=employee,
        company=company,
        start_time=now - timezone.timedelta(hours=2),
        end_time=now - timezone.timedelta(hours=1),
        status='confirmed',
    )
    auth(api_client, employee)
    response = api_client.post(QUICK_CLEANING_URL, {}, format='json')
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data['floor'] == floor.pk


@pytest.mark.django_db
def test_quick_cleaning_no_floor_no_booking_returns_400(api_client, employee):
    auth(api_client, employee)
    response = api_client.post(QUICK_CLEANING_URL, {}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_quick_cleaning_invalid_floor_returns_400(api_client, employee):
    auth(api_client, employee)
    response = api_client.post(QUICK_CLEANING_URL, {'floor': 99999}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_quick_cleaning_unauthenticated(api_client, floor):
    response = api_client.post(QUICK_CLEANING_URL, {'floor': floor.pk}, format='json')
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_quick_cleaning_guest_allowed(api_client, guest, floor):
    # Guests may submit quick cleaning requests.
    auth(api_client, guest)
    response = api_client.post(QUICK_CLEANING_URL, {'floor': floor.pk}, format='json')
    assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
def test_legacy_cleaning_endpoint_works(api_client, employee, floor):
    auth(api_client, employee)
    response = api_client.post(LEGACY_CLEANING_URL, {'floor': floor.pk}, format='json')
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data['request_type'] == 'cleaning'
    assert data['floor'] == floor.pk


# ── PATCH /api/v1/services/requests/{id}/status/ ─────────────────────

@pytest.fixture
def status_manager(db):
    """Service manager fixture for status-change tests (building-wide, no company)."""
    return User.objects.create_user(
        email='status-manager@test.com',
        password='pass',
        first_name='Status',
        last_name='Manager',
        role='service_manager',
        is_email_verified=True,
    )


@pytest.mark.django_db
def test_status_update_new_to_accepted(api_client, status_manager, service_request):
    auth(api_client, status_manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    assert response.json()['status'] == 'accepted'


@pytest.mark.django_db
def test_status_update_full_lifecycle(api_client, status_manager, service_request):
    auth(api_client, status_manager)
    base = f'{BASE_URL}{service_request.pk}/status/'
    for new_status in ('accepted', 'in_progress', 'completed'):
        resp = api_client.patch(base, {'status': new_status}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['status'] == new_status


@pytest.mark.django_db
def test_status_update_invalid_transition(api_client, status_manager, service_request):
    # new → in_progress is not valid (must go new → accepted first)
    auth(api_client, status_manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'in_progress'}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_status_update_without_status_returns_400(api_client, status_manager, service_request):
    auth(api_client, status_manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_status_update_sends_notification(api_client, status_manager, service_request):
    auth(api_client, status_manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    from apps.notifications.models import Notification
    assert Notification.objects.filter(
        user=service_request.created_by,
        notification_type='service_request_update',
    ).exists()


@pytest.mark.django_db
def test_status_update_sets_completed_at(api_client, status_manager, service_request):
    # advance from new → accepted → in_progress → completed
    auth(api_client, status_manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    for s in ('accepted', 'in_progress', 'completed'):
        api_client.patch(url, {'status': s}, format='json')
    service_request.refresh_from_db()
    assert service_request.completed_at is not None


@pytest.mark.django_db
def test_status_update_employee_forbidden(api_client, employee, service_request):
    auth(api_client, employee)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_status_update_company_admin_forbidden(api_client, admin, service_request):
    # company_admin can manage assignments but is intentionally blocked from
    # advancing status — ownership of the workflow stays with superadmin /
    # service_manager (see IsServiceRequestStatusManager).
    auth(api_client, admin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_status_update_unauthenticated(api_client, service_request):
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_status_update_superadmin_can_update(api_client, superadmin, service_request):
    auth(api_client, superadmin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK


# ── PATCH /api/v1/services/requests/{id}/update-status/ (legacy) ─────

@pytest.mark.django_db
def test_legacy_status_update_new_to_accepted(api_client, status_manager, service_request):
    auth(api_client, status_manager)
    url = f'{BASE_URL}{service_request.pk}/update-status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data['status'] == 'accepted'
    assert isinstance(data['created_by'], dict)
    assert isinstance(data['assigned_to'], dict)


@pytest.mark.django_db
def test_legacy_status_update_invalid_transition(api_client, status_manager, service_request):
    auth(api_client, status_manager)
    url = f'{BASE_URL}{service_request.pk}/update-status/'
    response = api_client.patch(url, {'status': 'in_progress'}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_legacy_status_update_employee_forbidden(api_client, employee, service_request):
    auth(api_client, employee)
    url = f'{BASE_URL}{service_request.pk}/update-status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_legacy_status_update_company_admin_forbidden(api_client, admin, service_request):
    auth(api_client, admin)
    url = f'{BASE_URL}{service_request.pk}/update-status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_legacy_status_update_unauthenticated(api_client, service_request):
    url = f'{BASE_URL}{service_request.pk}/update-status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_legacy_status_update_sends_notification(api_client, status_manager, service_request):
    auth(api_client, status_manager)
    url = f'{BASE_URL}{service_request.pk}/update-status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    from apps.notifications.models import Notification
    assert Notification.objects.filter(
        user=service_request.created_by,
        notification_type='service_request_update',
    ).exists()


# ── POST /api/v1/services/requests/{id}/rate/ ────────────────────────

@pytest.mark.django_db
def test_rate_completed_request(api_client, employee, completed_request):
    auth(api_client, employee)
    url = f'{BASE_URL}{completed_request.pk}/rate/'
    response = api_client.post(url, {'rating': 4}, format='json')
    assert response.status_code == status.HTTP_200_OK
    completed_request.refresh_from_db()
    assert completed_request.rating == 4


@pytest.mark.django_db
def test_rate_non_completed_request_returns_400(api_client, employee, service_request):
    # service_request.status == 'new'
    auth(api_client, employee)
    url = f'{BASE_URL}{service_request.pk}/rate/'
    response = api_client.post(url, {'rating': 5}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_rate_already_rated_returns_400(api_client, employee, completed_request):
    completed_request.rating = 3
    completed_request.save(update_fields=['rating'])
    auth(api_client, employee)
    url = f'{BASE_URL}{completed_request.pk}/rate/'
    response = api_client.post(url, {'rating': 5}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_rate_invalid_value_returns_400(api_client, employee, completed_request):
    auth(api_client, employee)
    url = f'{BASE_URL}{completed_request.pk}/rate/'
    response = api_client.post(url, {'rating': 6}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_rate_by_non_creator_returns_403(api_client, admin, completed_request):
    auth(api_client, admin)
    url = f'{BASE_URL}{completed_request.pk}/rate/'
    response = api_client.post(url, {'rating': 5}, format='json')
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_rate_unauthenticated_returns_401(api_client, completed_request):
    url = f'{BASE_URL}{completed_request.pk}/rate/'
    response = api_client.post(url, {'rating': 5}, format='json')
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_rate_returns_full_request_object(api_client, employee, completed_request):
    auth(api_client, employee)
    url = f'{BASE_URL}{completed_request.pk}/rate/'
    response = api_client.post(url, {'rating': 5}, format='json')
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data['rating'] == 5
    assert 'photo' in data
    assert 'status' in data


# ── DEV-222: service_manager role ────────────────────────────────────

@pytest.fixture
def service_manager(db):
    """Building-wide service manager — no company assignment."""
    return User.objects.create_user(
        email='svc-manager@test.com',
        password='pass',
        first_name='Service',
        last_name='Manager',
        role='service_manager',
        is_email_verified=True,
    )


@pytest.mark.django_db
def test_service_manager_sees_requests_across_all_companies(
    api_client, service_manager, employee, other_employee, company, other_company,
):
    own = ServiceRequest.objects.create(
        created_by=employee, company=company, request_type='repair', urgency='low',
    )
    foreign = ServiceRequest.objects.create(
        created_by=other_employee, company=other_company, request_type='cleaning', urgency='low',
    )
    auth(api_client, service_manager)
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_200_OK
    ids = {r['id'] for r in response.json()['results']}
    assert own.pk in ids
    assert foreign.pk in ids


@pytest.mark.django_db
def test_service_manager_can_update_status_for_any_company(
    api_client, service_manager, service_request,
):
    auth(api_client, service_manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    assert response.json()['status'] == 'accepted'


@pytest.mark.django_db
def test_service_manager_can_use_legacy_status_endpoint(
    api_client, service_manager, service_request,
):
    auth(api_client, service_manager)
    url = f'{BASE_URL}{service_request.pk}/update-status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_service_manager_can_assign_executor(
    api_client, service_manager, employee, service_request,
):
    auth(api_client, service_manager)
    url = f'{BASE_URL}{service_request.pk}/assign/'
    response = api_client.patch(url, {'assigned_to': employee.pk}, format='json')
    assert response.status_code == status.HTTP_200_OK
    service_request.refresh_from_db()
    assert service_request.assigned_to_id == employee.pk
    assert response.json()['assigned_to']['id'] == employee.pk


@pytest.mark.django_db
def test_company_admin_cannot_assign_executor(api_client, admin, employee, service_request):
    # company_admin is intentionally excluded from all service-request management
    # actions (status + assign). Only superadmin / service_manager may assign.
    auth(api_client, admin)
    url = f'{BASE_URL}{service_request.pk}/assign/'
    response = api_client.patch(url, {'assigned_to': employee.pk}, format='json')
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_assign_null_clears_executor(api_client, service_manager, employee, service_request):
    service_request.assigned_to = employee
    service_request.save(update_fields=['assigned_to'])
    auth(api_client, service_manager)
    url = f'{BASE_URL}{service_request.pk}/assign/'
    response = api_client.patch(url, {'assigned_to': None}, format='json')
    assert response.status_code == status.HTTP_200_OK
    service_request.refresh_from_db()
    assert service_request.assigned_to_id is None


@pytest.mark.django_db
def test_assign_rejects_guest_assignee(api_client, service_manager, guest, service_request):
    auth(api_client, service_manager)
    url = f'{BASE_URL}{service_request.pk}/assign/'
    response = api_client.patch(url, {'assigned_to': guest.pk}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_assign_rejects_inactive_assignee(api_client, service_manager, employee, service_request):
    employee.is_active = False
    employee.save(update_fields=['is_active'])
    auth(api_client, service_manager)
    url = f'{BASE_URL}{service_request.pk}/assign/'
    response = api_client.patch(url, {'assigned_to': employee.pk}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_assign_employee_forbidden(api_client, employee, service_request):
    auth(api_client, employee)
    url = f'{BASE_URL}{service_request.pk}/assign/'
    response = api_client.patch(url, {'assigned_to': employee.pk}, format='json')
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_assign_unauthenticated_returns_401(api_client, service_request):
    url = f'{BASE_URL}{service_request.pk}/assign/'
    response = api_client.patch(url, {'assigned_to': 1}, format='json')
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_assign_guest_forbidden(api_client, guest, service_request):
    auth(api_client, guest)
    url = f'{BASE_URL}{service_request.pk}/assign/'
    response = api_client.patch(url, {'assigned_to': guest.pk}, format='json')
    assert response.status_code == status.HTTP_403_FORBIDDEN


# ── Superadmin enriched response ──────────────────────────────────────

@pytest.mark.django_db
def test_superadmin_list_returns_nested_company(api_client, superadmin, service_request):
    """Superadmin list: company is a nested object, not a bare integer."""
    auth(api_client, superadmin)
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_200_OK
    result = next(r for r in response.json()['results'] if r['id'] == service_request.pk)
    company_field = result['company']
    assert isinstance(company_field, dict), 'company should be a nested object for superadmin'
    assert 'id' in company_field
    assert 'name' in company_field
    assert 'logo' in company_field
    assert company_field['id'] == service_request.company_id


@pytest.mark.django_db
def test_superadmin_list_returns_nested_created_by(api_client, superadmin, service_request):
    """Superadmin list: created_by is a nested user object."""
    auth(api_client, superadmin)
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_200_OK
    result = next(r for r in response.json()['results'] if r['id'] == service_request.pk)
    created_by_field = result['created_by']
    assert isinstance(created_by_field, dict), 'created_by should be a nested object for superadmin'
    for key in ('id', 'full_name', 'avatar', 'email', 'role'):
        assert key in created_by_field, f'expected key {key!r} in created_by'
    assert created_by_field['id'] == service_request.created_by_id


@pytest.mark.django_db
def test_superadmin_list_returns_nested_assigned_to_null(api_client, superadmin, service_request):
    """Superadmin list: assigned_to is null when unassigned."""
    assert service_request.assigned_to_id is None
    auth(api_client, superadmin)
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_200_OK
    result = next(r for r in response.json()['results'] if r['id'] == service_request.pk)
    assert result['assigned_to'] is None


@pytest.mark.django_db
def test_superadmin_list_returns_nested_assigned_to_object(
    api_client, superadmin, service_manager, service_request
):
    """Superadmin list: assigned_to is a nested user object when set."""
    service_request.assigned_to = service_manager
    service_request.save(update_fields=['assigned_to'])
    auth(api_client, superadmin)
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_200_OK
    result = next(r for r in response.json()['results'] if r['id'] == service_request.pk)
    assigned_field = result['assigned_to']
    assert isinstance(assigned_field, dict), 'assigned_to should be a nested object when set'
    assert assigned_field['id'] == service_manager.pk
    assert assigned_field['role'] == 'service_manager'


@pytest.mark.django_db
def test_superadmin_retrieve_returns_nested_objects(api_client, superadmin, service_request):
    """Superadmin detail: all three enriched fields are nested objects."""
    auth(api_client, superadmin)
    response = api_client.get(f'{BASE_URL}{service_request.pk}/')
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data['company'], dict)
    assert isinstance(data['created_by'], dict)
    assert data['assigned_to'] is None


@pytest.mark.django_db
def test_non_superadmin_list_returns_nested_company(api_client, admin, service_request):
    """company_admin receives nested objects for company and created_by, same as superadmin."""
    auth(api_client, admin)
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_200_OK
    results = response.json()['results']
    assert len(results) > 0
    result = results[0]
    assert isinstance(result['company'], dict), 'company should be a nested object for all roles'
    assert isinstance(result['created_by'], dict), 'created_by should be a nested object for all roles'


@pytest.mark.django_db
def test_employee_list_returns_nested_created_by(api_client, employee, service_request):
    """Regular employee also receives nested objects in the list response."""
    auth(api_client, employee)
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_200_OK
    results = response.json()['results']
    assert len(results) > 0
    result = results[0]
    assert isinstance(result['created_by'], dict)
    assert 'id' in result['created_by']


@pytest.mark.django_db
def test_superadmin_status_update_response_has_nested_fields(
    api_client, superadmin, service_request
):
    """Status update by superadmin returns the enriched response shape."""
    auth(api_client, superadmin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data['company'], dict)
    assert isinstance(data['created_by'], dict)


@pytest.mark.django_db
def test_superadmin_assign_response_has_nested_fields(
    api_client, superadmin, service_manager, service_request
):
    """Assign action by superadmin returns the enriched response shape."""
    auth(api_client, superadmin)
    url = f'{BASE_URL}{service_request.pk}/assign/'
    response = api_client.patch(url, {'assigned_to': service_manager.pk}, format='json')
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data['company'], dict)
    assert isinstance(data['assigned_to'], dict)
    assert data['assigned_to']['id'] == service_manager.pk


# ── Status update: assigned_to behaviour ─────────────────────────────


@pytest.mark.django_db
def test_status_update_superadmin_can_set_assigned_to(
    api_client, superadmin, service_manager, service_request
):
    """Superadmin may include assigned_to in the status PATCH body."""
    auth(api_client, superadmin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(
        url, {'status': 'accepted', 'assigned_to': service_manager.pk}, format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    service_request.refresh_from_db()
    assert service_request.assigned_to_id == service_manager.pk
    # Response is enriched for superadmin
    data = response.json()
    assert isinstance(data['assigned_to'], dict)
    assert data['assigned_to']['id'] == service_manager.pk


@pytest.mark.django_db
def test_status_update_superadmin_omit_assigned_to_leaves_existing(
    api_client, superadmin, service_manager, service_request
):
    """When superadmin omits assigned_to, the existing assignment is not changed."""
    service_request.assigned_to = service_manager
    service_request.save(update_fields=['assigned_to'])

    auth(api_client, superadmin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    # No assigned_to in body
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    service_request.refresh_from_db()
    # assigned_to must remain unchanged
    assert service_request.assigned_to_id == service_manager.pk


@pytest.mark.django_db
def test_status_update_superadmin_can_clear_assigned_to_with_null(
    api_client, superadmin, service_manager, service_request
):
    """Superadmin may pass assigned_to=null to clear the assignee."""
    service_request.assigned_to = service_manager
    service_request.save(update_fields=['assigned_to'])

    auth(api_client, superadmin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(
        url, {'status': 'accepted', 'assigned_to': None}, format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    service_request.refresh_from_db()
    assert service_request.assigned_to_id is None


@pytest.mark.django_db
def test_status_update_service_manager_auto_assigns_to_self(
    api_client, status_manager, service_request
):
    """Service manager is automatically set as assignee on every status change."""
    auth(api_client, status_manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    service_request.refresh_from_db()
    assert service_request.assigned_to_id == status_manager.pk


@pytest.mark.django_db
def test_status_update_service_manager_body_assigned_to_ignored(
    api_client, status_manager, employee, service_request
):
    """Even if a service_manager sends assigned_to in the body it is overridden to self."""
    auth(api_client, status_manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    # Attempt to assign to someone else via body — should be ignored
    response = api_client.patch(
        url, {'status': 'accepted', 'assigned_to': employee.pk}, format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    service_request.refresh_from_db()
    # Must be the manager, not the employee
    assert service_request.assigned_to_id == status_manager.pk


@pytest.mark.django_db
def test_status_update_superadmin_response_assigned_to_is_nested(
    api_client, superadmin, service_manager, service_request
):
    """Status update response for superadmin contains nested assigned_to object."""
    auth(api_client, superadmin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(
        url, {'status': 'accepted', 'assigned_to': service_manager.pk}, format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data['assigned_to'], dict)
    for key in ('id', 'full_name', 'email', 'role'):
        assert key in data['assigned_to']


# ── Rule 1: assignee locked when in_progress ──────────────────────────

@pytest.mark.django_db
def test_superadmin_cannot_change_assignee_on_in_progress_request(
    api_client, superadmin, service_manager, employee, service_request
):
    """Rule 1: superadmin cannot change assigned_to when status is in_progress."""
    service_request.status = 'in_progress'
    service_request.assigned_to = service_manager
    service_request.save(update_fields=['status', 'assigned_to'])

    auth(api_client, superadmin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    # Attempt to reassign to a different user while in_progress
    response = api_client.patch(
        url, {'status': 'completed', 'assigned_to': employee.pk}, format='json',
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_service_manager_cannot_take_in_progress_request_owned_by_other(
    api_client, service_request
):
    """Rule 1: a second service_manager cannot take an in_progress request."""
    # First manager owns the request
    first_manager = User.objects.create_user(
        email='first-manager@test.com',
        password='pass',
        first_name='First',
        last_name='Manager',
        role='service_manager',
        is_email_verified=True,
    )
    second_manager = User.objects.create_user(
        email='second-manager@test.com',
        password='pass',
        first_name='Second',
        last_name='Manager',
        role='service_manager',
        is_email_verified=True,
    )
    service_request.status = 'in_progress'
    service_request.assigned_to = first_manager
    service_request.save(update_fields=['status', 'assigned_to'])

    client = APIClient()
    auth(client, second_manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = client.patch(url, {'status': 'completed'}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


# ── Rule 2: auto-transition when assignee is set ──────────────────────

@pytest.mark.django_db
def test_superadmin_assign_other_user_without_status_auto_sets_accepted(
    api_client, superadmin, service_manager, service_request
):
    """Rule 2: superadmin assigns someone else without a status → status auto-becomes accepted."""
    assert service_request.status == 'new'

    auth(api_client, superadmin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(
        url, {'assigned_to': service_manager.pk}, format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    service_request.refresh_from_db()
    assert service_request.status == 'accepted'
    assert service_request.assigned_to_id == service_manager.pk


@pytest.mark.django_db
def test_superadmin_assign_self_without_status_auto_sets_in_progress(
    api_client, superadmin, service_request
):
    """Rule 2 exception: superadmin assigning themselves → in_progress (direct shortcut)."""
    assert service_request.status == 'new'

    auth(api_client, superadmin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(
        url, {'assigned_to': superadmin.pk}, format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    service_request.refresh_from_db()
    assert service_request.status == 'in_progress'
    assert service_request.assigned_to_id == superadmin.pk


@pytest.mark.django_db
def test_service_manager_take_request_without_status_auto_sets_in_progress(
    api_client, service_request
):
    """Rule 2: service_manager self-assigns without explicit status → in_progress."""
    manager = User.objects.create_user(
        email='take-manager@test.com',
        password='pass',
        first_name='Take',
        last_name='Manager',
        role='service_manager',
        is_email_verified=True,
    )
    assert service_request.status == 'new'

    client = APIClient()
    auth(client, manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    # Send only assigned_to — no explicit status; self-assign → in_progress
    response = client.patch(url, {'assigned_to': manager.pk}, format='json')
    assert response.status_code == status.HTTP_200_OK
    service_request.refresh_from_db()
    assert service_request.status == 'in_progress'
    assert service_request.assigned_to_id == manager.pk


@pytest.mark.django_db
def test_rule2_does_not_fire_when_status_explicitly_provided(
    api_client, service_manager, service_request
):
    """Rule 2 must not override an explicitly supplied status."""
    auth(api_client, service_manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    # Explicit status=accepted; service_manager auto-assigns — Rule 2 must NOT fire.
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    service_request.refresh_from_db()
    assert service_request.status == 'accepted'


@pytest.mark.django_db
def test_rule2_does_not_fire_when_assigned_to_null(
    api_client, superadmin, service_manager, service_request
):
    """Clearing assigned_to (null) must not trigger Rule 2."""
    service_request.status = 'new'
    service_request.assigned_to = service_manager
    service_request.save(update_fields=['status', 'assigned_to'])

    auth(api_client, superadmin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(
        url, {'assigned_to': None}, format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    service_request.refresh_from_db()
    # Status must remain 'new' — not auto-transitioned
    assert service_request.status == 'new'
    assert service_request.assigned_to_id is None


# ── Completion info: completed_at on in_progress → completed ──────────


@pytest.fixture
def in_progress_request(db, employee, company, floor, status_manager):
    """Service request already in in_progress with an assignee."""
    sr = ServiceRequest.objects.create(
        created_by=employee,
        company=company,
        request_type='repair',
        urgency='medium',
        floor=floor,
        location='Room 303',
        description='Broken pipe',
        status='in_progress',
        assigned_to=status_manager,
    )
    return sr


@pytest.mark.django_db
def test_completed_at_auto_stamped_when_completing(api_client, status_manager, in_progress_request):
    """completed_at is auto-set by the server when transitioning to completed."""
    auth(api_client, status_manager)
    url = f'{BASE_URL}{in_progress_request.pk}/status/'
    response = api_client.patch(url, {'status': 'completed'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    in_progress_request.refresh_from_db()
    assert in_progress_request.completed_at is not None


@pytest.mark.django_db
def test_completed_at_not_overwritten_if_already_set(api_client, status_manager, in_progress_request):
    """If completed_at is already set it must not be overwritten by a second completion."""
    from django.utils import timezone as tz
    original_ts = tz.now() - tz.timedelta(hours=1)
    in_progress_request.completed_at = original_ts
    in_progress_request.save(update_fields=['completed_at'])

    auth(api_client, status_manager)
    url = f'{BASE_URL}{in_progress_request.pk}/status/'
    response = api_client.patch(url, {'status': 'completed'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    in_progress_request.refresh_from_db()
    # Timestamp must be preserved, not overwritten
    assert abs((in_progress_request.completed_at - original_ts).total_seconds()) < 1


@pytest.mark.django_db
def test_client_can_supply_completed_at_when_in_progress(
    api_client, status_manager, in_progress_request,
):
    """Client may explicitly send completed_at; value is honoured."""
    from django.utils import timezone as tz
    custom_ts = tz.now().replace(microsecond=0)
    auth(api_client, status_manager)
    url = f'{BASE_URL}{in_progress_request.pk}/status/'
    response = api_client.patch(
        url,
        {'status': 'completed', 'completed_at': custom_ts.isoformat()},
        format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    in_progress_request.refresh_from_db()
    assert abs((in_progress_request.completed_at - custom_ts).total_seconds()) < 2


@pytest.mark.django_db
def test_completion_fields_rejected_when_not_in_progress(
    api_client, status_manager, service_request,
):
    """Sending completed_at when status != in_progress raises 400."""
    from django.utils import timezone as tz
    # service_request.status == 'new'
    assert service_request.status == 'new'
    auth(api_client, status_manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(
        url,
        {'status': 'accepted', 'completed_at': tz.now().isoformat()},
        format='json',
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_completion_fields_rejected_when_accepted_status(
    api_client, status_manager, service_request,
):
    """Sending completed_at when request is in 'accepted' state raises 400."""
    from django.utils import timezone as tz
    service_request.status = 'accepted'
    service_request.save(update_fields=['status'])

    auth(api_client, status_manager)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(
        url,
        {'status': 'in_progress', 'completed_at': tz.now().isoformat()},
        format='json',
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_superadmin_can_complete_in_progress_with_completion_info(
    api_client, superadmin, in_progress_request,
):
    """Superadmin completing an in_progress request also gets completed_at auto-stamped."""
    auth(api_client, superadmin)
    url = f'{BASE_URL}{in_progress_request.pk}/status/'
    response = api_client.patch(url, {'status': 'completed'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    in_progress_request.refresh_from_db()
    assert in_progress_request.completed_at is not None
