"""Rules for company invitations when a User row may already exist (removed / deactivated)."""

from __future__ import annotations

from typing import Optional

from apps.users.models import User


def lookup_user_by_invite_email(email: str) -> Optional[User]:
    normalized = (email or '').strip().lower()
    if not normalized:
        return None
    return User.objects.filter(email__iexact=normalized).first()


def email_blocks_new_company_invitation(email: str, company) -> Optional[str]:
    """
    Return an error message if a new invitation to this company must be rejected.

    None means the email may receive an invitation (e.g. user was removed from the
    company and the row remains with company=NULL).
    """
    user = lookup_user_by_invite_email(email)
    if user is None:
        return None
    if user.company_id == company.id and user.is_active:
        return 'User with this email is already a member of this company.'
    if user.company_id == company.id and not user.is_active:
        return (
            'A user with this email still belongs to this company but is deactivated. '
            'Reactivate their account or remove them before sending a new invitation.'
        )
    if user.company_id is not None and user.company_id != company.id and user.is_active:
        return 'User with this email is already an active member of another company.'
    return None


def existing_user_cannot_accept_invite_error(user: User, invitation) -> Optional[str]:
    """
    Invite registration: a User already exists for the invitation email.

    Return an error string if registration must fail; None if the user may re-join
    via this invitation (update existing row).
    """
    if user.company_id == invitation.company_id and user.is_active:
        return 'A user with this email is already registered.'
    if user.company_id is not None and user.company_id != invitation.company_id and user.is_active:
        return 'This account belongs to another organization. Log out and use the correct account.'
    return None
