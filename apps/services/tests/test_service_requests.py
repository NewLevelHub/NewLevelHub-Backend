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
    assert data['created_by'] == employee.pk
    assert data['company'] == employee.company_id
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
    assert response.json()['company'] == employee.company_id


@pytest.mark.django_db
def test_create_service_request_missing_required_fields(api_client, employee):
    auth(api_client, employee)
    payload = {'request_type': 'cleaning'}
    response = api_client.post(BASE_URL, payload, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()['detail']
    assert 'floor' in data
    assert 'location' in data
    assert 'description' in data
    assert 'urgency' in data


@pytest.mark.django_db
def test_create_service_request_unauthenticated(api_client):
    response = api_client.post(BASE_URL, {'request_type': 'cleaning'}, format='json')
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_create_service_request_guest_forbidden(api_client, guest):
    auth(api_client, guest)
    response = api_client.post(BASE_URL, {'request_type': 'cleaning'}, format='json')
    assert response.status_code == status.HTTP_403_FORBIDDEN


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
    assert results[0]['created_by'] == employee.pk


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
def test_list_guest_forbidden(api_client, guest):
    auth(api_client, guest)
    response = api_client.get(BASE_URL)
    assert response.status_code == status.HTTP_403_FORBIDDEN


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
    assert data['created_by'] == employee.pk


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
def test_quick_cleaning_guest_forbidden(api_client, guest, floor):
    auth(api_client, guest)
    response = api_client.post(QUICK_CLEANING_URL, {'floor': floor.pk}, format='json')
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_legacy_cleaning_endpoint_works(api_client, employee, floor):
    auth(api_client, employee)
    response = api_client.post(LEGACY_CLEANING_URL, {'floor': floor.pk}, format='json')
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data['request_type'] == 'cleaning'
    assert data['floor'] == floor.pk


# ── PATCH /api/v1/services/requests/{id}/status/ ─────────────────────

@pytest.mark.django_db
def test_status_update_new_to_accepted(api_client, admin, service_request):
    auth(api_client, admin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    assert response.json()['status'] == 'accepted'


@pytest.mark.django_db
def test_status_update_full_lifecycle(api_client, admin, service_request):
    auth(api_client, admin)
    base = f'{BASE_URL}{service_request.pk}/status/'
    for new_status in ('accepted', 'in_progress', 'completed'):
        resp = api_client.patch(base, {'status': new_status}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['status'] == new_status


@pytest.mark.django_db
def test_status_update_invalid_transition(api_client, admin, service_request):
    # new → in_progress is not valid (must go new → accepted first)
    auth(api_client, admin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'in_progress'}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_status_update_without_status_returns_400(api_client, admin, service_request):
    auth(api_client, admin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {}, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_status_update_sends_notification(api_client, admin, service_request):
    auth(api_client, admin)
    url = f'{BASE_URL}{service_request.pk}/status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    from apps.notifications.models import Notification
    assert Notification.objects.filter(
        user=service_request.created_by,
        notification_type='service_request_update',
    ).exists()


@pytest.mark.django_db
def test_status_update_sets_completed_at(api_client, admin, service_request):
    # advance from new → accepted → in_progress → completed
    auth(api_client, admin)
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


@pytest.mark.django_db
def test_status_update_admin_from_other_company_gets_404(
    api_client, other_company, employee, floor
):
    outsider_admin = User.objects.create_user(
        email='outsider-admin@test.com',
        password='pass',
        first_name='Outside',
        last_name='Admin',
        role='company_admin',
        company=other_company,
        is_email_verified=True,
    )
    owned_request = ServiceRequest.objects.create(
        created_by=employee,
        company=employee.company,
        request_type='repair',
        urgency='medium',
        floor=floor,
    )
    auth(api_client, outsider_admin)
    url = f'{BASE_URL}{owned_request.pk}/status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_404_NOT_FOUND


# ── PATCH /api/v1/services/requests/{id}/update-status/ (legacy) ─────

@pytest.mark.django_db
def test_legacy_status_update_new_to_accepted(api_client, admin, service_request):
    auth(api_client, admin)
    url = f'{BASE_URL}{service_request.pk}/update-status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data['status'] == 'accepted'
    assert 'created_by_name' in data
    assert 'assigned_to_name' in data


@pytest.mark.django_db
def test_legacy_status_update_invalid_transition(api_client, admin, service_request):
    auth(api_client, admin)
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
def test_legacy_status_update_unauthenticated(api_client, service_request):
    url = f'{BASE_URL}{service_request.pk}/update-status/'
    response = api_client.patch(url, {'status': 'accepted'}, format='json')
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_legacy_status_update_sends_notification(api_client, admin, service_request):
    auth(api_client, admin)
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
