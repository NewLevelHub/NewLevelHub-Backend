"""
Integration tests for Invitation.status field and associated business logic.

Acceptance criteria:
  AC1  — Revoke action → invitation.status becomes 'revoked'
  AC2  — Resend action → old invitation becomes 'revoked', new one is 'pending'
  AC3  — Accepting invite (InviteRegistrationSerializer) → status becomes 'accepted'
  AC4  — Filter ?status=pending returns only pending invitations
  AC5  — Filter ?status=revoked does not return pending or accepted invitations
  AC6  — is_valid=True only when status='pending' AND expires_at > now
  AC7  — is_used=True when status in ('accepted', 'revoked')
  AC8  — is_expired=True when status='expired' or pending-past-expiry
  AC9  — check_expired_invitations task marks pending+expired-at invitations as 'expired'
  AC10 — Building-staff revoke sets status='revoked'
  AC11 — Building-staff resend → old 'revoked', new 'pending'
  AC12 — ?status=accepted filter works for building-staff invitations
  AC13 — check_expired_invitations covers building-staff (company=None) invitations
  AC14 — Building-staff accept via InviteRegistrationSerializer sets status='accepted'
  AC15 — Legacy ?is_used= / ?is_expired= filters work on building-staff endpoint
  AC16 — Resend already-revoked building-staff invite returns 400
  AC17 — Building-staff list response includes 'status' field in serializer output
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company, Invitation
from apps.companies.tasks import check_expired_invitations
from apps.users.models import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='sa_invite_status@test.com',
        password='pass1234',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
    )


@pytest.fixture
def company(db):
    return Company.objects.create(name='Status Corp', plan='basic')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='ca_invite_status@test.com',
        password='pass1234',
        first_name='Carol',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def pending_invitation(db, company, company_admin):
    """A fresh pending invitation (expires 72 h from now)."""
    return Invitation.objects.create(
        company=company,
        email='invitee@test.com',
        invited_by=company_admin,
        role='employee',
    )


@pytest.fixture
def expired_invitation(db, company, company_admin):
    """A pending invitation whose expiry has already passed."""
    inv = Invitation.objects.create(
        company=company,
        email='expired@test.com',
        invited_by=company_admin,
        role='employee',
    )
    Invitation.objects.filter(pk=inv.pk).update(
        expires_at=timezone.now() - timedelta(hours=1),
    )
    inv.refresh_from_db()
    return inv


def auth(client, user):
    client.force_authenticate(user=user)
    return client


def invite_list_url(company_id):
    return f'/api/v1/companies/{company_id}/invitations/'


def revoke_url(company_id, invite_id):
    return f'/api/v1/companies/{company_id}/invitations/{invite_id}/revoke/'


def resend_url(company_id, invite_id):
    return f'/api/v1/companies/{company_id}/invitations/{invite_id}/resend/'


# ---------------------------------------------------------------------------
# AC1 — Revoke action sets status='revoked'
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRevokeAction:

    def test_revoke_sets_status_revoked(self, api_client, superadmin, company, pending_invitation):
        auth(api_client, superadmin)
        response = api_client.post(revoke_url(company.id, pending_invitation.id))
        assert response.status_code == status.HTTP_200_OK
        pending_invitation.refresh_from_db()
        assert pending_invitation.status == Invitation.STATUS_REVOKED

    def test_revoke_sets_used_at(self, api_client, superadmin, company, pending_invitation):
        auth(api_client, superadmin)
        api_client.post(revoke_url(company.id, pending_invitation.id))
        pending_invitation.refresh_from_db()
        assert pending_invitation.used_at is not None

    def test_company_admin_can_revoke(self, api_client, company_admin, company, pending_invitation):
        auth(api_client, company_admin)
        response = api_client.post(revoke_url(company.id, pending_invitation.id))
        assert response.status_code == status.HTTP_200_OK
        pending_invitation.refresh_from_db()
        assert pending_invitation.status == Invitation.STATUS_REVOKED


# ---------------------------------------------------------------------------
# AC2 — Resend action: old becomes 'revoked', new is 'pending'
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestResendAction:

    def test_resend_marks_old_as_revoked(
        self, api_client, superadmin, company, pending_invitation
    ):
        with patch('apps.companies.views.send_invitation_email') as mock_task:
            mock_task.delay.return_value = None
            auth(api_client, superadmin)
            response = api_client.post(resend_url(company.id, pending_invitation.id))
        assert response.status_code == status.HTTP_200_OK
        pending_invitation.refresh_from_db()
        assert pending_invitation.status == Invitation.STATUS_REVOKED

    def test_resend_creates_new_pending_invitation(
        self, api_client, superadmin, company, pending_invitation
    ):
        with patch('apps.companies.views.send_invitation_email') as mock_task:
            mock_task.delay.return_value = None
            auth(api_client, superadmin)
            api_client.post(resend_url(company.id, pending_invitation.id))
        new_inv = Invitation.objects.filter(
            company=company,
            email='invitee@test.com',
        ).order_by('-created_at').first()
        assert new_inv is not None
        assert new_inv.pk != pending_invitation.pk
        assert new_inv.status == Invitation.STATUS_PENDING

    def test_resend_already_revoked_returns_400(
        self, api_client, superadmin, company, pending_invitation
    ):
        # Revoke first
        pending_invitation.status = Invitation.STATUS_REVOKED
        pending_invitation.save(update_fields=['status'])

        auth(api_client, superadmin)
        response = api_client.post(resend_url(company.id, pending_invitation.id))
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC3 — Accepting invite via InviteRegistrationSerializer sets status='accepted'
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAcceptInvite:

    def test_registration_with_invite_sets_status_accepted(
        self, api_client, company, company_admin, pending_invitation
    ):
        payload = {
            'token': str(pending_invitation.token),
            'first_name': 'New',
            'last_name': 'Employee',
            'password': 'securepass1',
        }
        response = api_client.post('/api/v1/auth/register/invite/', payload, format='json')
        assert response.status_code == status.HTTP_201_CREATED, response.data
        pending_invitation.refresh_from_db()
        assert pending_invitation.status == Invitation.STATUS_ACCEPTED

    def test_registration_with_expired_invite_returns_error(
        self, api_client, expired_invitation
    ):
        payload = {
            'token': str(expired_invitation.token),
            'first_name': 'New',
            'last_name': 'Employee',
            'password': 'securepass1',
        }
        response = api_client.post('/api/v1/auth/register/invite/', payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_registration_with_accepted_invite_returns_error(
        self, api_client, pending_invitation
    ):
        # Mark invite as already accepted
        pending_invitation.status = Invitation.STATUS_ACCEPTED
        pending_invitation.save(update_fields=['status'])

        payload = {
            'token': str(pending_invitation.token),
            'first_name': 'Other',
            'last_name': 'User',
            'password': 'securepass1',
        }
        response = api_client.post('/api/v1/auth/register/invite/', payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC4 — Filter ?status=pending returns only pending
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStatusFilter:

    def _seed(self, company, company_admin):
        """Create one invitation per status."""
        inv_pending = Invitation.objects.create(
            company=company, email='p@test.com', invited_by=company_admin, role='employee',
        )
        inv_accepted = Invitation.objects.create(
            company=company, email='a@test.com', invited_by=company_admin, role='employee',
            status=Invitation.STATUS_ACCEPTED,
        )
        inv_revoked = Invitation.objects.create(
            company=company, email='r@test.com', invited_by=company_admin, role='employee',
            status=Invitation.STATUS_REVOKED,
        )
        inv_expired = Invitation.objects.create(
            company=company, email='e@test.com', invited_by=company_admin, role='employee',
            status=Invitation.STATUS_EXPIRED,
        )
        return inv_pending, inv_accepted, inv_revoked, inv_expired

    def test_filter_pending_returns_only_pending(
        self, api_client, superadmin, company, company_admin
    ):
        inv_pending, inv_accepted, inv_revoked, inv_expired = self._seed(company, company_admin)
        auth(api_client, superadmin)
        response = api_client.get(invite_list_url(company.id), {'status': 'pending'})
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert inv_pending.id in ids
        assert inv_accepted.id not in ids
        assert inv_revoked.id not in ids
        assert inv_expired.id not in ids

    # AC5
    def test_filter_revoked_does_not_return_pending_or_accepted(
        self, api_client, superadmin, company, company_admin
    ):
        inv_pending, inv_accepted, inv_revoked, inv_expired = self._seed(company, company_admin)
        auth(api_client, superadmin)
        response = api_client.get(invite_list_url(company.id), {'status': 'revoked'})
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert inv_revoked.id in ids
        assert inv_pending.id not in ids
        assert inv_accepted.id not in ids

    def test_filter_accepted_returns_only_accepted(
        self, api_client, superadmin, company, company_admin
    ):
        inv_pending, inv_accepted, inv_revoked, inv_expired = self._seed(company, company_admin)
        auth(api_client, superadmin)
        response = api_client.get(invite_list_url(company.id), {'status': 'accepted'})
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert inv_accepted.id in ids
        assert inv_pending.id not in ids
        assert inv_revoked.id not in ids

    def test_filter_expired_returns_only_expired(
        self, api_client, superadmin, company, company_admin
    ):
        inv_pending, inv_accepted, inv_revoked, inv_expired = self._seed(company, company_admin)
        auth(api_client, superadmin)
        response = api_client.get(invite_list_url(company.id), {'status': 'expired'})
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert inv_expired.id in ids
        assert inv_pending.id not in ids

    def test_invalid_status_param_ignored(
        self, api_client, superadmin, company, company_admin
    ):
        """An invalid status value is silently ignored — all invitations returned."""
        inv_pending, _, _, _ = self._seed(company, company_admin)
        auth(api_client, superadmin)
        # 'unknown' is not a valid choice — the view skips the filter
        response = api_client.get(invite_list_url(company.id), {'status': 'unknown'})
        assert response.status_code == status.HTTP_200_OK
        # Should return all 4 invitations
        assert response.data['count'] == 4


# ---------------------------------------------------------------------------
# AC6 — is_valid property
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsValidProperty:

    def test_pending_not_expired_is_valid(self, pending_invitation):
        assert pending_invitation.is_valid is True

    def test_pending_past_expiry_is_not_valid(self, expired_invitation):
        assert expired_invitation.is_valid is False

    def test_revoked_is_not_valid(self, pending_invitation):
        pending_invitation.status = Invitation.STATUS_REVOKED
        assert pending_invitation.is_valid is False

    def test_accepted_is_not_valid(self, pending_invitation):
        pending_invitation.status = Invitation.STATUS_ACCEPTED
        assert pending_invitation.is_valid is False


# ---------------------------------------------------------------------------
# AC7 — is_used property
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsUsedProperty:

    def test_pending_is_not_used(self, pending_invitation):
        assert pending_invitation.is_used is False

    def test_accepted_is_used(self, pending_invitation):
        pending_invitation.status = Invitation.STATUS_ACCEPTED
        assert pending_invitation.is_used is True

    def test_revoked_is_used(self, pending_invitation):
        pending_invitation.status = Invitation.STATUS_REVOKED
        assert pending_invitation.is_used is True

    def test_expired_is_not_used(self, pending_invitation):
        pending_invitation.status = Invitation.STATUS_EXPIRED
        assert pending_invitation.is_used is False


# ---------------------------------------------------------------------------
# AC8 — is_expired property
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsExpiredProperty:

    def test_pending_future_expiry_not_expired(self, pending_invitation):
        assert pending_invitation.is_expired is False

    def test_pending_past_expiry_is_expired(self, expired_invitation):
        assert expired_invitation.is_expired is True

    def test_status_expired_is_expired(self, pending_invitation):
        pending_invitation.status = Invitation.STATUS_EXPIRED
        assert pending_invitation.is_expired is True

    def test_revoked_is_not_expired(self, pending_invitation):
        pending_invitation.status = Invitation.STATUS_REVOKED
        assert pending_invitation.is_expired is False

    def test_accepted_is_not_expired(self, pending_invitation):
        pending_invitation.status = Invitation.STATUS_ACCEPTED
        assert pending_invitation.is_expired is False


# ---------------------------------------------------------------------------
# AC9 — check_expired_invitations task
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCheckExpiredTask:

    def test_task_marks_pending_past_expiry_as_expired(self, company, company_admin):
        past = timezone.now() - timedelta(hours=1)
        inv = Invitation.objects.create(
            company=company,
            email='task_test@test.com',
            invited_by=company_admin,
            role='employee',
        )
        Invitation.objects.filter(pk=inv.pk).update(expires_at=past)

        updated = check_expired_invitations()
        assert updated >= 1
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_EXPIRED

    def test_task_does_not_touch_accepted_or_revoked(self, company, company_admin):
        past = timezone.now() - timedelta(hours=1)
        inv_accepted = Invitation.objects.create(
            company=company,
            email='acc@test.com',
            invited_by=company_admin,
            role='employee',
            status=Invitation.STATUS_ACCEPTED,
        )
        Invitation.objects.filter(pk=inv_accepted.pk).update(expires_at=past)

        inv_revoked = Invitation.objects.create(
            company=company,
            email='rev@test.com',
            invited_by=company_admin,
            role='employee',
            status=Invitation.STATUS_REVOKED,
        )
        Invitation.objects.filter(pk=inv_revoked.pk).update(expires_at=past)

        check_expired_invitations()

        inv_accepted.refresh_from_db()
        inv_revoked.refresh_from_db()
        assert inv_accepted.status == Invitation.STATUS_ACCEPTED
        assert inv_revoked.status == Invitation.STATUS_REVOKED

    def test_task_does_not_touch_future_pending(self, company, company_admin):
        inv = Invitation.objects.create(
            company=company,
            email='future@test.com',
            invited_by=company_admin,
            role='employee',
        )
        check_expired_invitations()
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_PENDING


# ---------------------------------------------------------------------------
# AC10 / AC11 — Building-staff revoke and resend
# ---------------------------------------------------------------------------

@pytest.fixture
def building_invitation(db, superadmin):
    return Invitation.objects.create(
        company=None,
        email='reception@test.com',
        invited_by=superadmin,
        role='reception',
    )


@pytest.mark.django_db
class TestBuildingInvitationStatus:

    def test_revoke_building_invite_sets_revoked(
        self, api_client, superadmin, building_invitation
    ):
        auth(api_client, superadmin)
        response = api_client.post(
            f'/api/v1/companies/building-invites/{building_invitation.id}/revoke/'
        )
        assert response.status_code == status.HTTP_200_OK
        building_invitation.refresh_from_db()
        assert building_invitation.status == Invitation.STATUS_REVOKED

    def test_resend_building_invite_old_revoked_new_pending(
        self, api_client, superadmin, building_invitation
    ):
        with patch('apps.companies.views.send_invitation_email') as mock_task:
            mock_task.delay.return_value = None
            auth(api_client, superadmin)
            response = api_client.post(
                f'/api/v1/companies/building-invites/{building_invitation.id}/resend/'
            )
        assert response.status_code == status.HTTP_200_OK
        building_invitation.refresh_from_db()
        assert building_invitation.status == Invitation.STATUS_REVOKED

        new_inv = Invitation.objects.filter(
            company__isnull=True,
            email='reception@test.com',
        ).order_by('-created_at').first()
        assert new_inv is not None
        assert new_inv.pk != building_invitation.pk
        assert new_inv.status == Invitation.STATUS_PENDING

    # AC12
    def test_filter_status_accepted_for_building_invites(
        self, api_client, superadmin, building_invitation
    ):
        # Create an accepted building invite
        accepted = Invitation.objects.create(
            company=None,
            email='sm_accepted@test.com',
            invited_by=superadmin,
            role='service_manager',
            status=Invitation.STATUS_ACCEPTED,
        )
        auth(api_client, superadmin)
        response = api_client.get(
            '/api/v1/companies/building-invites/', {'status': 'accepted'}
        )
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert accepted.id in ids
        assert building_invitation.id not in ids


# ---------------------------------------------------------------------------
# Serializer output shape
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestInvitationListSerializerShape:

    def test_list_response_includes_status_field(
        self, api_client, superadmin, company, pending_invitation
    ):
        auth(api_client, superadmin)
        response = api_client.get(invite_list_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        item = next(r for r in response.data['results'] if r['id'] == pending_invitation.id)
        assert 'status' in item
        assert item['status'] == 'pending'
        # Backward-compat fields still present
        assert 'is_used' in item
        assert 'is_expired' in item
        assert 'is_valid' in item

    def test_revoked_invitation_has_correct_bool_fields(
        self, api_client, superadmin, company, pending_invitation
    ):
        pending_invitation.status = Invitation.STATUS_REVOKED
        pending_invitation.save(update_fields=['status'])

        auth(api_client, superadmin)
        response = api_client.get(invite_list_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        item = next(r for r in response.data['results'] if r['id'] == pending_invitation.id)
        assert item['status'] == 'revoked'
        assert item['is_used'] is True
        assert item['is_expired'] is False
        assert item['is_valid'] is False


# ---------------------------------------------------------------------------
# AC13 — check_expired_invitations covers building-staff (company=None) invites
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCheckExpiredTaskBuildingStaff:

    def test_task_marks_building_staff_pending_past_expiry_as_expired(self, superadmin):
        past = timezone.now() - timedelta(hours=1)
        inv = Invitation.objects.create(
            company=None,
            email='bs_expire@test.com',
            invited_by=superadmin,
            role='reception',
        )
        Invitation.objects.filter(pk=inv.pk).update(expires_at=past)

        updated = check_expired_invitations()
        assert updated >= 1
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_EXPIRED

    def test_task_does_not_touch_accepted_building_staff_invite(self, superadmin):
        past = timezone.now() - timedelta(hours=1)
        inv = Invitation.objects.create(
            company=None,
            email='bs_accepted@test.com',
            invited_by=superadmin,
            role='service_manager',
            status=Invitation.STATUS_ACCEPTED,
        )
        Invitation.objects.filter(pk=inv.pk).update(expires_at=past)

        check_expired_invitations()

        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_ACCEPTED


# ---------------------------------------------------------------------------
# AC14 — Building-staff accept via InviteRegistrationSerializer sets 'accepted'
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBuildingStaffInviteAccept:

    def test_accept_building_staff_invite_sets_status_accepted(
        self, api_client, building_invitation
    ):
        payload = {
            'token': str(building_invitation.token),
            'first_name': 'Rec',
            'last_name': 'Eptor',
            'password': 'securepass1',
        }
        response = api_client.post('/api/v1/auth/register/invite/', payload, format='json')
        assert response.status_code == status.HTTP_201_CREATED, response.data
        building_invitation.refresh_from_db()
        assert building_invitation.status == Invitation.STATUS_ACCEPTED

    def test_accept_building_staff_invite_assigns_correct_role(
        self, api_client, building_invitation
    ):
        payload = {
            'token': str(building_invitation.token),
            'first_name': 'Rec',
            'last_name': 'Eptor',
            'password': 'securepass1',
        }
        response = api_client.post('/api/v1/auth/register/invite/', payload, format='json')
        assert response.status_code == status.HTTP_201_CREATED, response.data
        user = User.objects.get(email='reception@test.com')
        assert user.role == 'reception'
        assert user.company is None

    def test_expired_building_staff_invite_cannot_be_accepted(
        self, api_client, superadmin
    ):
        inv = Invitation.objects.create(
            company=None,
            email='exp_reception@test.com',
            invited_by=superadmin,
            role='reception',
        )
        Invitation.objects.filter(pk=inv.pk).update(
            expires_at=timezone.now() - timedelta(hours=1),
        )
        inv.refresh_from_db()
        payload = {
            'token': str(inv.token),
            'first_name': 'Rec',
            'last_name': 'Eptor',
            'password': 'securepass1',
        }
        response = api_client.post('/api/v1/auth/register/invite/', payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC15 — Legacy ?is_used= / ?is_expired= filters on building-staff endpoint
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBuildingInviteLegacyFilters:

    def _seed(self, superadmin):
        """Create one building invite per key status."""
        inv_pending = Invitation.objects.create(
            company=None, email='bsp@test.com', invited_by=superadmin, role='reception',
        )
        inv_accepted = Invitation.objects.create(
            company=None, email='bsa@test.com', invited_by=superadmin, role='reception',
            status=Invitation.STATUS_ACCEPTED,
        )
        inv_revoked = Invitation.objects.create(
            company=None, email='bsr@test.com', invited_by=superadmin, role='reception',
            status=Invitation.STATUS_REVOKED,
        )
        inv_expired_db = Invitation.objects.create(
            company=None, email='bse@test.com', invited_by=superadmin, role='reception',
        )
        Invitation.objects.filter(pk=inv_expired_db.pk).update(
            expires_at=timezone.now() - timedelta(hours=1),
        )
        inv_expired_db.refresh_from_db()
        return inv_pending, inv_accepted, inv_revoked, inv_expired_db

    def test_is_used_true_returns_accepted_and_revoked(self, api_client, superadmin):
        inv_pending, inv_accepted, inv_revoked, inv_expired_db = self._seed(superadmin)
        auth(api_client, superadmin)
        response = api_client.get('/api/v1/companies/building-invites/', {'is_used': 'true'})
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert inv_accepted.id in ids
        assert inv_revoked.id in ids
        assert inv_pending.id not in ids

    def test_is_used_false_excludes_accepted_and_revoked(self, api_client, superadmin):
        inv_pending, inv_accepted, inv_revoked, inv_expired_db = self._seed(superadmin)
        auth(api_client, superadmin)
        response = api_client.get('/api/v1/companies/building-invites/', {'is_used': 'false'})
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert inv_pending.id in ids
        assert inv_accepted.id not in ids
        assert inv_revoked.id not in ids

    def test_is_expired_true_returns_pending_past_expiry(self, api_client, superadmin):
        inv_pending, inv_accepted, inv_revoked, inv_expired_db = self._seed(superadmin)
        auth(api_client, superadmin)
        response = api_client.get('/api/v1/companies/building-invites/', {'is_expired': 'true'})
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert inv_expired_db.id in ids
        assert inv_pending.id not in ids
        assert inv_accepted.id not in ids

    def test_is_expired_false_excludes_pending_past_expiry(self, api_client, superadmin):
        inv_pending, inv_accepted, inv_revoked, inv_expired_db = self._seed(superadmin)
        auth(api_client, superadmin)
        response = api_client.get('/api/v1/companies/building-invites/', {'is_expired': 'false'})
        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in response.data['results']]
        assert inv_pending.id in ids
        assert inv_expired_db.id not in ids


# ---------------------------------------------------------------------------
# AC16 — Resend already-revoked building-staff invite returns 400
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBuildingInviteResendRevoked:

    def test_resend_revoked_building_invite_returns_400(
        self, api_client, superadmin, building_invitation
    ):
        building_invitation.status = Invitation.STATUS_REVOKED
        building_invitation.save(update_fields=['status'])

        auth(api_client, superadmin)
        response = api_client.post(
            f'/api/v1/companies/building-invites/{building_invitation.id}/resend/'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_resend_accepted_building_invite_returns_400(
        self, api_client, superadmin, building_invitation
    ):
        building_invitation.status = Invitation.STATUS_ACCEPTED
        building_invitation.save(update_fields=['status'])

        auth(api_client, superadmin)
        response = api_client.post(
            f'/api/v1/companies/building-invites/{building_invitation.id}/resend/'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC17 — Building-staff list response includes 'status' field
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBuildingInviteSerializerShape:

    def test_building_invite_list_includes_status_field(
        self, api_client, superadmin, building_invitation
    ):
        auth(api_client, superadmin)
        response = api_client.get('/api/v1/companies/building-invites/')
        assert response.status_code == status.HTTP_200_OK
        item = next(
            r for r in response.data['results'] if r['id'] == building_invitation.id
        )
        assert 'status' in item
        assert item['status'] == 'pending'
        assert 'is_used' in item
        assert 'is_expired' in item
        assert 'is_valid' in item

    def test_building_invite_status_pending_bool_fields(
        self, api_client, superadmin, building_invitation
    ):
        auth(api_client, superadmin)
        response = api_client.get('/api/v1/companies/building-invites/')
        assert response.status_code == status.HTTP_200_OK
        item = next(
            r for r in response.data['results'] if r['id'] == building_invitation.id
        )
        assert item['is_used'] is False
        assert item['is_expired'] is False
        assert item['is_valid'] is True
