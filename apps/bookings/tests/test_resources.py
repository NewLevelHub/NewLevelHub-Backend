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
        assert 'availability_start' in r.json()['detail']

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
        assert 'availability_days' in r.json()['detail']

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
    def test_basic_hides_assigned_even_own_company(
        self, api_client, superadmin, employee, company
    ):
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
        end = fixed + timedelta(minutes=20)
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
        assert row['status'] in ('occupied', 'soon_available')


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
    def test_schedule_day_returns_booking_and_block(
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
        bl_start = make_aware(datetime.combine(d, time(14, 0)))
        bl_end = make_aware(datetime.combine(d, time(15, 0)))
        ResourceBlock.objects.create(
            resource=resource,
            blocked_by=employee,
            start_time=bl_start,
            end_time=bl_end,
            reason='event',
        )
        api_client.force_authenticate(user=employee)
        url = f'{RESOURCES_URL}{rid}/schedule/'
        r = api_client.get(url, {'date': '2031-03-02'})
        assert r.status_code == status.HTTP_200_OK
        body = r.json()
        assert len(body) == 2
        booking_row = next(x for x in body if x['booking_id'] is not None)
        block_row = next(x for x in body if x['booking_id'] is None)
        assert booking_row['user_name'] == employee.full_name
        assert block_row['user_name'] is None

    def test_schedule_week_requires_single_param(self, api_client, superadmin, employee, company):
        api_client.force_authenticate(user=superadmin)
        cr = api_client.post(RESOURCES_URL, {'type': 'desk', 'name': 'Week desk', 'floor': 1}, format='json')
        rid = cr.json()['id']
        resource = Resource.objects.get(pk=rid)
        # Wednesday 2031-03-05; week starts Monday 2031-03-03
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
        r = api_client.get(url, {'week': '2031-03-05'})
        assert r.status_code == status.HTTP_200_OK
        assert len(r.json()) == 1

        bad = api_client.get(url, {'date': '2031-03-05', 'week': '2031-03-05'})
        assert bad.status_code == status.HTTP_400_BAD_REQUEST

        missing = api_client.get(url)
        assert missing.status_code == status.HTTP_400_BAD_REQUEST

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

    def test_schedule_for_guest_forbidden(self, api_client, superadmin, guest_user):
        api_client.force_authenticate(user=superadmin)
        cr = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Guest forbidden sched', 'floor': 1},
            format='json',
        )
        rid = cr.json()['id']
        api_client.force_authenticate(user=guest_user)
        r = api_client.get(f'{RESOURCES_URL}{rid}/schedule/', {'date': '2031-03-02'})
        assert r.status_code == status.HTTP_403_FORBIDDEN
