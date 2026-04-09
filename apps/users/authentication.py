def active_user_authentication_rule(user):
    """
    Reject JWT authentication for inactive users.
    """
    return bool(user is not None and user.is_active)
