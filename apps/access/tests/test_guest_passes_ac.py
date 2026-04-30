from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.access.models import GuestPass
from apps.companies.models import Company
from apps.users.models import User

PASSES_URL = '/api/v1/access/passes/'


def pass_detail_url(pass_id):
    return f'/api/v1/access/passes/{pass_id}/'


def pass_revoke_url(pass_id):
    return f'/api/v1/access/passes/{pass_id}/revoke/'


def pass_resend_url(pass_id):
    return f'/api/v1/access/passes/{pass_id}/resend/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Access AC Co', plan='basic')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='access-admin@test.local',
        password='pass',
        first_name='Access',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='access-employee@test.local',
        password='pass',
        first_name='Access',
        last_name='Employee',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='access-superadmin@test.local',
        password='pass',
        first_name='Access',
        last_name='Superadmin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def second_company(db):
    return Company.objects.create(name='Another Access Co', plan='basic')


@pytest.fixture
def second_company_admin(db, second_company):
    return User.objects.create_user(
        email='access-admin-2@test.local',
        password='pass',
        first_name='Access',
        last_name='Admin2',
        role='company_admin',
        company=second_company,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='access-guest@test.local',
        password='pass',
        first_name='Access',
        last_name='Guest',
        role='guest',
        is_email_verified=True,
    )


def _payload(valid_until_days=5):
    now = timezone.now() + timedelta(minutes=5)
    return {
        'guest_name': 'John Visitor',
        'guest_email': 'john.visitor@test.local',
        'guest_phone': '+70000000000',
        'purpose': 'Business meeting',
        'valid_from': now.isoformat(),
        'valid_until': (now + timedelta(days=valid_until_days)).isoformat(),
        'is_single_use': True,
    }


def _create_pass(*, creator, guest_email='created.guest@test.local', status_code='active', usage_type='single'):
    now = timezone.now()
    return GuestPass.objects.create(
        created_by=creator,
        company=creator.company,
        guest_name='Created Guest',
        guest_email=guest_email,
        guest_phone='',
        visit_purpose='Created pass',
        status=status_code,
        usage_type=usage_type,
        valid_from=now - timedelta(hours=1),
        valid_until=now + timedelta(days=7),
    )


@pytest.mark.django_db
class TestGuestPassesCreateAC:
    def test_create_accepts_ac_payload_and_returns_201(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(PASSES_URL, _payload(), format='json')
        assert response.status_code == status.HTTP_201_CREATED

    def test_create_allows_missing_guest_phone(self, api_client, company_admin):
        payload = _payload()
        payload.pop('guest_phone')
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(PASSES_URL, payload, format='json')
        assert response.status_code == status.HTTP_201_CREATED

    def test_create_denies_guest_role(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        response = api_client.post(PASSES_URL, _payload(), format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_cannot_create_pass_for_self_email(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        payload = _payload()
        payload['guest_email'] = company_admin.email
        response = api_client.post(PASSES_URL, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_create_pass_for_existing_employee_email(self, api_client, company_admin, employee):
        api_client.force_authenticate(user=company_admin)
        payload = _payload()
        payload['guest_email'] = employee.email
        response = api_client.post(PASSES_URL, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_employee_can_create_pass(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        response = api_client.post(PASSES_URL, _payload(), format='json')
        assert response.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize('creator_fixture', ['company_admin', 'employee'])
    def test_cannot_create_pass_with_valid_from_in_past(self, request, api_client, creator_fixture):
        creator = request.getfixturevalue(creator_fixture)
        api_client.force_authenticate(user=creator)
        payload = _payload()
        payload['valid_from'] = (timezone.now() - timedelta(hours=1)).isoformat()
        payload['valid_until'] = (timezone.now() + timedelta(days=2)).isoformat()
        response = api_client.post(PASSES_URL, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_guest_coworker_cannot_create_more_than_two_active(self, api_client, company_admin):
        payload = _payload()
        _create_pass(creator=company_admin, guest_email=payload['guest_email'], status_code='active')
        _create_pass(creator=company_admin, guest_email=payload['guest_email'], status_code='active')
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(PASSES_URL, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_company_admin_has_no_active_pass_limit(self, api_client, company_admin):
        _create_pass(creator=company_admin, guest_email='guest-1@test.local', status_code='active')
        _create_pass(creator=company_admin, guest_email='guest-2@test.local', status_code='active')
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(PASSES_URL, _payload(), format='json')
        assert response.status_code == status.HTTP_201_CREATED

    def test_valid_until_cannot_exceed_thirty_days(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(PASSES_URL, _payload(valid_until_days=31), format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_generates_qr_file_reference(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(PASSES_URL, _payload(), format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data.get('qr_code')
        assert response.data.get('qr_image')

    @patch('apps.access.tasks.send_guest_pass_email.delay')
    def test_create_enqueues_guest_email_task(self, mocked_delay, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(PASSES_URL, _payload(), format='json')
        assert response.status_code == status.HTTP_201_CREATED
        mocked_delay.assert_called_once()


@pytest.mark.django_db
class TestGuestPassesListAndFiltersAC:
    def test_company_admin_get_returns_company_passes(self, api_client, company_admin):
        mine = _create_pass(creator=company_admin, status_code='active')
        other_admin = User.objects.create_user(
            email='other-admin@test.local',
            password='pass',
            first_name='Other',
            last_name='Admin',
            role='company_admin',
            company=company_admin.company,
            is_email_verified=True,
        )
        _create_pass(creator=other_admin, status_code='active')
        api_client.force_authenticate(user=company_admin)
        response = api_client.get(PASSES_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert mine.id in ids

    def test_employee_get_returns_only_own_passes(self, api_client, company_admin, employee):
        mine = _create_pass(creator=employee, status_code='active')
        _create_pass(creator=company_admin, status_code='active')
        api_client.force_authenticate(user=employee)
        response = api_client.get(PASSES_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert ids == [mine.id]

    def test_company_admin_sees_company_employees_passes(self, api_client, company_admin, employee):
        own = _create_pass(creator=company_admin, status_code='active')
        employee_pass = _create_pass(creator=employee, status_code='active')
        api_client.force_authenticate(user=company_admin)
        response = api_client.get(PASSES_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = {row['id'] for row in response.data.get('results', response.data)}
        assert own.id in ids
        assert employee_pass.id in ids

    def test_superadmin_sees_all_companies_passes(self, api_client, company_admin):
        company_b = Company.objects.create(name='Access Co B', plan='basic')
        admin_b = User.objects.create_user(
            email='access-admin-b@test.local',
            password='pass',
            first_name='Access',
            last_name='AdminB',
            role='company_admin',
            company=company_b,
            is_email_verified=True,
        )
        superadmin = User.objects.create_user(
            email='superadmin-access@test.local',
            password='pass',
            first_name='Super',
            last_name='Admin',
            role='superadmin',
            is_email_verified=True,
        )
        pass_a = _create_pass(creator=company_admin, status_code='active')
        pass_b = _create_pass(creator=admin_b, status_code='active')
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(PASSES_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = {row['id'] for row in response.data.get('results', response.data)}
        assert pass_a.id in ids
        assert pass_b.id in ids

    @pytest.mark.parametrize('filter_status', ['active', 'used', 'expired', 'revoked'])
    def test_status_filter_supported(self, api_client, company_admin, filter_status):
        matching = _create_pass(creator=company_admin, status_code=filter_status)
        other_status = 'active' if filter_status != 'active' else 'revoked'
        _create_pass(creator=company_admin, status_code=other_status)
        api_client.force_authenticate(user=company_admin)
        response = api_client.get(PASSES_URL, {'status': filter_status})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert ids == [matching.id]


@pytest.mark.django_db
class TestGuestPassesDetailAC:
    def test_get_detail_returns_qr_url(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        create_response = api_client.post(PASSES_URL, _payload(), format='json')
        assert create_response.status_code == status.HTTP_201_CREATED
        response = api_client.get(pass_detail_url(create_response.data['id']))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == create_response.data['id']
        assert response.data.get('qr_image')


@pytest.mark.django_db
class TestGuestPassActionsAC:
    def test_revoke_sets_status_revoked(self, api_client, company_admin):
        target_pass = _create_pass(creator=company_admin, status_code='active')
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(pass_revoke_url(target_pass.id))
        assert response.status_code == status.HTTP_200_OK
        target_pass.refresh_from_db()
        assert target_pass.status == 'revoked'

    @pytest.mark.parametrize('blocked_status', ['used', 'expired'])
    def test_revoke_rejects_used_or_expired(self, api_client, company_admin, blocked_status):
        target_pass = _create_pass(creator=company_admin, status_code=blocked_status)
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(pass_revoke_url(target_pass.id))
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_revoke_rejects_used_pass_after_qr_validation(self, api_client, company_admin):
        receiver = User.objects.create_user(
            email='reception@test.local',
            password='pass',
            first_name='Reception',
            last_name='Desk',
            role='employee',
            company=company_admin.company,
            is_email_verified=True,
        )
        target_pass = _create_pass(creator=company_admin, status_code='active', usage_type='single')
        api_client.force_authenticate(user=receiver)
        validate_response = api_client.post(
            '/api/v1/access/validate/',
            {'qr_code': str(target_pass.qr_code)},
            format='json',
        )
        assert validate_response.status_code == status.HTTP_200_OK
        target_pass.refresh_from_db()
        assert target_pass.status == 'used'

        api_client.force_authenticate(user=company_admin)
        revoke_response = api_client.post(pass_revoke_url(target_pass.id))
        assert revoke_response.status_code == status.HTTP_400_BAD_REQUEST
        target_pass.refresh_from_db()
        assert target_pass.status == 'used'

    def test_superadmin_can_revoke_any_company_pass(self, api_client, superadmin, second_company_admin):
        target_pass = _create_pass(creator=second_company_admin, status_code='active')
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(pass_revoke_url(target_pass.id))
        assert response.status_code == status.HTTP_200_OK
        target_pass.refresh_from_db()
        assert target_pass.status == 'revoked'

    def test_company_admin_cannot_revoke_other_company_pass(self, api_client, company_admin, second_company_admin):
        target_pass = _create_pass(creator=second_company_admin, status_code='active')
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(pass_revoke_url(target_pass.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @patch('apps.access.tasks.send_guest_pass_email.delay')
    def test_resend_requeues_qr_email(self, mocked_delay, api_client, company_admin):
        target_pass = _create_pass(creator=company_admin, status_code='active')
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(pass_resend_url(target_pass.id))
        assert response.status_code == status.HTTP_200_OK
        mocked_delay.assert_called_once_with(target_pass.id)

    @patch('apps.access.tasks.send_guest_pass_email.delay')
    def test_resend_rate_limited_to_three_per_hour(self, mocked_delay, api_client, company_admin):
        target_pass = _create_pass(creator=company_admin, status_code='active')
        api_client.force_authenticate(user=company_admin)
        for _ in range(3):
            ok_response = api_client.post(pass_resend_url(target_pass.id))
            assert ok_response.status_code == status.HTTP_200_OK
        limited_response = api_client.post(pass_resend_url(target_pass.id))
        assert limited_response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert mocked_delay.call_count == 3

    @pytest.mark.parametrize('blocked_status', ['revoked', 'used', 'expired'])
    @patch('apps.access.tasks.send_guest_pass_email.delay')
    def test_resend_rejects_non_active_statuses(self, mocked_delay, api_client, company_admin, blocked_status):
        target_pass = _create_pass(creator=company_admin, status_code=blocked_status)
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(pass_resend_url(target_pass.id))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mocked_delay.assert_not_called()


@pytest.mark.django_db
class TestGuestPassesAdminViewAC:
    def test_company_admin_sees_only_own_company_passes(self, api_client, company_admin, second_company_admin):
        mine = _create_pass(creator=company_admin, status_code='active')
        _create_pass(creator=second_company_admin, status_code='active')
        api_client.force_authenticate(user=company_admin)
        response = api_client.get(PASSES_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert ids == [mine.id]

    def test_superadmin_sees_all_passes(self, api_client, superadmin, company_admin, second_company_admin):
        first = _create_pass(creator=company_admin, status_code='active')
        second = _create_pass(creator=second_company_admin, status_code='active')
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(PASSES_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert first.id in ids
        assert second.id in ids

    def test_superadmin_filters_by_company_id(self, api_client, superadmin, company_admin, second_company_admin):
        mine = _create_pass(creator=company_admin, status_code='active')
        _create_pass(creator=second_company_admin, status_code='active')
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(PASSES_URL, {'company_id': company_admin.company_id})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert ids == [mine.id]

    def test_superadmin_filters_by_company_name(self, api_client, superadmin, company_admin, second_company_admin):
        mine = _create_pass(creator=company_admin, status_code='active')
        _create_pass(creator=second_company_admin, status_code='active')
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(PASSES_URL, {'company_name': company_admin.company.name})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert ids == [mine.id]

    def test_superadmin_company_name_filter_matches_partial(self, api_client, superadmin, company_admin):
        _create_pass(creator=company_admin, status_code='active')
        similar_company = Company.objects.create(name=f'{company_admin.company.name} Annex', plan='basic')
        similar_admin = User.objects.create_user(
            email='access-admin-similar@test.local',
            password='pass',
            first_name='Access',
            last_name='AdminSimilar',
            role='company_admin',
            company=similar_company,
            is_email_verified=True,
        )
        similar_pass = _create_pass(creator=similar_admin, status_code='active')
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(PASSES_URL, {'company_name': company_admin.company.name})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert similar_pass.id in ids

    def test_superadmin_filters_by_created_by(self, api_client, superadmin, company_admin):
        mine = _create_pass(creator=company_admin, status_code='active')
        another = User.objects.create_user(
            email='creator-filter@test.local',
            password='pass',
            first_name='Creator',
            last_name='Filter',
            role='employee',
            company=company_admin.company,
            is_email_verified=True,
        )
        _create_pass(creator=another, status_code='active')
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(PASSES_URL, {'created_by': company_admin.id})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert ids == [mine.id]

    def test_superadmin_filters_by_created_by_email(self, api_client, superadmin, company_admin):
        mine = _create_pass(creator=company_admin, status_code='active')
        another = User.objects.create_user(
            email='creator-email-filter@test.local',
            password='pass',
            first_name='Creator',
            last_name='EmailFilter',
            role='employee',
            company=company_admin.company,
            is_email_verified=True,
        )
        _create_pass(creator=another, status_code='active')
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(PASSES_URL, {'created_by_email': company_admin.email})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert ids == [mine.id]

    def test_superadmin_created_by_email_filter_matches_partial(self, api_client, superadmin, company_admin):
        _create_pass(creator=company_admin, status_code='active')
        similar_creator = User.objects.create_user(
            email=f'prefix+{company_admin.email}',
            password='pass',
            first_name='Creator',
            last_name='Similar',
            role='employee',
            company=company_admin.company,
            is_email_verified=True,
        )
        similar_pass = _create_pass(creator=similar_creator, status_code='active')
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(PASSES_URL, {'created_by_email': company_admin.email})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert similar_pass.id in ids

    def test_superadmin_filters_by_status(self, api_client, superadmin, company_admin):
        active_pass = _create_pass(creator=company_admin, status_code='active')
        revoked_pass = _create_pass(creator=company_admin, status_code='revoked')
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(PASSES_URL, {'status': 'active'})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert active_pass.id in ids
        assert revoked_pass.id not in ids

    def test_superadmin_filters_by_created_at_dates(self, api_client, superadmin, company_admin):
        old_pass = _create_pass(creator=company_admin, status_code='active')
        old_created_at = timezone.now() - timedelta(days=10)
        GuestPass.objects.filter(pk=old_pass.pk).update(created_at=old_created_at)
        recent_pass = _create_pass(creator=company_admin, status_code='active')
        date_from = (timezone.now() - timedelta(days=2)).date().isoformat()
        date_to = timezone.now().date().isoformat()
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(PASSES_URL, {'date_from': date_from, 'date_to': date_to})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.data.get('results', response.data)]
        assert recent_pass.id in ids
        assert old_pass.id not in ids
