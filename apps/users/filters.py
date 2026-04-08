import django_filters

from .models import User


class UserFilter(django_filters.FilterSet):
    company_id = django_filters.NumberFilter(field_name='company_id')

    class Meta:
        model = User
        fields = ['role', 'company_id', 'is_active']
