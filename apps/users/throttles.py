from rest_framework.throttling import SimpleRateThrottle


class PasswordResetRateThrottle(SimpleRateThrottle):
    """5 password-reset requests per 15 minutes per IP."""

    scope = 'password_reset'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }

    def get_rate(self):
        from django.conf import settings
        return getattr(settings, 'PASSWORD_RESET_THROTTLE_RATE', '5/15min')

    def parse_rate(self, rate):
        if rate is None:
            return None, None
        num, period = rate.split('/')
        num_requests = int(num)
        if 'min' in period:
            minutes = int(period.replace('min', '').strip() or '1')
            duration = minutes * 60
        else:
            duration = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}.get(period[0], 60)
        return num_requests, duration
