from apps.users.session import should_track_activity, touch_last_activity


class UpdateLastActivityMiddleware:
    """
    Updates user's last_login after each successful authenticated request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        user = getattr(request, 'user', None)
        if (
            user
            and user.is_authenticated
            and response.status_code < 400
            and should_track_activity(request)
        ):
            touch_last_activity(user)

        return response
