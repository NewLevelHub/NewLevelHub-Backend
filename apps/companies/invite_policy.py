"""Rules for company invitations when a User row may already exist (removed / deactivated)."""

from __future__ import annotations

from typing import Optional, List

from apps.users.models import User


def _i18n(key: str, params: dict = None) -> List[dict]:
    """Return an _i18n-marked error list suitable for raise serializers.ValidationError(...)."""
    return [{'_i18n': True, 'key': key, 'params': params or {}}]


def lookup_user_by_invite_email(email: str) -> Optional[User]:
    normalized = (email or '').strip().lower()
    if not normalized:
        return None
    return User.objects.filter(email__iexact=normalized).first()


def email_blocks_new_company_invitation(email: str, company) -> Optional[List[dict]]:
    """
    Return an _i18n error list if a new invitation to this company must be rejected.

    None means the email may receive an invitation (e.g. user was removed from the
    company and the row remains with company=NULL, or the user is a guest being
    upgraded to a company role).
    """
    user = lookup_user_by_invite_email(email)
    if user is None:
        return None
    # Guests can always be invited — the invite upgrades them to a company role.
    if user.role == 'guest':
        return None
    if user.company_id == company.id and user.is_active:
        return _i18n('company.invite_email_already_member')
    if user.company_id == company.id and not user.is_active:
        return _i18n('company.invite_email_member_deactivated')
    if user.company_id is not None and user.company_id != company.id and user.is_active:
        return _i18n('company.invite_email_in_another_company')
    return None


def existing_user_cannot_accept_invite_error(user: User, invitation) -> Optional[List[dict]]:
    """
    Invite registration: a User already exists for the invitation email.

    Return an _i18n error list if registration must fail; None if the user may re-join
    via this invitation (update existing row).
    """
    # Guests can accept invites — the invite upgrades them to a company role.
    if user.role == 'guest':
        return None
    if user.company_id == invitation.company_id and user.is_active:
        return _i18n('users.email_already_registered')
    if user.company_id is not None and user.company_id != invitation.company_id and user.is_active:
        return _i18n('company.invite_email_registered_other_org')
    if user.company_id is None and user.is_active:
        return _i18n('users.email_already_registered')
    return None
