"""Integration tests for Floor Plans CRUD (services app)."""

import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.services.models import Floor, MapPoint
from apps.users.models import User


FLOORS_URL = '/api/v1/services/floors/'


def floors_detail_url(floor_id):
    return f'{FLOORS_URL}{floor_id}/'


def floors_map_url(floor_id):
    return f'{FLOORS_URL}{floor_id}/map/'


def make_image_file(name='plan.jpg'):
    """Create a minimal in-memory JPEG suitable for ImageField upload."""
    img = Image.new('RGB', (10, 10), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type='image/jpeg')


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Floor Co', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Co', plan='basic')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@floors.test',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='cadmin@floors.test',
        password='pass',
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp@floors.test',
        password='pass',
        first_name='Emp',
        last_name='Loyee',
        role='employee',
        company=company,
    )


@pytest.fixture
def guest(db):
    return User.objects.create_user(
        email='guest@floors.test',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
    )


@pytest.fixture
def floor(db, company):
    return Floor.objects.create(number=1, name='Ground Floor', company=company)


@pytest.fixture
def floor_with_points(db, company):
    f = Floor.objects.create(number=2, name='Second Floor', company=company)
    MapPoint.objects.create(floor=f, point_type='desk', x=10.0, y=20.0, company=company)
    MapPoint.objects.create(floor=f, point_type='kitchen', x=50.0, y=60.0, company=company)
    return f


# ── Helpers ───────────────────────────────────────────────────────────────────

def auth(client, user):
    client.force_authenticate(user=user)


# ── Unauthenticated / guest access ────────────────────────────────────────────

@pytest.mark.django_db
def test_unauthenticated_list_returns_401(api_client, floor):
    response = api_client.get(FLOORS_URL)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_guest_list_returns_200(api_client, guest, floor):
    # Guests can see global floors (company=null) for the building map.
    auth(api_client, guest)
    response = api_client.get(FLOORS_URL)
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_guest_post_returns_403(api_client, guest):
    # Guests cannot create floors — only superadmin can.
    auth(api_client, guest)
    response = api_client.post(FLOORS_URL, {'number': 5, 'name': 'Test'})
    assert response.status_code == status.HTTP_403_FORBIDDEN


# ── Superadmin CREATE ─────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_superadmin_can_create_floor_without_image(api_client, superadmin):
    auth(api_client, superadmin)
    response = api_client.post(FLOORS_URL, {'number': 10, 'name': 'Rooftop'}, format='json')
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data['number'] == 10
    assert data['name'] == 'Rooftop'
    assert data['plan_image_url'] is None


@pytest.mark.django_db
def test_superadmin_can_create_floor_with_image(api_client, superadmin):
    auth(api_client, superadmin)
    payload = {
        'number': 11,
        'name': 'Penthouse',
        'plan_image': make_image_file(),
    }
    response = api_client.post(FLOORS_URL, payload, format='multipart')
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data['plan_image_url'] is not None
    assert 'floors/plans/' in data['plan_image_url']


@pytest.mark.django_db
def test_employee_cannot_create_floor(api_client, employee):
    auth(api_client, employee)
    response = api_client.post(FLOORS_URL, {'number': 99, 'name': 'Nope'}, format='json')
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_company_admin_cannot_create_floor(api_client, company_admin):
    auth(api_client, company_admin)
    response = api_client.post(FLOORS_URL, {'number': 88, 'name': 'Nope'}, format='json')
    assert response.status_code == status.HTTP_403_FORBIDDEN


# ── LIST view ─────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_employee_can_list_floors(api_client, employee, floor):
    auth(api_client, employee)
    response = api_client.get(FLOORS_URL)
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_list_response_includes_plan_image_url(api_client, superadmin):
    auth(api_client, superadmin)
    # Create a floor with an image first.
    payload = {'number': 20, 'name': 'Floor 20', 'plan_image': make_image_file()}
    api_client.post(FLOORS_URL, payload, format='multipart')

    response = api_client.get(FLOORS_URL)
    assert response.status_code == status.HTTP_200_OK
    results = response.json()['results']
    floor_data = next(f for f in results if f['number'] == 20)
    assert 'plan_image_url' in floor_data
    assert floor_data['plan_image_url'] is not None


@pytest.mark.django_db
def test_list_does_not_include_map_points(api_client, employee, floor_with_points):
    auth(api_client, employee)
    response = api_client.get(FLOORS_URL)
    assert response.status_code == status.HTTP_200_OK
    results = response.json()['results']
    for item in results:
        assert 'map_points' not in item


@pytest.mark.django_db
def test_superadmin_list_sees_all_companies(api_client, superadmin, company, other_company):
    Floor.objects.create(number=30, name='A', company=company)
    Floor.objects.create(number=31, name='B', company=other_company)
    auth(api_client, superadmin)
    response = api_client.get(FLOORS_URL)
    assert response.status_code == status.HTTP_200_OK
    numbers = [f['number'] for f in response.json()['results']]
    assert 30 in numbers
    assert 31 in numbers


@pytest.mark.django_db
def test_employee_list_sees_only_own_company(api_client, employee, company, other_company):
    Floor.objects.create(number=40, name='Mine', company=company)
    Floor.objects.create(number=41, name='Theirs', company=other_company)
    auth(api_client, employee)
    response = api_client.get(FLOORS_URL)
    assert response.status_code == status.HTTP_200_OK
    numbers = [f['number'] for f in response.json()['results']]
    assert 40 in numbers
    assert 41 not in numbers


# ── RETRIEVE / detail view ────────────────────────────────────────────────────

@pytest.mark.django_db
def test_retrieve_floor_includes_map_points(api_client, employee, floor_with_points):
    auth(api_client, employee)
    response = api_client.get(floors_detail_url(floor_with_points.id))
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert 'map_points' in data
    assert len(data['map_points']) == 2


@pytest.mark.django_db
def test_retrieve_floor_plan_image_url_is_none_when_no_image(api_client, employee, floor):
    auth(api_client, employee)
    response = api_client.get(floors_detail_url(floor.id))
    assert response.status_code == status.HTTP_200_OK
    assert response.json()['plan_image_url'] is None


@pytest.mark.django_db
def test_floor_map_returns_blocked_status_for_active_resource_block(api_client, superadmin, employee, company):
    floor = Floor.objects.create(number=4, name='Fourth Floor', company=company)
    resource = Resource.objects.create(
        name='Desk A-11',
        resource_type='desk',
        floor=floor.number,
    )
    point = MapPoint.objects.create(
        floor=floor,
        point_type='desk',
        x=15.0,
        y=25.0,
        label='Desk A-11',
        resource=resource,
        company=company,
    )

    check_time = timezone.now().replace(microsecond=0)
    block_start = check_time - timedelta(minutes=30)
    block_end = check_time + timedelta(minutes=30)

    auth(api_client, superadmin)
    block_response = api_client.post(
        f'/api/v1/bookings/resources/{resource.id}/block/',
        {
            'start_time': block_start.isoformat(),
            'end_time': block_end.isoformat(),
            'reason': 'Maintenance window',
        },
        format='json',
    )
    assert block_response.status_code == status.HTTP_201_CREATED

    auth(api_client, employee)
    map_response = api_client.get(
        floors_map_url(floor.id),
        {'datetime': check_time.isoformat()},
    )
    assert map_response.status_code == status.HTTP_200_OK
    points = map_response.json()['points']
    point_payload = next(item for item in points if item['id'] == point.id)
    assert point_payload['resource_status'] == 'blocked'


@pytest.mark.django_db
def test_floor_map_blocked_has_priority_over_active_booking(api_client, superadmin, employee, company):
    floor = Floor.objects.create(number=5, name='Fifth Floor', company=company)
    resource = Resource.objects.create(name='Desk B-01', resource_type='desk', floor=floor.number)
    point = MapPoint.objects.create(
        floor=floor,
        point_type='desk',
        x=11.0,
        y=22.0,
        label='Desk B-01',
        resource=resource,
        company=company,
    )
    check_time = timezone.now().replace(microsecond=0)

    Booking.objects.create(
        resource=resource,
        user=employee,
        company=company,
        start_time=check_time - timedelta(minutes=10),
        end_time=check_time + timedelta(minutes=40),
        status='confirmed',
    )

    auth(api_client, superadmin)
    block_response = api_client.post(
        f'/api/v1/bookings/resources/{resource.id}/block/',
        {
            'start_time': (check_time - timedelta(minutes=5)).isoformat(),
            'end_time': (check_time + timedelta(minutes=20)).isoformat(),
            'reason': 'Emergency maintenance',
        },
        format='json',
    )
    assert block_response.status_code == status.HTTP_201_CREATED

    auth(api_client, employee)
    response = api_client.get(floors_map_url(floor.id), {'datetime': check_time.isoformat()})
    assert response.status_code == status.HTTP_200_OK
    payload = next(item for item in response.json()['points'] if item['id'] == point.id)
    assert payload['resource_status'] == 'blocked'
    assert payload['resource_status_reason'] == 'active_block'
    assert payload['next_free_at'] is not None


@pytest.mark.django_db
def test_floor_map_status_boundary_between_occupied_and_soon_available(api_client, employee, company):
    floor = Floor.objects.create(number=6, name='Sixth Floor', company=company)
    resource = Resource.objects.create(name='Desk C-01', resource_type='desk', floor=floor.number)
    point = MapPoint.objects.create(
        floor=floor,
        point_type='desk',
        x=13.0,
        y=23.0,
        label='Desk C-01',
        resource=resource,
        company=company,
    )
    check_time = timezone.now().replace(microsecond=0)

    booking = Booking.objects.create(
        resource=resource,
        user=employee,
        company=company,
        start_time=check_time - timedelta(minutes=5),
        end_time=check_time + timedelta(minutes=31),
        status='confirmed',
    )

    auth(api_client, employee)
    occupied_response = api_client.get(floors_map_url(floor.id), {'datetime': check_time.isoformat()})
    assert occupied_response.status_code == status.HTTP_200_OK
    occupied_payload = next(item for item in occupied_response.json()['points'] if item['id'] == point.id)
    assert occupied_payload['resource_status'] == 'occupied'
    assert occupied_payload['resource_status_reason'] == 'active_booking'

    booking.end_time = check_time + timedelta(minutes=30)
    booking.save(update_fields=['end_time'])

    soon_response = api_client.get(floors_map_url(floor.id), {'datetime': check_time.isoformat()})
    assert soon_response.status_code == status.HTTP_200_OK
    soon_payload = next(item for item in soon_response.json()['points'] if item['id'] == point.id)
    assert soon_payload['resource_status'] == 'soon_available'
    assert soon_payload['resource_status_reason'] == 'active_booking_ends_within_threshold'
    assert soon_payload['next_free_at'] is not None


@pytest.mark.django_db
def test_floor_map_returns_free_when_no_active_booking_or_block(api_client, employee, company):
    floor = Floor.objects.create(number=7, name='Seventh Floor', company=company)
    resource = Resource.objects.create(name='Desk D-01', resource_type='desk', floor=floor.number)
    point = MapPoint.objects.create(
        floor=floor,
        point_type='desk',
        x=14.0,
        y=24.0,
        label='Desk D-01',
        resource=resource,
        company=company,
    )
    check_time = timezone.now().replace(microsecond=0)

    auth(api_client, employee)
    response = api_client.get(floors_map_url(floor.id), {'datetime': check_time.isoformat()})
    assert response.status_code == status.HTTP_200_OK
    payload = next(item for item in response.json()['points'] if item['id'] == point.id)
    assert payload['resource_status'] == 'free'
    assert payload['resource_status_reason'] == 'no_active_booking_or_block'
    assert payload['next_free_at'] is None


@pytest.mark.django_db
def test_floor_map_returns_null_status_for_non_resource_points(api_client, employee, company):
    floor = Floor.objects.create(number=8, name='Eighth Floor', company=company)
    point = MapPoint.objects.create(
        floor=floor,
        point_type='kitchen',
        x=35.0,
        y=45.0,
        label='Kitchen Zone',
        company=company,
    )
    check_time = timezone.now().replace(microsecond=0)

    auth(api_client, employee)
    response = api_client.get(floors_map_url(floor.id), {'datetime': check_time.isoformat()})
    assert response.status_code == status.HTTP_200_OK
    payload = next(item for item in response.json()['points'] if item['id'] == point.id)
    assert payload['resource_status'] is None
    assert payload['resource_status_reason'] == 'not_a_bookable_resource'
    assert payload['next_free_at'] is None


# ── PATCH ─────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_superadmin_can_patch_floor_name(api_client, superadmin, floor):
    auth(api_client, superadmin)
    response = api_client.patch(
        floors_detail_url(floor.id),
        {'name': 'Renamed Floor'},
        format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()['name'] == 'Renamed Floor'


@pytest.mark.django_db
def test_superadmin_can_patch_floor_image(api_client, superadmin, floor):
    auth(api_client, superadmin)
    response = api_client.patch(
        floors_detail_url(floor.id),
        {'plan_image': make_image_file('new_plan.jpg')},
        format='multipart',
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()['plan_image_url'] is not None


@pytest.mark.django_db
def test_employee_cannot_patch_floor(api_client, employee, floor):
    auth(api_client, employee)
    response = api_client.patch(
        floors_detail_url(floor.id),
        {'name': 'Hacked'},
        format='json',
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


# ── DELETE ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_superadmin_can_delete_floor(api_client, superadmin, floor):
    auth(api_client, superadmin)
    floor_id = floor.id
    response = api_client.delete(floors_detail_url(floor_id))
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not Floor.objects.filter(id=floor_id).exists()


@pytest.mark.django_db
def test_delete_cascades_to_map_points(api_client, superadmin, floor_with_points):
    auth(api_client, superadmin)
    floor_id = floor_with_points.id
    point_ids = list(MapPoint.objects.filter(floor=floor_with_points).values_list('id', flat=True))
    assert len(point_ids) == 2

    response = api_client.delete(floors_detail_url(floor_id))
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not Floor.objects.filter(id=floor_id).exists()
    assert not MapPoint.objects.filter(id__in=point_ids).exists()


@pytest.mark.django_db
def test_employee_cannot_delete_floor(api_client, employee, floor):
    auth(api_client, employee)
    response = api_client.delete(floors_detail_url(floor.id))
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert Floor.objects.filter(id=floor.id).exists()


@pytest.mark.django_db
def test_delete_floor_deletes_resource_linked_via_map_point(api_client, superadmin, company):
    auth(api_client, superadmin)
    resource = Resource.objects.create(name='Desk A-01', resource_type='desk', floor=2)
    floor = Floor.objects.create(number=2, name='Second Floor', company=company)
    MapPoint.objects.create(floor=floor, point_type='desk', x=10.0, y=20.0, resource=resource)

    response = api_client.delete(floors_detail_url(floor.id))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not Floor.objects.filter(id=floor.id).exists()
    assert not Resource.objects.filter(id=resource.id).exists()


@pytest.mark.django_db
def test_delete_floor_deletes_resource_by_floor_number(api_client, superadmin, company):
    """Resource created with this floor's number but not placed on the map is also deleted."""
    auth(api_client, superadmin)
    floor = Floor.objects.create(number=5, name='Fifth Floor', company=company)
    resource = Resource.objects.create(name='Desk B-01', resource_type='desk', floor=5)

    response = api_client.delete(floors_detail_url(floor.id))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not Floor.objects.filter(id=floor.id).exists()
    assert not Resource.objects.filter(id=resource.id).exists()
