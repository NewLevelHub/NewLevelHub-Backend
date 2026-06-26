import django_filters
from django.db import models
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
    is_used = django_filters.BooleanFilter(method='filter_is_used')
    is_expired = django_filters.BooleanFilter(method='filter_is_expired')
    status = django_filters.ChoiceFilter(choices=Invitation.STATUS_CHOICES)

    class Meta:
        model = Invitation
        fields = ['status']

    def filter_is_used(self, queryset, name, value):
        if value:
            return queryset.filter(status__in=[Invitation.STATUS_ACCEPTED, Invitation.STATUS_REVOKED])
        return queryset.exclude(status__in=[Invitation.STATUS_ACCEPTED, Invitation.STATUS_REVOKED])

    def filter_is_expired(self, queryset, name, value):
        now = timezone.now()
        if value:
            # Expired status set explicitly, OR pending past expiry
            return queryset.filter(
                models.Q(status=Invitation.STATUS_EXPIRED)
                | models.Q(status=Invitation.STATUS_PENDING, expires_at__lt=now)
            )
        return queryset.exclude(
            models.Q(status=Invitation.STATUS_EXPIRED)
            | models.Q(status=Invitation.STATUS_PENDING, expires_at__lt=now)
        )


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


class CompanyDirectoryFilter(django_filters.FilterSet):
    role = django_filters.ChoiceFilter(choices=User.ROLE_CHOICES)
    position = django_filters.CharFilter(lookup_expr='icontains')
    search = django_filters.CharFilter(method='filter_search')

    class Meta:
        model = User
        fields = ['role', 'position']

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(email__icontains=value)
            | Q(first_name__icontains=value)
            | Q(last_name__icontains=value)
        )
