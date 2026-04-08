from django.contrib.auth import get_user_model
from django.utils import timezone


User = get_user_model()


class UpdateLastActivityMiddleware:
    """
    Updates user's last_login on each authenticated request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            now = timezone.now()
            User.objects.filter(pk=user.pk).update(last_login=now)
            user.last_login = now

        return response
