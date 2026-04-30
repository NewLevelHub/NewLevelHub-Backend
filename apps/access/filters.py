import django_filters

from .models import GuestPass


class GuestPassFilter(django_filters.FilterSet):
    company_id = django_filters.NumberFilter(field_name='company_id')
    company_name = django_filters.CharFilter(field_name='company__name', lookup_expr='icontains')
    created_by = django_filters.NumberFilter(field_name='created_by_id')
    created_by_email = django_filters.CharFilter(field_name='created_by__email', lookup_expr='icontains')
    status = django_filters.CharFilter(field_name='status')
    date_from = django_filters.DateFilter(field_name='created_at', lookup_expr='date__gte')
    date_to = django_filters.DateFilter(field_name='created_at', lookup_expr='date__lte')

    class Meta:
        model = GuestPass
        fields = ['company_id', 'company_name', 'created_by', 'created_by_email', 'status', 'date_from', 'date_to']
