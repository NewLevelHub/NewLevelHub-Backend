"""Tests for nested company invitations API."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company, Invitation
from apps.notifications.models import Notification
from apps.users.models import User


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Invite Co', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Co', plan='premium')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@test.com',
        password='pass',
        first_name='S',
        last_name='A',
        role='superadmin',
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@invite.co',
        password='pass',
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@invite.co',
        password='pass',
        first_name='Normal',
        last_name='Employee',
        role='employee',
        company=company,
    )


def auth(client, user):
    client.force_authenticate(user=user)
    return client


def invitations_url(company_id):
    return f'/api/v1/companies/{company_id}/invitations/'


def invitation_revoke_url(company_id, invitation_id):
    return f'/api/v1/companies/{company_id}/invitations/{invitation_id}/revoke/'


def invitation_resend_url(company_id, invitation_id):
    return f'/api/v1/companies/{company_id}/invitations/{invitation_id}/resend/'


@pytest.mark.django_db
class TestInvitationCreate:
    @patch('django.db.transaction.on_commit', side_effect=lambda fn, using=None: fn())
    @patch('apps.companies.serializers.send_invitation_email.delay')
    def test_create_invitation_sets_expiry_and_enqueues_email(
        self, mock_delay, _mock_on_commit, api_client, company_admin, company
    ):
        auth(api_client, company_admin)
        started_at = timezone.now()
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'new.user@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED

        invitation = Invitation.objects.get(company=company, email='new.user@example.com')
        delta_seconds = (invitation.expires_at - started_at).total_seconds()
        assert 71.9 * 3600 <= delta_seconds <= 72.1 * 3600
        mock_delay.assert_called_once_with(invitation.id)

    def test_cannot_invite_registered_email(self, api_client, company_admin, company, employee):
        auth(api_client, company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': employee.email, 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch('django.db.transaction.on_commit', side_effect=lambda fn, using=None: fn())
    @patch('apps.companies.serializers.send_invitation_email.delay')
    def test_can_invite_active_guest_email(self, mock_delay, _mock_on_commit, api_client, company_admin, company):
        User.objects.create_user(
            email='guest@example.com',
            password='pass',
            first_name='Guest',
            last_name='User',
            role='guest',
            company=None,
            is_active=True,
        )
        auth(api_client, company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'guest@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        mock_delay.assert_called_once()

    @patch('django.db.transaction.on_commit', side_effect=lambda fn, using=None: fn())
    @patch('apps.companies.serializers.send_invitation_email.delay')
    def test_can_invite_same_email_after_member_removed_from_company(
        self, mock_delay, _mock_on_commit, api_client, company_admin, company, employee
    ):
        employee.company = None
        employee.is_active = False
        employee.role = 'guest'
        employee.save(update_fields=['company', 'is_active', 'role', 'updated_at'])
        auth(api_client, company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': employee.email, 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        mock_delay.assert_called_once()

    def test_cannot_invite_deactivated_member_still_in_company(self, api_client, company_admin, company, employee):
        employee.is_active = False
        employee.save(update_fields=['is_active'])
        auth(api_client, company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': employee.email, 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'деактивирован' in str(response.data).lower()

    def test_cannot_invite_when_active_invite_exists(self, api_client, company_admin, company):
        Invitation.objects.create(
            company=company,
            email='taken@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=72),
        )
        auth(api_client, company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'taken@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_company_admin_cannot_invite_company_admin(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'new.admin@example.com', 'role': 'company_admin'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch('django.db.transaction.on_commit', side_effect=lambda fn, using=None: fn())
    @patch('apps.companies.serializers.send_invitation_email.delay')
    def test_superadmin_can_invite_company_admin(self, mock_delay, _mock_on_commit, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'new.admin@example.com', 'role': 'company_admin'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        invitation = Invitation.objects.get(company=company, email='new.admin@example.com')
        assert invitation.role == 'company_admin'
        mock_delay.assert_called_once_with(invitation.id)

    def test_company_invite_rejects_service_manager_role(self, api_client, superadmin, company):
        # service_manager is a building-staff role and must be invited via
        # POST /api/v1/companies/building-invites/, not the company-scoped endpoint.
        auth(api_client, superadmin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'svc-manager@example.com', 'role': 'service_manager'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_company_invite_rejects_reception_role(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'reception@example.com', 'role': 'reception'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch('apps.companies.views.send_invitation_email.delay')
    def test_employee_limit_reached_returns_400(self, _mock_delay, api_client, company_admin, company):
        company.max_employees = 2
        company.save(update_fields=['max_employees'])
        User.objects.create_user(
            email='employee-2@invite.co',
            password='pass',
            first_name='Emp2',
            last_name='User',
            role='employee',
            company=company,
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'blocked@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'detail' in response.data or 'error' in response.data

    @patch('apps.companies.views.send_invitation_email.delay')
    def test_create_invitation_at_80_percent_creates_admin_notification(
        self, _mock_delay, api_client, company_admin, company
    ):
        company.max_employees = 5
        company.save(update_fields=['max_employees'])
        for idx in range(3):
            User.objects.create_user(
                email=f'emp-{idx}@invite.co',
                password='pass',
                first_name=f'Emp{idx}',
                last_name='User',
                role='employee',
                company=company,
            )

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'threshold@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning: 80%',
            body='Your employee usage has reached 80% (4/5 employees). Please free up space or upgrade your plan.',
        ).exists()

    @patch('apps.companies.views.send_invitation_email.delay')
    def test_create_invitation_at_95_percent_creates_admin_notification(
        self, _mock_delay, api_client, company_admin, company
    ):
        company.max_employees = 20
        company.save(update_fields=['max_employees'])
        for idx in range(18):
            User.objects.create_user(
                email=f'emp95-{idx}@invite.co',
                password='pass',
                first_name=f'Emp95{idx}',
                last_name='User',
                role='employee',
                company=company,
            )

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'threshold95@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning: 95%',
            body='Your employee usage has reached 95% (19/20 employees). Please free up space or upgrade your plan.',
        ).exists()


@pytest.mark.django_db
class TestInvitationListAndActions:
    def test_list_filters_by_is_used_and_is_expired(self, api_client, company_admin, company):
        active_inv = Invitation.objects.create(
            company=company,
            email='active@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=12),
        )
        Invitation.objects.create(
            company=company,
            email='expired@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() - timedelta(hours=1),
        )
        used_inv = Invitation.objects.create(
            company=company,
            email='used@example.com',
            invited_by=company_admin,
            role='employee',
            status=Invitation.STATUS_ACCEPTED,
            expires_at=timezone.now() + timedelta(hours=12),
        )
        auth(api_client, company_admin)

        used_response = api_client.get(invitations_url(company.id), {'is_used': 'true'})
        assert used_response.status_code == status.HTTP_200_OK
        used_ids = {row['id'] for row in used_response.data['results']}
        assert used_ids == {used_inv.id}

        expired_response = api_client.get(invitations_url(company.id), {'is_expired': 'true'})
        assert expired_response.status_code == status.HTTP_200_OK
        expired_ids = {row['id'] for row in expired_response.data['results']}
        assert active_inv.id not in expired_ids

    def test_revoke_marks_invitation_used(self, api_client, company_admin, company):
        invitation = Invitation.objects.create(
            company=company,
            email='revokable@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=72),
        )
        auth(api_client, company_admin)
        response = api_client.post(invitation_revoke_url(company.id, invitation.id), format='json')
        assert response.status_code == status.HTTP_200_OK
        invitation.refresh_from_db()
        assert invitation.is_used is True

    @patch('django.db.transaction.on_commit', side_effect=lambda fn, using=None: fn())
    @patch('apps.companies.views.send_invitation_email.delay')
    def test_resend_invalidates_old_and_creates_new(
        self, mock_delay, _mock_on_commit, api_client, company_admin, company
    ):
        invitation = Invitation.objects.create(
            company=company,
            email='resend@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=72),
        )
        auth(api_client, company_admin)
        response = api_client.post(invitation_resend_url(company.id, invitation.id), format='json')
        assert response.status_code == status.HTTP_200_OK

        invitation.refresh_from_db()
        assert invitation.is_used is True

        new_invitation = Invitation.objects.exclude(id=invitation.id).get(email='resend@example.com')
        assert new_invitation.company_id == company.id
        assert new_invitation.status == 'pending'
        assert str(new_invitation.token) != str(invitation.token)
        mock_delay.assert_called_once_with(new_invitation.id)

    def test_company_admin_cannot_access_other_company_invitations(
        self, api_client, company_admin, other_company
    ):
        auth(api_client, company_admin)
        response = api_client.get(invitations_url(other_company.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ── /api/v1/companies/building-invites/ ────────────────────────────────

BUILDING_INVITES_URL = '/api/v1/companies/building-invites/'


def building_invite_revoke_url(invitation_id):
    return f'/api/v1/companies/building-invites/{invitation_id}/revoke/'


def building_invite_resend_url(invitation_id):
    return f'/api/v1/companies/building-invites/{invitation_id}/resend/'


@pytest.mark.django_db
class TestBuildingInvitations:
    @patch('django.db.transaction.on_commit', side_effect=lambda fn, using=None: fn())
    @patch('apps.companies.serializers.send_invitation_email.delay')
    def test_superadmin_can_invite_service_manager(self, mock_delay, _mock_on_commit, api_client, superadmin):
        auth(api_client, superadmin)
        response = api_client.post(
            BUILDING_INVITES_URL,
            {'email': 'svc-manager@example.com', 'role': 'service_manager'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        invitation = Invitation.objects.get(email='svc-manager@example.com')
        assert invitation.company_id is None
        assert invitation.role == 'service_manager'
        mock_delay.assert_called_once_with(invitation.id)

    @patch('django.db.transaction.on_commit', side_effect=lambda fn, using=None: fn())
    @patch('apps.companies.serializers.send_invitation_email.delay')
    def test_superadmin_can_invite_reception(self, mock_delay, _mock_on_commit, api_client, superadmin):
        auth(api_client, superadmin)
        response = api_client.post(
            BUILDING_INVITES_URL,
            {'email': 'reception@example.com', 'role': 'reception'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        invitation = Invitation.objects.get(email='reception@example.com')
        assert invitation.company_id is None
        assert invitation.role == 'reception'
        mock_delay.assert_called_once_with(invitation.id)

    def test_employee_role_rejected(self, api_client, superadmin):
        auth(api_client, superadmin)
        response = api_client.post(
            BUILDING_INVITES_URL,
            {'email': 'employee@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_company_admin_forbidden(self, api_client, company_admin):
        auth(api_client, company_admin)
        response = api_client.post(
            BUILDING_INVITES_URL,
            {'email': 'svc@example.com', 'role': 'service_manager'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.post(
            BUILDING_INVITES_URL,
            {'email': 'svc@example.com', 'role': 'service_manager'},
            format='json',
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch('apps.companies.serializers.send_invitation_email.delay')
    def test_list_returns_only_building_invites(
        self, _mock_delay, api_client, superadmin, company, company_admin,
    ):
        # Company-scoped invite — should NOT appear in the building-invites list.
        Invitation.objects.create(
            company=company,
            email='employee@example.com',
            invited_by=company_admin,
            role='employee',
        )
        # Building-staff invite — should appear.
        Invitation.objects.create(
            company=None,
            email='svc@example.com',
            invited_by=superadmin,
            role='service_manager',
        )
        auth(api_client, superadmin)
        response = api_client.get(BUILDING_INVITES_URL)
        assert response.status_code == status.HTTP_200_OK
        emails = {row['email'] for row in response.json()['results']}
        # building-staff invite included, company-scoped invite excluded
        assert 'svc@example.com' in emails
        assert 'employee@example.com' not in emails

    @patch('apps.companies.serializers.send_invitation_email.delay')
    def test_revoke(self, _mock_delay, api_client, superadmin):
        invitation = Invitation.objects.create(
            company=None,
            email='svc@example.com',
            invited_by=superadmin,
            role='service_manager',
        )
        auth(api_client, superadmin)
        response = api_client.post(building_invite_revoke_url(invitation.id))
        assert response.status_code == status.HTTP_200_OK
        invitation.refresh_from_db()
        assert invitation.is_used is True

    @patch('django.db.transaction.on_commit', side_effect=lambda fn, using=None: fn())
    @patch('apps.companies.serializers.send_invitation_email.delay')
    def test_resend_creates_new_invitation(self, mock_delay, _mock_on_commit, api_client, superadmin):
        old = Invitation.objects.create(
            company=None,
            email='svc@example.com',
            invited_by=superadmin,
            role='service_manager',
        )
        auth(api_client, superadmin)
        response = api_client.post(building_invite_resend_url(old.id))
        assert response.status_code == status.HTTP_200_OK
        old.refresh_from_db()
        assert old.is_used is True
        new = Invitation.objects.filter(
            company__isnull=True, email='svc@example.com', status=Invitation.STATUS_PENDING,
        ).first()
        assert new is not None
        assert new.id != old.id
        mock_delay.assert_called_once_with(new.id)

    def test_duplicate_active_invite_rejected(self, api_client, superadmin):
        Invitation.objects.create(
            company=None,
            email='svc@example.com',
            invited_by=superadmin,
            role='service_manager',
            expires_at=timezone.now() + timedelta(hours=24),
        )
        auth(api_client, superadmin)
        response = api_client.post(
            BUILDING_INVITES_URL,
            {'email': 'svc@example.com', 'role': 'service_manager'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ── /api/v1/companies/building-staff/ ──────────────────────────────────

BUILDING_STAFF_URL = '/api/v1/companies/building-staff/'


@pytest.mark.django_db
class TestBuildingStaffList:
    def _make_staff(self, role, email):
        return User.objects.create_user(
            email=email,
            password='pass',
            first_name=role.replace('_', ' ').title(),
            last_name='Staff',
            role=role,
            is_email_verified=True,
        )

    def test_superadmin_sees_only_building_roles(
        self, api_client, superadmin, company_admin, employee,
    ):
        svc = self._make_staff('service_manager', 'svc-list@nlh.test')
        rec = self._make_staff('reception', 'rec-list@nlh.test')
        auth(api_client, superadmin)
        response = api_client.get(BUILDING_STAFF_URL)
        assert response.status_code == status.HTTP_200_OK
        rows = response.json()['results']
        ids = {row['id'] for row in rows}
        assert svc.id in ids
        assert rec.id in ids
        assert company_admin.id not in ids
        assert employee.id not in ids

    def test_filter_by_role(self, api_client, superadmin):
        svc = self._make_staff('service_manager', 'svc-only@nlh.test')
        self._make_staff('reception', 'rec-skip@nlh.test')
        auth(api_client, superadmin)
        response = api_client.get(BUILDING_STAFF_URL, {'role': 'service_manager'})
        assert response.status_code == status.HTTP_200_OK
        roles = {row['role'] for row in response.json()['results']}
        assert roles == {'service_manager'}
        ids = {row['id'] for row in response.json()['results']}
        assert svc.id in ids

    def test_search_by_email(self, api_client, superadmin):
        target = self._make_staff('service_manager', 'unique-svc-search@nlh.test')
        auth(api_client, superadmin)
        response = api_client.get(BUILDING_STAFF_URL, {'search': 'unique-svc-search'})
        assert response.status_code == status.HTTP_200_OK
        ids = {row['id'] for row in response.json()['results']}
        assert ids == {target.id}

    def test_company_admin_forbidden(self, api_client, company_admin):
        auth(api_client, company_admin)
        response = api_client.get(BUILDING_STAFF_URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.get(BUILDING_STAFF_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
