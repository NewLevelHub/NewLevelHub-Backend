import django_filters

from .models import AccessLog, GuestPass


class AccessLogFilter(django_filters.FilterSet):
    company_id = django_filters.NumberFilter(field_name='guest_pass__company_id')
    date_from = django_filters.DateFilter(field_name='created_at', lookup_expr='date__gte')
    date_to = django_filters.DateFilter(field_name='created_at', lookup_expr='date__lte')

    class Meta:
        model = AccessLog
        fields = ['company_id', 'date_from', 'date_to', 'method', 'is_entry']


# Filters for GuestPass list endpoint: supports company, date range and status lookups.
class GuestPassFilter(django_filters.FilterSet):
    company_id = django_filters.NumberFilter(field_name='company_id')
    company_name = django_filters.CharFilter(field_name='company__name', lookup_expr='icontains')
    created_by = django_filters.NumberFilter(field_name='created_by_id')
    created_by_email = django_filters.CharFilter(field_name='created_by__email', lookup_expr='icontains')
    guest_name = django_filters.CharFilter(field_name='guest_name', lookup_expr='icontains')
    status = django_filters.CharFilter(field_name='status')
    date_from = django_filters.DateFilter(field_name='created_at', lookup_expr='date__gte')
    date_to = django_filters.DateFilter(field_name='created_at', lookup_expr='date__lte')
    valid_from_after = django_filters.DateFilter(field_name='valid_from', lookup_expr='date__gte')
    valid_from_before = django_filters.DateFilter(field_name='valid_from', lookup_expr='date__lte')

    class Meta:
        model = GuestPass
        fields = [
            'company_id', 'company_name', 'created_by', 'created_by_email', 'guest_name',
            'status', 'date_from', 'date_to', 'valid_from_after', 'valid_from_before',
        ]
