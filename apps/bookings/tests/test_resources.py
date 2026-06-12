"""Integration tests for bookings Resource CRUD (DEV-63), catalog (DEV-70), availability (DEV-63+)."""

from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.utils import timezone
from django.utils.timezone import make_aware
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource, ResourceBlock
from apps.companies.models import Company
from apps.notifications.models import Notification
from apps.services.models import Floor
from apps.users.models import User


RESOURCES_URL = '/api/v1/bookings/resources/'
BULK_CREATE_URL = '/api/v1/bookings/resources/bulk-create/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Tenant Co', plan='basic')


@pytest.fixture
def premium_company(db):
    return Company.objects.create(name='Premium Co', plan='premium')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@resources.test',
        password='pass',
        first_name='S',
        last_name='A',
        role='superadmin',
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp@resources.test',
        password='pass',
        first_name='E',
        last_name='M',
        role='employee',
        company=company,
    )


@pytest.fixture
def premium_employee(db, premium_company):
    return User.objects.create_user(
        email='prem@resources.test',
        password='pass',
        first_name='P',
        last_name='R',
        role='employee',
        company=premium_company,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@resources.test',
        password='pass',
        first_name='G',
        last_name='U',
        role='guest',
    )


@pytest.mark.django_db
class TestResourceListAuth:
    def test_anonymous_list_401(self, api_client):
        r = api_client.get(RESOURCES_URL)
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_list_200(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        r = api_client.get(RESOURCES_URL)
        assert r.status_code == status.HTTP_200_OK

    def test_employee_list_200(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        r = api_client.get(RESOURCES_URL)
        assert r.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestResourceCreate:
    def test_non_superadmin_create_403(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'D1',
                'floor': 2,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_create_meeting_room(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'meeting_room',
                'name': 'MR A',
                'floor': 3,
                'zone': 'North',
                'description': 'Big room',
                'capacity': 12,
                'equipment': {'projector': True, 'tv': False, 'whiteboard': True},
                'min_duration_minutes': 60,
                'max_duration_minutes': 240,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_201_CREATED
        body = r.json()
        assert body['type'] == 'meeting_room'
        assert body['capacity'] == 12
        assert body['equipment']['projector'] is True
        assert body['equipment']['tv'] is False
        rid = body['id']
        resource = Resource.objects.get(pk=rid)
        assert resource.has_projector is True
        assert resource.has_tv is False

    def test_meeting_room_capacity_required(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'meeting_room',
                'name': 'MR B',
                'floor': 1,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_superadmin_create_desk(self, api_client, superadmin, company):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'Hot 1',
                'floor': 5,
                'has_monitor': True,
                'has_dock': True,
                'has_power_outlet': True,
                'is_hot_desk': True,
                'assigned_company': company.id,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_201_CREATED
        body = r.json()
        assert body['type'] == 'desk'
        assert body['assigned_company'] == company.id

    def test_superadmin_create_parking(self, api_client, superadmin, company):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'parking',
                'name': 'P-01',
                'floor': 1,
                'parking_type': 'vip',
                'assigned_company': company.id,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_201_CREATED
        body = r.json()
        assert body['parking_type'] == 'vip'
        assert Resource.objects.get(pk=body['id']).is_vip is True

    def test_parking_type_required_on_create(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'parking',
                'name': 'P-02',
                'floor': 1,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_superadmin_create_capsule(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'capsule',
                'name': 'Cap 1',
                'floor': 6,
                'capsule_zone': 'quiet',
            },
            format='json',
        )
        assert r.status_code == status.HTTP_201_CREATED
        assert r.json()['capsule_zone'] == 'quiet'

    def test_availability_start_must_be_before_end(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'Desk Time',
                'floor': 2,
                'availability_start': '18:00:00',
                'availability_end': '09:00:00',
            },
            format='json',
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        body = r.json()
        assert 'availability_start' in body.get('error', {}).get('details', body)

    def test_availability_days_values_must_be_0_to_6(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'Desk Days',
                'floor': 2,
                'availability_days': [1, 2, 7],
            },
            format='json',
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        body = r.json()
        assert 'availability_days' in body.get('error', {}).get('details', body)

    def test_create_with_availability_fields(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'Desk Schedule',
                'floor': 2,
                'availability_start': '09:00:00',
                'availability_end': '19:00:00',
                'availability_days': [1, 2, 3, 4, 5],
            },
            format='json',
        )
        assert r.status_code == status.HTTP_201_CREATED
        body = r.json()
        assert body['availability_start'] == '09:00:00'
        assert body['availability_end'] == '19:00:00'
        assert body['availability_days'] == [1, 2, 3, 4, 5]


@pytest.mark.django_db
class TestResourceBulkCreate:
    def test_non_superadmin_bulk_create_403(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        r = api_client.post(
            BULK_CREATE_URL,
            {
                'template': {
                    'type': 'desk',
                    'floor': 3,
                },
                'count': 3,
                'name_prefix': 'Стол',
            },
            format='json',
        )
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_bulk_create_resources(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            BULK_CREATE_URL,
            {
                'template': {
                    'type': 'desk',
                    'floor': 3,
                    'availability_start': '09:00:00',
                    'availability_end': '18:00:00',
                    'availability_days': [1, 2, 3, 4, 5],
                },
                'count': 3,
                'name_prefix': 'Стол',
            },
            format='json',
        )
        assert r.status_code == status.HTTP_201_CREATED
        body = r.json()
        assert len(body) == 3
        assert [item['name'] for item in body] == ['Стол 1', 'Стол 2', 'Стол 3']
        assert all(item['availability_days'] == [1, 2, 3, 4, 5] for item in body)


@pytest.mark.django_db
class TestResourceDeactivateAndDelete:
    def test_patch_deactivate_cancels_future_bookings_and_notifies(
        self, api_client, superadmin, employee, company
    ):
        api_client.force_authenticate(user=superadmin)
        create = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'Desk X',
                'floor': 1,
            },
            format='json',
        )
        rid = create.json()['id']
        resource = Resource.objects.get(pk=rid)
        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(hours=1)
        booking = Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time=start,
            end_time=end,
            status='confirmed',
        )
        api_client.force_authenticate(user=superadmin)
        patch = api_client.patch(
            f'{RESOURCES_URL}{rid}/',
            {'is_active': False},
            format='json',
        )
        assert patch.status_code == status.HTTP_200_OK
        booking.refresh_from_db()
        assert booking.status == 'cancelled'
        assert Notification.objects.filter(
            user=employee,
            notification_type='booking_cancelled',
        ).exists()

    def test_delete_blocked_when_future_bookings(self, api_client, superadmin, employee, company):
        api_client.force_authenticate(user=superadmin)
        create = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Desk Y', 'floor': 1},
            format='json',
        )
        rid = create.json()['id']
        resource = Resource.objects.get(pk=rid)
        Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time=timezone.now() + timedelta(days=2),
            end_time=timezone.now() + timedelta(days=2, hours=1),
            status='confirmed',
        )
        api_client.force_authenticate(user=superadmin)
        r = api_client.delete(f'{RESOURCES_URL}{rid}/')
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert Resource.objects.filter(pk=rid).exists()

    def test_delete_ok_without_future_bookings(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        create = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Desk Z', 'floor': 1},
            format='json',
        )
        rid = create.json()['id']
        r = api_client.delete(f'{RESOURCES_URL}{rid}/')
        assert r.status_code == status.HTTP_204_NO_CONTENT
        assert not Resource.objects.filter(pk=rid).exists()


@pytest.mark.django_db
class TestResourceVisibility:
    def test_inactive_hidden_from_non_superadmin(self, api_client, superadmin, employee):
        api_client.force_authenticate(user=superadmin)
        create = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Hidden', 'floor': 1, 'is_active': False},
            format='json',
        )
        rid = create.json()['id']
        api_client.force_authenticate(user=employee)
        r = api_client.get(f'{RESOURCES_URL}{rid}/')
        assert r.status_code == status.HTTP_404_NOT_FOUND
        api_client.force_authenticate(user=superadmin)
        r2 = api_client.get(f'{RESOURCES_URL}{rid}/')
        assert r2.status_code == status.HTTP_200_OK


def _list_results(response):
    data = response.json()
    if isinstance(data, dict) and 'results' in data:
        return data['results']
    return data


@pytest.mark.django_db
class TestResourceCatalogFilters:
    def test_filter_type_alias_and_capacity(self, api_client, superadmin, employee):
        api_client.force_authenticate(user=superadmin)
        api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Alpha Desk', 'floor': 2},
            format='json',
        )
        api_client.post(
            RESOURCES_URL,
            {
                'type': 'meeting_room',
                'name': 'Байтерек зал',
                'floor': 3,
                'zone': 'A',
                'capacity': 8,
                'equipment': {'projector': True, 'tv': True},
            },
            format='json',
        )
        api_client.post(
            RESOURCES_URL,
            {
                'type': 'meeting_room',
                'name': 'Small room',
                'floor': 3,
                'capacity': 4,
                'equipment': {'projector': False, 'tv': True},
            },
            format='json',
        )
        api_client.force_authenticate(user=employee)
        r = api_client.get(RESOURCES_URL, {'type': 'desk'})
        names = {x['name'] for x in _list_results(r)}
        assert 'Alpha Desk' in names
        assert 'Байтерек зал' not in names

        r2 = api_client.get(RESOURCES_URL, {'capacity_min': 6, 'capacity_max': 10})
        names2 = {x['name'] for x in _list_results(r2)}
        assert 'Байтерек зал' in names2
        assert 'Small room' not in names2

        r3 = api_client.get(RESOURCES_URL, {'equipment': 'projector,tv'})
        names3 = {x['name'] for x in _list_results(r3)}
        assert 'Байтерек зал' in names3
        assert 'Small room' not in names3

        r4 = api_client.get(RESOURCES_URL, {'search': 'байтерек'})
        assert len(_list_results(r4)) == 1
        assert _list_results(r4)[0]['name'] == 'Байтерек зал'

        r2_body = r2.json()
        assert 'meeting_room_equipment_keys' in r2_body
        assert {'projector', 'tv'}.issubset(set(r2_body['meeting_room_equipment_keys']))

        r3_body = r3.json()
        assert set(r3_body['meeting_room_equipment_keys']) >= {'projector', 'tv'}

        assert api_client.get(RESOURCES_URL, {'type': 'desk'}).json()['meeting_room_equipment_keys'] == []

    def test_search_does_not_match_zone_only(self, api_client, superadmin, employee):
        api_client.force_authenticate(user=superadmin)
        api_client.post(
            RESOURCES_URL,
            {
                'type': 'meeting_room',
                'name': 'Room X',
                'floor': 1,
                'zone': 'Байтерек wing',
                'capacity': 6,
            },
            format='json',
        )
        api_client.force_authenticate(user=employee)
        r = api_client.get(RESOURCES_URL, {'search': 'байтерек'})
        assert _list_results(r) == []


@pytest.mark.django_db
class TestResourceCatalogOrdering:
    def test_ordering_name_lexicographic(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        tag = 'OrderNameDEV71'
        created = []
        for n in [f'Z_{tag}', f'A_{tag}', f'M_{tag}']:
            c = api_client.post(
                RESOURCES_URL,
                {'type': 'desk', 'name': n, 'floor': 1},
                format='json',
            )
            assert c.status_code == status.HTTP_201_CREATED
            created.append(n)
        r = api_client.get(RESOURCES_URL, {'ordering': 'name', 'page_size': 100})
        assert r.status_code == status.HTTP_200_OK
        ours = [x['name'] for x in _list_results(r) if tag in x['name']]
        assert ours == sorted(created)


@pytest.mark.django_db
class TestResourceCatalogAssignedVisibility:
    def test_basic_does_not_see_own_company_assigned(
        self, api_client, superadmin, employee, company
    ):
        # basic plan: assigned resources are hidden even if assigned to own company
        api_client.force_authenticate(user=superadmin)
        c = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'Dedicated basic',
                'floor': 1,
                'assigned_company': company.id,
            },
            format='json',
        )
        rid = c.json()['id']
        api_client.force_authenticate(user=employee)
        r = api_client.get(RESOURCES_URL)
        ids = {x['id'] for x in _list_results(r)}
        assert rid not in ids

    def test_premium_sees_own_assigned(self, api_client, superadmin, premium_employee, premium_company):
        api_client.force_authenticate(user=superadmin)
        c = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'Dedicated premium',
                'floor': 1,
                'assigned_company': premium_company.id,
            },
            format='json',
        )
        rid = c.json()['id']
        api_client.force_authenticate(user=premium_employee)
        r = api_client.get(RESOURCES_URL)
        ids = {x['id'] for x in _list_results(r)}
        assert rid in ids

    def test_premium_does_not_see_other_company_assigned(
        self, api_client, superadmin, premium_employee, company
    ):
        api_client.force_authenticate(user=superadmin)
        c = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'Other co desk',
                'floor': 1,
                'assigned_company': company.id,
            },
            format='json',
        )
        rid = c.json()['id']
        api_client.force_authenticate(user=premium_employee)
        r = api_client.get(RESOURCES_URL)
        ids = {x['id'] for x in _list_results(r)}
        assert rid not in ids


@pytest.mark.django_db
class TestResourceCatalogStatus:
    def test_status_free_and_occupied_and_soon(self, api_client, superadmin, employee, company):
        api_client.force_authenticate(user=superadmin)
        d1 = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'Free desk', 'floor': 1}, format='json')
        d2 = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'Busy desk', 'floor': 1}, format='json')
        r1_id = d1.json()['id']
        r2_id = d2.json()['id']
        resource2 = Resource.objects.get(pk=r2_id)
        fixed = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        # Long booking → occupied (ends more than 30 min after "soon" window starts)
        Booking.objects.create(
            resource=resource2,
            user=employee,
            company=company,
            start_time=fixed - timedelta(hours=1),
            end_time=fixed + timedelta(hours=2),
            status='confirmed',
        )
        api_client.force_authenticate(user=employee)
        with patch('django.utils.timezone.now', return_value=fixed):
            r = api_client.get(RESOURCES_URL)
        by_id = {x['id']: x for x in _list_results(r)}
        assert by_id[r1_id]['status'] == 'free'
        assert by_id[r2_id]['status'] == 'occupied'
        assert by_id[r2_id]['available_at'] is None

    def test_status_soon_available(self, api_client, superadmin, employee, company):
        api_client.force_authenticate(user=superadmin)
        d = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'Soon', 'floor': 1}, format='json')
        rid = d.json()['id']
        resource = Resource.objects.get(pk=rid)
        fixed = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        # 5 minutes is within the SOON_AVAILABLE_MINUTES (15 min) threshold
        end = fixed + timedelta(minutes=5)
        Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time=fixed - timedelta(hours=1),
            end_time=end,
            status='confirmed',
        )
        api_client.force_authenticate(user=employee)
        with patch('django.utils.timezone.now', return_value=fixed):
            r = api_client.get(RESOURCES_URL)
        row = next(x for x in _list_results(r) if x['id'] == rid)
        assert row['status'] == 'soon_available'
        assert row['available_at'] is not None

    def test_block_marks_occupied(self, api_client, superadmin, employee):
        api_client.force_authenticate(user=superadmin)
        d = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'Blocked', 'floor': 1}, format='json')
        rid = d.json()['id']
        resource = Resource.objects.get(pk=rid)
        fixed = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        ResourceBlock.objects.create(
            resource=resource,
            blocked_by=employee,
            start_time=fixed - timedelta(minutes=30),
            end_time=fixed + timedelta(hours=1),
            reason='event',
        )
        api_client.force_authenticate(user=employee)
        with patch('django.utils.timezone.now', return_value=fixed):
            r = api_client.get(RESOURCES_URL)
        row = next(x for x in _list_results(r) if x['id'] == rid)
        assert row['status'] == 'blocked'


@pytest.mark.django_db
class TestResourceAvailabilityIntervalFilter:
    def test_free_interval_excludes_booking_overlap(self, api_client, superadmin, employee, company):
        api_client.force_authenticate(user=superadmin)
        a = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'Slot A', 'floor': 1}, format='json')
        b = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'Slot B', 'floor': 1}, format='json')
        id_a = a.json()['id']
        id_b = b.json()['id']
        res_b = Resource.objects.get(pk=id_b)
        slot_start = make_aware(datetime(2030, 6, 10, 10, 0, 0), dt_timezone.utc)
        slot_end = make_aware(datetime(2030, 6, 10, 11, 0, 0), dt_timezone.utc)
        Booking.objects.create(
            resource=res_b,
            user=employee,
            company=company,
            start_time=slot_start,
            end_time=slot_end,
            status='confirmed',
        )
        api_client.force_authenticate(user=employee)
        r = api_client.get(
            RESOURCES_URL,
            {
                'available_from': '2030-06-10T09:30:00Z',
                'available_to': '2030-06-10T11:30:00Z',
            },
        )
        assert r.status_code == status.HTTP_200_OK
        ids = {x['id'] for x in _list_results(r)}
        assert id_a in ids
        assert id_b not in ids

    def test_free_interval_excludes_block_overlap(self, api_client, superadmin, employee):
        api_client.force_authenticate(user=superadmin)
        c = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'Blocked slot', 'floor': 1}, format='json')
        rid = c.json()['id']
        resource = Resource.objects.get(pk=rid)
        slot_start = make_aware(datetime(2030, 7, 1, 9, 0, 0), dt_timezone.utc)
        slot_end = make_aware(datetime(2030, 7, 1, 12, 0, 0), dt_timezone.utc)
        ResourceBlock.objects.create(
            resource=resource,
            blocked_by=employee,
            start_time=slot_start,
            end_time=slot_end,
            reason='maintenance',
        )
        api_client.force_authenticate(user=employee)
        r = api_client.get(
            RESOURCES_URL,
            {
                'available_from': '2030-07-01T09:00:00Z',
                'available_to': '2030-07-01T10:00:00Z',
            },
        )
        ids = {x['id'] for x in _list_results(r)}
        assert rid not in ids

    def test_invalid_interval_returns_empty(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        r = api_client.get(
            RESOURCES_URL,
            {
                'available_from': '2030-08-01T12:00:00Z',
                'available_to': '2030-08-01T10:00:00Z',
            },
        )
        assert _list_results(r) == []


@pytest.mark.django_db
class TestResourceScheduleEndpoints:
    def test_schedule_day_returns_booking(
        self, api_client, superadmin, employee, company
    ):
        api_client.force_authenticate(user=superadmin)
        cr = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'Sched desk', 'floor': 1}, format='json')
        rid = cr.json()['id']
        resource = Resource.objects.get(pk=rid)
        d = date(2031, 3, 2)
        b_start = make_aware(datetime.combine(d, time(10, 0)))
        b_end = make_aware(datetime.combine(d, time(11, 0)))
        Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time=b_start,
            end_time=b_end,
            status='confirmed',
        )
        api_client.force_authenticate(user=employee)
        url = f'{RESOURCES_URL}{rid}/schedule/'
        r = api_client.get(url, {'date': '2031-03-02'})
        assert r.status_code == status.HTTP_200_OK
        body = r.json()
        # Only confirmed bookings are returned (not blocks)
        assert len(body) == 1
        slot = body[0]
        assert set(slot.keys()) == {'booking_id', 'start', 'end', 'status'}
        assert slot['booking_id'] is not None
        assert slot['status'] in ('occupied', 'soon_available')

    def test_schedule_defaults_to_today_and_ignores_week_param(
        self, api_client, superadmin, employee, company
    ):
        api_client.force_authenticate(user=superadmin)
        cr = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'Week desk', 'floor': 1}, format='json')
        rid = cr.json()['id']
        resource = Resource.objects.get(pk=rid)
        # Booking on 2031-03-05 — not today, so will not appear in default view
        Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time=make_aware(datetime(2031, 3, 5, 9, 0)),
            end_time=make_aware(datetime(2031, 3, 5, 10, 0)),
            status='confirmed',
        )
        api_client.force_authenticate(user=employee)
        url = f'{RESOURCES_URL}{rid}/schedule/'

        # No params: defaults to today (returns 200, no bookings for today)
        missing = api_client.get(url)
        assert missing.status_code == status.HTTP_200_OK

        # date param: returns slots for that date
        r = api_client.get(url, {'date': '2031-03-05'})
        assert r.status_code == status.HTTP_200_OK
        assert len(r.json()) == 1

        # Invalid date: returns 400
        bad = api_client.get(url, {'date': 'not-a-date'})
        assert bad.status_code == status.HTTP_400_BAD_REQUEST

    def test_retrieve_includes_schedule_list(self, api_client, superadmin, employee, company):
        api_client.force_authenticate(user=superadmin)
        cr = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'Detail sched', 'floor': 1}, format='json')
        rid = cr.json()['id']
        resource = Resource.objects.get(pk=rid)
        today = timezone.localdate()
        b_start = timezone.make_aware(datetime.combine(today, time(10, 0)))
        b_end = timezone.make_aware(datetime.combine(today, time(10, 30)))
        Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time=b_start,
            end_time=b_end,
            status='confirmed',
        )
        api_client.force_authenticate(user=employee)
        r = api_client.get(f'{RESOURCES_URL}{rid}/')
        assert r.status_code == status.HTTP_200_OK
        data = r.json()
        assert 'schedule' in data
        assert isinstance(data['schedule'], list)
        assert len(data['schedule']) >= 1
        assert data['schedule'][0]['booking_id'] is not None

    def test_schedule_guest(self, api_client, superadmin, employee, company, guest_user):
        api_client.force_authenticate(user=superadmin)
        cr = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Guest sched desk', 'floor': 1},
            format='json',
        )
        rid = cr.json()['id']
        resource = Resource.objects.get(pk=rid)
        d = date(2031, 3, 2)
        Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time=make_aware(datetime.combine(d, time(10, 0))),
            end_time=make_aware(datetime.combine(d, time(11, 0))),
            status='confirmed',
        )
        api_client.force_authenticate(user=guest_user)
        r = api_client.get(f'{RESOURCES_URL}{rid}/schedule/', {'date': '2031-03-02'})
        assert r.status_code == status.HTTP_200_OK
        slots = r.json()
        assert len(slots) == 1
        slot = slots[0]
        assert set(slot.keys()) == {'booking_id', 'start', 'end', 'status'}
        pii_keys = {'user', 'email', 'user_name', 'booked_by', 'first_name', 'last_name'}
        assert not pii_keys.intersection(slot.keys())


@pytest.mark.django_db
class TestResourceBlockingAcceptanceCriteria:
    def test_superadmin_blocks_resource_and_cancels_overlapping_bookings_with_notification(
        self, api_client, superadmin, employee, company
    ):
        api_client.force_authenticate(user=superadmin)
        create_resource = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'AC Block Desk', 'floor': 1},
            format='json',
        )
        resource_id = create_resource.json()['id']
        resource = Resource.objects.get(pk=resource_id)
        start_time = timezone.now() + timedelta(days=1)
        end_time = start_time + timedelta(hours=2)
        booking = Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time=start_time,
            end_time=end_time,
            status='confirmed',
        )

        block_response = api_client.post(
            f'{RESOURCES_URL}{resource_id}/block/',
            {
                'start_time': (start_time + timedelta(minutes=30)).isoformat(),
                'end_time': (end_time + timedelta(hours=1)).isoformat(),
                'reason': 'Ремонт кондиционера',
            },
            format='json',
        )
        assert block_response.status_code == status.HTTP_201_CREATED
        booking.refresh_from_db()
        assert booking.status == 'cancelled'
        assert booking.cancel_reason == 'Ремонт кондиционера'
        assert booking.cancelled_by_id == superadmin.id
        notification = Notification.objects.filter(
            user=employee,
            notification_type='booking_cancelled',
        ).order_by('-id').first()
        assert notification is not None
        assert 'Ремонт кондиционера' in notification.body

    def test_non_superadmin_cannot_block_resource(self, api_client, superadmin, employee):
        api_client.force_authenticate(user=superadmin)
        create_resource = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'AC Restrict Desk', 'floor': 1},
            format='json',
        )
        resource_id = create_resource.json()['id']
        api_client.force_authenticate(user=employee)
        block_response = api_client.post(
            f'{RESOURCES_URL}{resource_id}/block/',
            {
                'start_time': (timezone.now() + timedelta(days=1)).isoformat(),
                'end_time': (timezone.now() + timedelta(days=1, hours=2)).isoformat(),
                'reason': 'event',
            },
            format='json',
        )
        assert block_response.status_code == status.HTTP_403_FORBIDDEN

    def test_booking_conflict_control_considers_resource_block(
        self, api_client, superadmin, employee
    ):
        employee.is_email_verified = True
        employee.save(update_fields=['is_email_verified'])
        api_client.force_authenticate(user=superadmin)
        create_resource = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'AC Conflict Desk', 'floor': 1},
            format='json',
        )
        resource_id = create_resource.json()['id']
        resource = Resource.objects.get(pk=resource_id)
        blocked_from = timezone.now() + timedelta(days=2)
        blocked_to = blocked_from + timedelta(hours=2)
        ResourceBlock.objects.create(
            resource=resource,
            blocked_by=superadmin,
            start_time=blocked_from,
            end_time=blocked_to,
            reason='Внутреннее мероприятие',
        )

        api_client.force_authenticate(user=employee)
        booking_response = api_client.post(
            '/api/v1/bookings/reservations/',
            {
                'resource_id': resource_id,
                'start_time': (blocked_from + timedelta(minutes=30)).isoformat(),
                'end_time': (blocked_from + timedelta(hours=1)).isoformat(),
            },
            format='json',
        )
        assert booking_response.status_code == status.HTTP_409_CONFLICT

    def test_can_list_and_delete_resource_blocks(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        create_resource = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'AC Blocks Desk', 'floor': 1},
            format='json',
        )
        resource_id = create_resource.json()['id']
        create_block = ResourceBlock.objects.create(
            resource_id=resource_id,
            blocked_by=superadmin,
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=2),
            reason='Ремонт',
        )
        block_id = create_block.id

        blocks_response = api_client.get(f'{RESOURCES_URL}{resource_id}/blocks/')
        assert blocks_response.status_code == status.HTTP_200_OK
        blocks_payload = blocks_response.json()
        assert isinstance(blocks_payload, list)
        assert any(item['id'] == block_id for item in blocks_payload)

        delete_response = api_client.delete(f'{RESOURCES_URL}{resource_id}/blocks/{block_id}/')
        assert delete_response.status_code == status.HTTP_204_NO_CONTENT
        assert not ResourceBlock.objects.filter(pk=block_id).exists()

    def test_catalog_marks_blocked_status_with_reason(self, api_client, superadmin, employee):
        api_client.force_authenticate(user=superadmin)
        create_resource = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'AC Catalog Desk', 'floor': 1},
            format='json',
        )
        resource_id = create_resource.json()['id']
        ResourceBlock.objects.create(
            resource_id=resource_id,
            blocked_by=superadmin,
            start_time=timezone.now() - timedelta(minutes=5),
            end_time=timezone.now() + timedelta(hours=3),
            reason='Ремонт покрытия',
        )

        api_client.force_authenticate(user=employee)
        catalog_response = api_client.get(RESOURCES_URL)
        assert catalog_response.status_code == status.HTTP_200_OK
        row = next(item for item in _list_results(catalog_response) if item['id'] == resource_id)
        assert row['status'] == 'blocked'
        assert row['reason'] == 'Ремонт покрытия'


# ---------------------------------------------------------------------------
# Tests: is_soon_available canonical rule (unit) and catalog integration
# ---------------------------------------------------------------------------

class TestIsSoonAvailable:
    """Unit tests for the single source of truth: is_soon_available() in schedule.py."""

    def test_returns_true_when_ending_within_threshold(self):
        from datetime import timedelta, datetime, timezone as dt_tz
        from apps.bookings.schedule import is_soon_available

        now = datetime(2030, 1, 1, 12, 0, 0, tzinfo=dt_tz.utc)
        end_time = now + timedelta(minutes=5)
        assert is_soon_available(end_time, now) is True

    def test_returns_false_when_ending_after_threshold(self):
        from datetime import timedelta, datetime, timezone as dt_tz
        from apps.bookings.schedule import is_soon_available

        now = datetime(2030, 1, 1, 12, 0, 0, tzinfo=dt_tz.utc)
        # 30 min is well beyond the default 15-min threshold
        end_time = now + timedelta(minutes=30)
        assert is_soon_available(end_time, now) is False

    def test_returns_false_when_already_ended(self):
        from datetime import timedelta, datetime, timezone as dt_tz
        from apps.bookings.schedule import is_soon_available

        now = datetime(2030, 1, 1, 12, 0, 0, tzinfo=dt_tz.utc)
        end_time = now - timedelta(minutes=1)
        assert is_soon_available(end_time, now) is False


@pytest.mark.django_db
class TestResourceCatalogSoonAvailableRule:
    """Integration tests confirming catalog status uses is_soon_available as single source of truth."""

    def test_resource_status_is_soon_available_when_ending_within_threshold(
        self, api_client, superadmin, employee, company
    ):
        """Resource with a booking ending in 5 min shows status=soon_available and available_at set."""
        api_client.force_authenticate(user=superadmin)
        d = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'SoonDesk', 'floor': 1}, format='json')
        rid = d.json()['id']
        resource = Resource.objects.get(pk=rid)
        fixed = timezone.now().replace(microsecond=0)
        Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time=fixed - timedelta(hours=1),
            end_time=fixed + timedelta(minutes=5),
            status='confirmed',
        )
        api_client.force_authenticate(user=employee)
        with patch('django.utils.timezone.now', return_value=fixed):
            r = api_client.get(RESOURCES_URL)
        row = next(x for x in _list_results(r) if x['id'] == rid)
        assert row['status'] == 'soon_available'
        assert row['available_at'] is not None

    def test_resource_status_is_occupied_when_ending_after_threshold(
        self, api_client, superadmin, employee, company
    ):
        """Resource with a booking ending in 30 min shows status=occupied and available_at=None."""
        api_client.force_authenticate(user=superadmin)
        d = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'OccDesk', 'floor': 1}, format='json')
        rid = d.json()['id']
        resource = Resource.objects.get(pk=rid)
        fixed = timezone.now().replace(microsecond=0)
        Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time=fixed - timedelta(hours=1),
            end_time=fixed + timedelta(minutes=30),
            status='confirmed',
        )
        api_client.force_authenticate(user=employee)
        with patch('django.utils.timezone.now', return_value=fixed):
            r = api_client.get(RESOURCES_URL)
        row = next(x for x in _list_results(r) if x['id'] == rid)
        assert row['status'] == 'occupied'
        assert row['available_at'] is None


# ---------------------------------------------------------------------------
# Regression: DEV-63 — resource with assigned_company must be filterable by company
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestResourceFilterByCompany:
    """Superadmin must be able to filter resources by assigned_company via ?assigned_company= or ?company_id=."""

    def test_filter_by_assigned_company_returns_assigned_resource(
        self, api_client, superadmin, company
    ):
        api_client.force_authenticate(user=superadmin)
        assigned = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'CompanyDesk', 'floor': 1, 'assigned_company': company.id},
            format='json',
        )
        unassigned = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'FreeDesk', 'floor': 1},
            format='json',
        )
        assigned_id = assigned.json()['id']
        unassigned_id = unassigned.json()['id']

        r = api_client.get(RESOURCES_URL, {'assigned_company': company.id})
        assert r.status_code == status.HTTP_200_OK
        ids = {x['id'] for x in _list_results(r)}
        assert assigned_id in ids
        assert unassigned_id not in ids

    def test_filter_by_company_id_alias(self, api_client, superadmin, company):
        api_client.force_authenticate(user=superadmin)
        assigned = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'AliasDesk', 'floor': 2, 'assigned_company': company.id},
            format='json',
        )
        assigned_id = assigned.json()['id']

        r = api_client.get(RESOURCES_URL, {'company_id': company.id})
        assert r.status_code == status.HTTP_200_OK
        ids = {x['id'] for x in _list_results(r)}
        assert assigned_id in ids

    def test_filter_by_wrong_company_excludes_resource(
        self, api_client, superadmin, company, premium_company
    ):
        api_client.force_authenticate(user=superadmin)
        assigned = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'WrongCoDeskDEV63', 'floor': 1, 'assigned_company': company.id},
            format='json',
        )
        assigned_id = assigned.json()['id']

        r = api_client.get(RESOURCES_URL, {'assigned_company': premium_company.id})
        assert r.status_code == status.HTTP_200_OK
        ids = {x['id'] for x in _list_results(r)}
        assert assigned_id not in ids


@pytest.mark.django_db
class TestResourceAvailabilityDaysAndHoursFilter:
    """
    Tests for the available_days and available_from/available_until filters
    inside filter_free_interval().

    All request datetimes use UTC ('Z').  TIME_ZONE = 'Asia/Almaty' (UTC+5),
    so '2030-06-15T05:00:00Z' == '2030-06-15 10:00 Almaty local'.

    2030-06-15 is a Saturday (weekday=5).
    2030-06-16 is a Sunday  (weekday=6).
    2030-06-17 is a Monday  (weekday=0).
    """

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _create_resource(self, api_client, superadmin, name, available_days,
                         available_from='08:00:00', available_until='22:00:00'):
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': name,
                'floor': 1,
                'availability_days': available_days,
                'availability_start': available_from,
                'availability_end': available_until,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_201_CREATED, r.json()
        return r.json()['id']

    # ------------------------------------------------------------------ #
    # available_days tests                                                 #
    # ------------------------------------------------------------------ #

    def test_filter_excludes_resource_unavailable_day(self, api_client, superadmin, employee):
        """Resource available Mon–Fri only must be excluded for a Saturday request."""
        api_client.force_authenticate(user=superadmin)
        # Weekdays only: Mon=0 … Fri=4
        rid = self._create_resource(api_client, superadmin, 'WeekdayOnly', [0, 1, 2, 3, 4])

        api_client.force_authenticate(user=employee)
        # 2030-06-15 is Saturday → weekday=5, not in [0,1,2,3,4]
        # UTC 05:00–07:00 == Almaty 10:00–12:00 (within operating hours)
        r = api_client.get(
            RESOURCES_URL,
            {
                'available_from': '2030-06-15T05:00:00Z',
                'available_to': '2030-06-15T07:00:00Z',
            },
        )
        assert r.status_code == status.HTTP_200_OK
        ids = {x['id'] for x in _list_results(r)}
        assert rid not in ids

    def test_filter_excludes_resource_partial_day_coverage(self, api_client, superadmin, employee):
        """Resource available Mon–Sat must be excluded when the request spans Sat+Sun (Sun is not covered)."""
        api_client.force_authenticate(user=superadmin)
        # Mon=0 … Sat=5, no Sunday (6)
        rid = self._create_resource(api_client, superadmin, 'MonToSat', [0, 1, 2, 3, 4, 5])

        api_client.force_authenticate(user=employee)
        # 2030-06-15 is Saturday (weekday=5), 2030-06-16 is Sunday (weekday=6).
        # requested_days = {5, 6}; resource covers {0..5} → 6 is missing → EXCLUDED.
        # UTC 19:00 on 2030-06-15 == Almaty 00:00 on 2030-06-16, so the range spans
        # both Sat and Sun in local time.
        r = api_client.get(
            RESOURCES_URL,
            {
                'available_from': '2030-06-15T05:00:00Z',   # Almaty: 2030-06-15 (Sat) 10:00
                'available_to': '2030-06-16T05:00:00Z',     # Almaty: 2030-06-16 (Sun) 10:00
            },
        )
        assert r.status_code == status.HTTP_200_OK
        ids = {x['id'] for x in _list_results(r)}
        assert rid not in ids

    def test_filter_includes_resource_available_day(self, api_client, superadmin, employee):
        """Same weekdays-only resource IS returned for a Monday request."""
        api_client.force_authenticate(user=superadmin)
        rid = self._create_resource(api_client, superadmin, 'WeekdayOnly2', [0, 1, 2, 3, 4])

        api_client.force_authenticate(user=employee)
        # 2030-06-17 is Monday → weekday=0, in [0,1,2,3,4]
        # UTC 05:00–07:00 == Almaty 10:00–12:00 (within operating hours)
        r = api_client.get(
            RESOURCES_URL,
            {
                'available_from': '2030-06-17T05:00:00Z',
                'available_to': '2030-06-17T07:00:00Z',
            },
        )
        assert r.status_code == status.HTTP_200_OK
        ids = {x['id'] for x in _list_results(r)}
        assert rid in ids

    def test_filter_empty_available_days_means_every_day(self, api_client, superadmin, employee):
        """A resource with available_days=[] (no restriction) appears on any day."""
        api_client.force_authenticate(user=superadmin)
        rid = self._create_resource(api_client, superadmin, 'AnyDay', [])

        api_client.force_authenticate(user=employee)
        # Saturday request — should still be included because [] means "every day"
        r = api_client.get(
            RESOURCES_URL,
            {
                'available_from': '2030-06-15T05:00:00Z',
                'available_to': '2030-06-15T07:00:00Z',
            },
        )
        assert r.status_code == status.HTTP_200_OK
        ids = {x['id'] for x in _list_results(r)}
        assert rid in ids

    # ------------------------------------------------------------------ #
    # available hours tests (single-day only)                             #
    # ------------------------------------------------------------------ #

    def test_filter_excludes_resource_outside_hours(self, api_client, superadmin, employee):
        """Request that starts before available_from or ends after available_until excludes resource."""
        api_client.force_authenticate(user=superadmin)
        # Resource is open 09:00–18:00 Almaty local
        rid = self._create_resource(
            api_client, superadmin, 'NineToSix', [],
            available_from='09:00:00',
            available_until='18:00:00',
        )

        api_client.force_authenticate(user=employee)
        # Request: 2030-06-17 (Monday) 03:00–14:00 UTC == 08:00–19:00 Almaty local.
        # 08:00 < resource.available_from (09:00) → excluded.
        r = api_client.get(
            RESOURCES_URL,
            {
                'available_from': '2030-06-17T03:00:00Z',
                'available_to': '2030-06-17T14:00:00Z',
            },
        )
        assert r.status_code == status.HTTP_200_OK
        ids = {x['id'] for x in _list_results(r)}
        assert rid not in ids

    def test_filter_includes_resource_within_hours(self, api_client, superadmin, employee):
        """Request within the resource operating hours includes the resource."""
        api_client.force_authenticate(user=superadmin)
        rid = self._create_resource(
            api_client, superadmin, 'NineToSix2', [],
            available_from='09:00:00',
            available_until='18:00:00',
        )

        api_client.force_authenticate(user=employee)
        # Request: 2030-06-17 (Monday) 05:00–12:00 UTC == 10:00–17:00 Almaty local.
        # 10:00 >= available_from (09:00) AND 17:00 <= available_until (18:00) → included.
        r = api_client.get(
            RESOURCES_URL,
            {
                'available_from': '2030-06-17T05:00:00Z',
                'available_to': '2030-06-17T12:00:00Z',
            },
        )
        assert r.status_code == status.HTTP_200_OK
        ids = {x['id'] for x in _list_results(r)}
        assert rid in ids

    def test_filter_skips_hours_check_for_multi_day_range(self, api_client, superadmin, employee):
        """For multi-day ranges the hours check is skipped; only days filter applies.

        A resource open 09:00–18:00 would normally fail an 08:00–19:00 single-day
        request, but for a range that spans two different local dates the hours
        check is bypassed entirely.
        """
        api_client.force_authenticate(user=superadmin)
        # Resource open 09:00–18:00 Mon–Sun (every day)
        rid = self._create_resource(
            api_client, superadmin, 'MultiDayRes', [],
            available_from='09:00:00',
            available_until='18:00:00',
        )

        api_client.force_authenticate(user=employee)
        # Two-day local range: 2030-06-17 (Monday) and 2030-06-18 (Tuesday) in Almaty.
        # UTC 19:00 on 2030-06-17 == Almaty 00:00 on 2030-06-18, so:
        #   local dt_from = 2030-06-17 10:00 (UTC 05:00)
        #   local dt_to   = 2030-06-18 10:00 (UTC 05:00 next day)
        # Both days are weekdays (Mon=0, Tue=1) so days filter passes.
        # The time portion spans midnight, which would fail a 09:00–18:00 hours check
        # on a single-day request, but the hours check is skipped for multi-day ranges.
        r = api_client.get(
            RESOURCES_URL,
            {
                'available_from': '2030-06-17T05:00:00Z',  # Almaty: 2030-06-17 10:00
                'available_to': '2030-06-18T05:00:00Z',    # Almaty: 2030-06-18 10:00
            },
        )
        assert r.status_code == status.HTTP_200_OK
        ids = {x['id'] for x in _list_results(r)}
        assert rid in ids


@pytest.mark.django_db
class TestResourceFloorIdFilter:
    """Regression tests for the floor_id filter.

    Original bug: floor_id + available_from/available_to caused a Django FieldError:
    "Field Resource.floor_fk cannot be both deferred and traversed using select_related
    at the same time." The exception was silently swallowed inside the list comprehension
    in filter_free_interval, and the queryset fell back to returning ALL resources instead
    of an empty list.

    Fix: add .select_related(None) before .only('id', 'available_days') so the
    floor_fk select_related annotation does not conflict with deferred loading.
    """

    def test_floor_id_no_resources_returns_empty(self, api_client, superadmin, employee):
        """floor_id pointing to a floor with no linked resources returns an empty list."""
        empty_floor = Floor.objects.create(number=99, name='Empty Floor Regression')

        api_client.force_authenticate(user=superadmin)
        floor_other = Floor.objects.create(number=100, name='Other Floor Regression')
        r_other = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Desk On Other Floor', 'floor': 100},
            format='json',
        )
        assert r_other.status_code == status.HTTP_201_CREATED
        Resource.objects.filter(id=r_other.json()['id']).update(floor_fk=floor_other)

        api_client.force_authenticate(user=employee)
        resp = api_client.get(RESOURCES_URL, {'floor_id': str(empty_floor.id)})
        assert resp.status_code == status.HTTP_200_OK
        assert _list_results(resp) == [], (
            f'Expected [] for floor_id={empty_floor.id} (no resources linked), '
            f'got {len(_list_results(resp))} results.'
        )

    def test_floor_id_filters_to_correct_resources(self, api_client, superadmin, employee):
        """floor_id returns only resources linked to that specific floor."""
        api_client.force_authenticate(user=superadmin)
        floor_a = Floor.objects.create(number=10, name='Floor A Regression')
        floor_b = Floor.objects.create(number=11, name='Floor B Regression')

        r_a = api_client.post(
            RESOURCES_URL, {'type': 'desk', 'name': 'Desk Floor A Reg', 'floor': 10}, format='json'
        )
        r_b = api_client.post(
            RESOURCES_URL, {'type': 'desk', 'name': 'Desk Floor B Reg', 'floor': 11}, format='json'
        )
        assert r_a.status_code == status.HTTP_201_CREATED
        assert r_b.status_code == status.HTTP_201_CREATED

        Resource.objects.filter(id=r_a.json()['id']).update(floor_fk=floor_a)
        Resource.objects.filter(id=r_b.json()['id']).update(floor_fk=floor_b)

        api_client.force_authenticate(user=employee)
        resp = api_client.get(RESOURCES_URL, {'floor_id': str(floor_a.id)})
        assert resp.status_code == status.HTTP_200_OK
        ids = {x['id'] for x in _list_results(resp)}
        assert r_a.json()['id'] in ids
        assert r_b.json()['id'] not in ids

    def test_floor_id_with_availability_empty_floor_returns_empty_not_all(
        self, api_client, superadmin, employee
    ):
        """Regression: floor_id + available_from/to on a floor with no resources returns [].

        Before the fix (missing select_related(None) in filter_free_interval), Django raised
        FieldError which was silently swallowed and all resources were returned instead.
        """
        api_client.force_authenticate(user=superadmin)
        empty_floor = Floor.objects.create(number=200, name='Empty Floor Avail Regression')
        floor_other = Floor.objects.create(number=201, name='Other Floor Avail Regression')

        r_other = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Desk Avail Reg Other', 'floor': 201},
            format='json',
        )
        assert r_other.status_code == status.HTTP_201_CREATED
        Resource.objects.filter(id=r_other.json()['id']).update(floor_fk=floor_other)

        api_client.force_authenticate(user=employee)
        # Combine floor_id (empty floor) with an availability interval.
        # This was the exact trigger for the FieldError regression.
        resp = api_client.get(RESOURCES_URL, {
            'floor_id': str(empty_floor.id),
            'available_from': '2030-06-17T05:00:00Z',
            'available_to': '2030-06-17T07:00:00Z',
        })
        assert resp.status_code == status.HTTP_200_OK
        results = _list_results(resp)
        assert results == [], (
            f'BUG REGRESSION: floor_id + availability returned {len(results)} resources '
            f'for a floor with no resources (expected []).'
        )
