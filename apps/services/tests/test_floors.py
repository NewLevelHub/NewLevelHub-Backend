"""Integration tests for Floor Plans CRUD (services app)."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.services.models import Floor, MapPoint
from apps.users.models import User


FLOORS_URL = '/api/v1/services/floors/'


def floors_detail_url(floor_id):
    return f'{FLOORS_URL}{floor_id}/'


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
def test_guest_list_returns_403(api_client, guest, floor):
    auth(api_client, guest)
    response = api_client.get(FLOORS_URL)
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_guest_post_returns_403(api_client, guest):
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
