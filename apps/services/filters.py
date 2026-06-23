import django_filters
from .models import Announcement, ServiceRequest


class ServiceRequestFilter(django_filters.FilterSet):
    type = django_filters.CharFilter(field_name='request_type')
    # Alias: ?company=<id> filters by company_id.
    # Only has an effect for superadmin and service_manager whose querysets are
    # not already scoped to a single company.  For other roles the queryset is
    # pre-filtered by get_queryset() so this parameter is silently ignored.
    company = django_filters.NumberFilter(field_name='company_id')

    class Meta:
        model = ServiceRequest
        fields = {
            'request_type': ['exact'],
            'status': ['exact'],
            'urgency': ['exact'],
            'floor': ['exact'],
        }


class AnnouncementFilter(django_filters.FilterSet):
    """
    Filter set for the announcement feed.

    ``scope`` — exact match on the derived ``scope`` field:
      * ``building`` — building-wide announcements (company IS NULL)
      * ``company``  — company-internal announcements (company IS NOT NULL)

    ``category`` and ``is_pinned`` are direct field filters.
    """

    scope = django_filters.ChoiceFilter(choices=Announcement.SCOPE_CHOICES)

    class Meta:
        model = Announcement
        fields = ['scope', 'category', 'is_pinned']
