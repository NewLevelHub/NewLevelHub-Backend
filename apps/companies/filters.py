import django_filters
from .models import Company


class CompanyFilter(django_filters.FilterSet):
    plan = django_filters.ChoiceFilter(choices=Company.PLAN_CHOICES)
    is_active = django_filters.BooleanFilter()
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = Company
        fields = ['plan', 'is_active', 'name']
