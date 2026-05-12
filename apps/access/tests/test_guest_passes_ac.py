from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.access.models import GuestPass
from apps.access.models import AccessLog
from apps.companies.models import Company
from apps.users.models import User

PASSES_URL = '/api/v1/access/passes/'
VALIDATE_URL = '/api/v1/access/validate/'


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
class TestGuestPassesValidateQrAC:
    def test_superadmin_can_validate_qr(self, api_client, company_admin):
        superadmin = User.objects.create_user(
            email='validate-superadmin@test.local',
            password='pass',
            first_name='Validate',
            last_name='SuperAdmin',
            role='superadmin',
            is_email_verified=True,
        )
        guest_pass = _create_pass(creator=company_admin, status_code='active')
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(VALIDATE_URL, {'qr_code': str(guest_pass.qr_code)}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['valid'] is True

    def test_reception_can_validate_qr(self, api_client, company_admin):
        reception = User.objects.create_user(
            email='validate-reception@test.local',
            password='pass',
            first_name='Validate',
            last_name='Reception',
            role='reception',
            company=company_admin.company,
            is_email_verified=True,
        )
        guest_pass = _create_pass(creator=company_admin, status_code='active')
        api_client.force_authenticate(user=reception)
        response = api_client.post(VALIDATE_URL, {'qr_code': str(guest_pass.qr_code)}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['valid'] is True

    @pytest.mark.parametrize('role', ['company_admin', 'employee', 'guest'])
    def test_other_roles_get_403(self, api_client, company_admin, employee, guest_user, role):
        role_to_user = {
            'company_admin': company_admin,
            'employee': employee,
            'guest': guest_user,
        }
        guest_pass = _create_pass(creator=company_admin, status_code='active')
        api_client.force_authenticate(user=role_to_user[role])
        response = api_client.post(VALIDATE_URL, {'qr_code': str(guest_pass.qr_code)}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_validate_valid_pass_returns_ac_payload(self, api_client, company_admin):
        superadmin = User.objects.create_user(
            email='validate-payload-superadmin@test.local',
            password='pass',
            first_name='Validate',
            last_name='Payload',
            role='superadmin',
            is_email_verified=True,
        )
        guest_pass = _create_pass(creator=company_admin, status_code='active')
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(VALIDATE_URL, {'qr_code': str(guest_pass.qr_code)}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['valid'] is True
        assert response.data['guest_name'] == guest_pass.guest_name
        assert response.data['purpose'] == guest_pass.visit_purpose
        assert response.data['invited_by'] == guest_pass.created_by.full_name
        assert response.data['valid_from'].isoformat() == guest_pass.valid_from.isoformat()
        assert response.data['valid_until'].isoformat() == guest_pass.valid_until.isoformat()

    @pytest.mark.parametrize(
        ('status_code', 'valid_until_delta', 'times_used', 'expected_reason'),
        [
            ('revoked', timedelta(days=1), 0, 'revoked'),
            ('used', timedelta(days=1), 1, 'already_used'),
        ],
    )
    def test_validate_invalid_pass_returns_expected_reason(
        self,
        api_client,
        company_admin,
        status_code,
        valid_until_delta,
        times_used,
        expected_reason,
    ):
        superadmin = User.objects.create_user(
            email=f'validate-invalid-{expected_reason}@test.local',
            password='pass',
            first_name='Validate',
            last_name='Invalid',
            role='superadmin',
            is_email_verified=True,
        )
        now = timezone.now()
        guest_pass = GuestPass.objects.create(
            created_by=company_admin,
            company=company_admin.company,
            guest_name='Invalid Guest',
            guest_email=f'invalid-{expected_reason}@test.local',
            visit_purpose='Invalid check',
            status=status_code,
            usage_type='single',
            times_used=times_used,
            valid_from=now - timedelta(hours=1),
            valid_until=now + valid_until_delta,
        )
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(VALIDATE_URL, {'qr_code': str(guest_pass.qr_code)}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'valid': False, 'reason': expected_reason}

    def test_validate_expired_pass_returns_403(self, api_client, company_admin):
        superadmin = User.objects.create_user(
            email='validate-expired@test.local',
            password='pass',
            first_name='Validate',
            last_name='Expired',
            role='superadmin',
            is_email_verified=True,
        )
        now = timezone.now()
        guest_pass = GuestPass.objects.create(
            created_by=company_admin,
            company=company_admin.company,
            guest_name='Expired Guest',
            guest_email='expired-pass@test.local',
            visit_purpose='Expiry check',
            status='active',
            usage_type='single',
            times_used=0,
            valid_from=now - timedelta(hours=2),
            valid_until=now - timedelta(hours=1),
        )
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(VALIDATE_URL, {'qr_code': str(guest_pass.qr_code)}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'Срок действия QR-кода истек' in response.data['detail']

    def test_validate_not_found_returns_reason_not_found(self, api_client):
        superadmin = User.objects.create_user(
            email='validate-not-found@test.local',
            password='pass',
            first_name='Validate',
            last_name='NotFound',
            role='superadmin',
            is_email_verified=True,
        )
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(VALIDATE_URL, {'qr_code': '64fdbf4f-465e-40e6-8ef4-3f3c96d34ac6'}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'valid': False, 'reason': 'not_found'}

    @patch('apps.access.views.notify_pass_creator_on_entry.delay')
    def test_single_use_marks_used_creates_log_and_notifies(self, mocked_notify_delay, api_client, company_admin):
        superadmin = User.objects.create_user(
            email='validate-sideeffects@test.local',
            password='pass',
            first_name='Validate',
            last_name='Effects',
            role='superadmin',
            is_email_verified=True,
        )
        guest_pass = _create_pass(creator=company_admin, status_code='active', usage_type='single')
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(VALIDATE_URL, {'qr_code': str(guest_pass.qr_code)}, format='json')
        assert response.status_code == status.HTTP_200_OK

        guest_pass.refresh_from_db()
        assert guest_pass.status == 'used'
        assert guest_pass.times_used == 1

        access_log = AccessLog.objects.get(guest_pass=guest_pass)
        assert access_log.checked_by == superadmin
        assert access_log.method == 'qr'
        mocked_notify_delay.assert_called_once_with(guest_pass.id)


@pytest.mark.django_db
class TestQrTimeValidation:
    """Tests for time-based (valid_from / valid_until) QR validation."""

    def _superadmin(self, suffix):
        return User.objects.create_user(
            email=f'time-superadmin-{suffix}@test.local',
            password='pass',
            first_name='Time',
            last_name='Superadmin',
            role='superadmin',
            is_email_verified=True,
        )

    # --- validate_qr time checks ---

    def test_validate_qr_before_valid_from_returns_403(self, api_client, company_admin):
        superadmin = self._superadmin('before')
        now = timezone.now()
        guest_pass = GuestPass.objects.create(
            created_by=company_admin,
            company=company_admin.company,
            guest_name='Future Guest',
            guest_email='future@test.local',
            visit_purpose='Future visit',
            status='active',
            usage_type='single',
            times_used=0,
            valid_from=now + timedelta(hours=2),
            valid_until=now + timedelta(hours=4),
        )
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(VALIDATE_URL, {'qr_code': str(guest_pass.qr_code)}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'Доступ ещё не разрешён' in response.data['detail']

    def test_validate_qr_after_valid_until_returns_403(self, api_client, company_admin):
        superadmin = self._superadmin('after')
        now = timezone.now()
        guest_pass = GuestPass.objects.create(
            created_by=company_admin,
            company=company_admin.company,
            guest_name='Past Guest',
            guest_email='past@test.local',
            visit_purpose='Past visit',
            status='active',
            usage_type='single',
            times_used=0,
            valid_from=now - timedelta(hours=4),
            valid_until=now - timedelta(hours=1),
        )
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(VALIDATE_URL, {'qr_code': str(guest_pass.qr_code)}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'Срок действия QR-кода истек' in response.data['detail']

    @patch('apps.access.views.notify_pass_creator_on_entry.delay')
    def test_validate_qr_within_window_succeeds(self, mocked_delay, api_client, company_admin):
        superadmin = self._superadmin('within')
        now = timezone.now()
        guest_pass = GuestPass.objects.create(
            created_by=company_admin,
            company=company_admin.company,
            guest_name='Active Guest',
            guest_email='active-time@test.local',
            visit_purpose='Active visit',
            status='active',
            usage_type='single',
            times_used=0,
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(hours=1),
        )
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(VALIDATE_URL, {'qr_code': str(guest_pass.qr_code)}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['valid'] is True

    # --- GuestPassCreateSerializer valid_from optionality ---

    @patch('apps.access.tasks.send_guest_pass_email.delay')
    def test_create_guest_pass_without_valid_from_defaults_to_now(self, mocked_delay, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        now = timezone.now()
        payload = {
            'guest_name': 'No From Guest',
            'guest_email': 'no-from@test.local',
            'valid_until': (now + timedelta(days=2)).isoformat(),
            'is_single_use': True,
            'purpose': 'Default from test',
        }
        response = api_client.post(PASSES_URL, payload, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        from django.utils.dateparse import parse_datetime
        returned_from = parse_datetime(response.data['valid_from'])
        if returned_from is not None and returned_from.tzinfo is None:
            from django.utils import timezone as tz
            returned_from = tz.make_aware(returned_from)
        assert abs((returned_from - now).total_seconds()) < 5

    @patch('apps.access.tasks.send_guest_pass_email.delay')
    def test_create_guest_pass_with_explicit_valid_from(self, mocked_delay, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        now = timezone.now()
        explicit_from = now + timedelta(hours=1)
        payload = {
            'guest_name': 'Explicit From Guest',
            'guest_email': 'explicit-from@test.local',
            'valid_from': explicit_from.isoformat(),
            'valid_until': (now + timedelta(days=2)).isoformat(),
            'is_single_use': True,
            'purpose': 'Explicit from test',
        }
        response = api_client.post(PASSES_URL, payload, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        from django.utils.dateparse import parse_datetime
        returned_from = parse_datetime(response.data['valid_from'])
        if returned_from is not None and returned_from.tzinfo is None:
            from django.utils import timezone as tz
            returned_from = tz.make_aware(returned_from)
        assert abs((returned_from - explicit_from).total_seconds()) < 2

    def test_create_guest_pass_with_past_valid_from_returns_400(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        now = timezone.now()
        payload = {
            'guest_name': 'Past From Guest',
            'guest_email': 'past-from@test.local',
            'valid_from': (now - timedelta(minutes=10)).isoformat(),
            'valid_until': (now + timedelta(days=2)).isoformat(),
            'is_single_use': True,
            'purpose': 'Past from test',
        }
        response = api_client.post(PASSES_URL, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestExpireGuestPassesTask:
    """Unit tests for the expire_guest_passes periodic task (DEV-174)."""

    def _make_pass(self, company, creator, **kwargs):
        return GuestPass.objects.create(
            company=company,
            created_by=creator,
            guest_name='Task Guest',
            guest_email='task-guest@test.local',
            visit_purpose='test',
            usage_type='single',
            **kwargs,
        )

    def test_active_expired_pass_becomes_expired(self, company_admin):
        now = timezone.now()
        gp = self._make_pass(
            company_admin.company,
            company_admin,
            status='active',
            valid_from=now - timedelta(hours=2),
            valid_until=now - timedelta(minutes=1),
        )
        from apps.access.tasks import expire_guest_passes
        expire_guest_passes()
        gp.refresh_from_db()
        assert gp.status == 'expired'

    def test_still_valid_pass_is_not_changed(self, company_admin):
        now = timezone.now()
        gp = self._make_pass(
            company_admin.company,
            company_admin,
            status='active',
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(hours=1),
        )
        from apps.access.tasks import expire_guest_passes
        expire_guest_passes()
        gp.refresh_from_db()
        assert gp.status == 'active'

    def test_already_expired_pass_is_not_touched(self, company_admin):
        now = timezone.now()
        gp = self._make_pass(
            company_admin.company,
            company_admin,
            status='expired',
            valid_from=now - timedelta(hours=3),
            valid_until=now - timedelta(hours=2),
        )
        from apps.access.tasks import expire_guest_passes
        expire_guest_passes()
        gp.refresh_from_db()
        assert gp.status == 'expired'

    def test_idempotent_second_run(self, company_admin):
        now = timezone.now()
        gp = self._make_pass(
            company_admin.company,
            company_admin,
            status='active',
            valid_from=now - timedelta(hours=2),
            valid_until=now - timedelta(minutes=1),
        )
        from apps.access.tasks import expire_guest_passes
        expire_guest_passes()
        expire_guest_passes()
        gp.refresh_from_db()
        assert gp.status == 'expired'

    def test_filter_status_expired_returns_expired_passes(self, api_client, company_admin):
        now = timezone.now()
        from apps.access.tasks import expire_guest_passes
        self._make_pass(
            company_admin.company,
            company_admin,
            status='active',
            valid_from=now - timedelta(hours=2),
            valid_until=now - timedelta(minutes=1),
        )
        expire_guest_passes()
        api_client.force_authenticate(user=company_admin)
        response = api_client.get(PASSES_URL, {'status': 'expired'})
        assert response.status_code == status.HTTP_200_OK
        assert response.data['count'] >= 1
        assert all(p['status'] == 'expired' for p in response.data['results'])
