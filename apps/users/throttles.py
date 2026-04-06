from rest_framework.throttling import SimpleRateThrottle


class EmailResendThrottle(SimpleRateThrottle):
    """3 requests per 10 minutes, keyed by authenticated user."""

    scope = 'email_resend'
    rate = '3/600'

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return self.cache_format % {
                'scope': self.scope,
                'ident': request.user.pk,
            }
        return None

    def parse_rate(self, rate):
        num, period = rate.split('/')
        return (int(num), int(period))
