import django_filters
from django.db.models import Q
from django.utils import timezone

from apps.users.models import User
from .models import Company, Invitation


class CompanyFilter(django_filters.FilterSet):
    plan = django_filters.ChoiceFilter(choices=Company.PLAN_CHOICES)
    is_active = django_filters.BooleanFilter()
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = Company
        fields = ['plan', 'is_active', 'name']


class InvitationFilter(django_filters.FilterSet):
    is_used = django_filters.BooleanFilter()
    is_expired = django_filters.BooleanFilter(method='filter_is_expired')

    class Meta:
        model = Invitation
        fields = ['is_used']

    def filter_is_expired(self, queryset, name, value):
        now = timezone.now()
        if value:
            return queryset.filter(expires_at__lt=now)
        return queryset.filter(expires_at__gte=now)


class CompanyMemberFilter(django_filters.FilterSet):
    role = django_filters.ChoiceFilter(choices=User.ROLE_CHOICES)
    is_active = django_filters.BooleanFilter()
    search = django_filters.CharFilter(method='filter_search')

    class Meta:
        model = User
        fields = ['role', 'is_active']

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(email__icontains=value)
            | Q(first_name__icontains=value)
            | Q(last_name__icontains=value)
        )
