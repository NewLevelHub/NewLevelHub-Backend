"""
Unit / integration tests for the check_expired_invitations Celery task.

Acceptance criteria verified here:
  - Task returns the count of records updated.
  - Pending invitations whose expires_at is in the past are set to 'expired'.
  - Pending invitations whose expires_at is in the future are left untouched.
  - Already-accepted or already-revoked invitations are never changed, even when
    their expires_at is also in the past.
  - Already-expired invitations (status already 'expired') are not double-counted.
  - Building-staff invitations (company=None) are handled identically to
    company-scoped invitations.
  - Running the task multiple times is idempotent (no extra rows flipped).
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.companies.models import Company, Invitation
from apps.companies.tasks import check_expired_invitations
from apps.users.models import User


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='task_sa@test.com',
        password='pass1234',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
    )


@pytest.fixture
def company(db):
    return Company.objects.create(name='Expiry Task Corp', plan='basic')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='task_ca@test.com',
        password='pass1234',
        first_name='Carol',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


def _make_invitation(invited_by, *, company=None, email='inv@test.com',
                     role='employee', status=Invitation.STATUS_PENDING,
                     expires_offset_hours=72):
    """Helper: create an Invitation and then force-set expires_at via ORM update."""
    inv = Invitation.objects.create(
        company=company,
        email=email,
        invited_by=invited_by,
        role=role,
        status=status,
    )
    expires_at = timezone.now() + timedelta(hours=expires_offset_hours)
    Invitation.objects.filter(pk=inv.pk).update(expires_at=expires_at)
    inv.refresh_from_db()
    return inv


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCheckExpiredInvitationsTask:

    def test_returns_count_includes_newly_overdue_invitations(self, company, company_admin):
        """Task return value equals the number of rows transitioned to 'expired'.

        We drain pre-existing dirty rows first so the assertion is not sensitive
        to leftover data in the test DB from other test modules.
        """
        # Drain any pre-existing overdue-pending rows so the delta below is exactly 3.
        check_expired_invitations()

        invitations = [
            _make_invitation(
                company_admin, company=company,
                email=f'batch_count_{i}@test.com',
                expires_offset_hours=-1,
            )
            for i in range(3)
        ]
        result = check_expired_invitations()
        # result must be at least 3 (our rows); previous runs may have left rows but
        # those were already expired by the `before` call above, so delta == 3.
        assert result == 3
        # Every invitation we created must now be expired.
        for inv in invitations:
            inv.refresh_from_db()
            assert inv.status == Invitation.STATUS_EXPIRED

    def test_pending_past_expiry_becomes_expired(self, company, company_admin):
        """Core happy-path: a single overdue pending invitation is transitioned."""
        inv = _make_invitation(
            company_admin, company=company,
            email='overdue@test.com',
            expires_offset_hours=-1,
        )
        check_expired_invitations()
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_EXPIRED

    def test_pending_future_expiry_is_not_touched(self, company, company_admin):
        """Invitations that have not yet expired must remain pending."""
        inv = _make_invitation(
            company_admin, company=company,
            email='fresh@test.com',
            expires_offset_hours=48,
        )
        check_expired_invitations()
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_PENDING

    def test_only_future_pending_means_zero_new_expirations(self, company, company_admin):
        """When only a not-yet-expired pending invitation exists, the task changes nothing.

        We drain any pre-existing overdue rows first, then add a future-expiry one
        and verify no additional rows are changed.
        """
        # Drain any pre-existing dirty rows so the baseline is 0.
        check_expired_invitations()

        inv = _make_invitation(
            company_admin, company=company,
            email='noop_future@test.com',
            expires_offset_hours=48,
        )
        result = check_expired_invitations()
        assert result == 0
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_PENDING

    def test_task_safe_when_no_overdue_rows_remain(self, company, company_admin):
        """Task returns 0 when there are no pending+overdue invitations.

        Drain dirty rows first, then run again to confirm a clean 0 return.
        """
        # Drain any pre-existing dirty rows.
        check_expired_invitations()
        # Only a future invitation exists now.
        _make_invitation(
            company_admin, company=company,
            email='safe_zero@test.com',
            expires_offset_hours=48,
        )
        result = check_expired_invitations()
        assert result == 0


# ---------------------------------------------------------------------------
# Status isolation — other statuses must not be modified
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskDoesNotTouchNonPendingInvitations:

    def test_accepted_invitation_is_not_changed(self, company, company_admin):
        inv = _make_invitation(
            company_admin, company=company,
            email='accepted@test.com',
            status=Invitation.STATUS_ACCEPTED,
            expires_offset_hours=-1,
        )
        check_expired_invitations()
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_ACCEPTED

    def test_revoked_invitation_is_not_changed(self, company, company_admin):
        inv = _make_invitation(
            company_admin, company=company,
            email='revoked@test.com',
            status=Invitation.STATUS_REVOKED,
            expires_offset_hours=-1,
        )
        check_expired_invitations()
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_REVOKED

    def test_already_expired_invitation_is_not_recounted(self, company, company_admin):
        """An invitation already marked 'expired' should not appear in the updated count."""
        # Drain any pre-existing overdue pending rows so the baseline is 0.
        check_expired_invitations()

        inv = _make_invitation(
            company_admin, company=company,
            email='already_expired@test.com',
            status=Invitation.STATUS_EXPIRED,
            expires_offset_hours=-1,
        )
        result = check_expired_invitations()
        # The already-expired row has status != 'pending', so it is excluded from the
        # filter and must not be counted.
        assert result == 0
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_EXPIRED


# ---------------------------------------------------------------------------
# Building-staff invitations (company=None)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskWithBuildingStaffInvitations:

    def test_building_staff_pending_past_expiry_becomes_expired(self, superadmin):
        """Building-staff invitations (company=None) are treated identically."""
        inv = _make_invitation(
            superadmin, company=None,
            email='bs_overdue@test.com',
            role='reception',
            expires_offset_hours=-1,
        )
        check_expired_invitations()
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_EXPIRED

    def test_building_staff_future_invite_is_not_touched(self, superadmin):
        inv = _make_invitation(
            superadmin, company=None,
            email='bs_fresh@test.com',
            role='service_manager',
            expires_offset_hours=48,
        )
        check_expired_invitations()
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_PENDING

    def test_building_staff_accepted_is_not_changed(self, superadmin):
        inv = _make_invitation(
            superadmin, company=None,
            email='bs_accepted@test.com',
            role='reception',
            status=Invitation.STATUS_ACCEPTED,
            expires_offset_hours=-1,
        )
        check_expired_invitations()
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_ACCEPTED

    def test_mixed_company_and_building_staff_all_expired_correctly(
        self, superadmin, company, company_admin
    ):
        """Both company-scoped and building-staff overdue invites are updated in one run."""
        # Drain any pre-existing overdue rows so our delta == 2.
        check_expired_invitations()

        company_inv = _make_invitation(
            company_admin, company=company,
            email='c_overdue@test.com',
            expires_offset_hours=-1,
        )
        bs_inv = _make_invitation(
            superadmin, company=None,
            email='bs_overdue2@test.com',
            role='reception',
            expires_offset_hours=-1,
        )
        result = check_expired_invitations()
        assert result == 2
        company_inv.refresh_from_db()
        bs_inv.refresh_from_db()
        assert company_inv.status == Invitation.STATUS_EXPIRED
        assert bs_inv.status == Invitation.STATUS_EXPIRED


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskIdempotency:

    def test_running_task_twice_is_idempotent(self, company, company_admin):
        """Second run sees no pending+overdue rows, returns 0.

        We drain pre-existing dirty rows before creating our own invitation,
        so the first run returns exactly 1 and the second returns 0.
        """
        # Drain any pre-existing dirty rows.
        check_expired_invitations()

        inv = _make_invitation(
            company_admin, company=company,
            email='idempotent@test.com',
            expires_offset_hours=-1,
        )
        first_result = check_expired_invitations()
        second_result = check_expired_invitations()
        assert first_result == 1
        assert second_result == 0
        inv.refresh_from_db()
        assert inv.status == Invitation.STATUS_EXPIRED
