import django_filters
from .models import ServiceRequest


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
