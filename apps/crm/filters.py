from datetime import date, timedelta

import django_filters

from .models import Task


class TaskFilter(django_filters.FilterSet):
    board_id = django_filters.NumberFilter(field_name='column__board_id')
    column_id = django_filters.NumberFilter(field_name='column_id')
    assignee_id = django_filters.NumberFilter(field_name='assignee_id')
    priority = django_filters.CharFilter(field_name='priority')
    label_ids = django_filters.BaseInFilter(field_name='labels__id', lookup_expr='in')
    deadline_from = django_filters.DateFilter(field_name='deadline', lookup_expr='date__gte')
    deadline_to = django_filters.DateFilter(field_name='deadline', lookup_expr='date__lte')
    deadline = django_filters.CharFilter(method='filter_deadline')
    search = django_filters.CharFilter(field_name='title', lookup_expr='icontains')

    class Meta:
        model = Task
        fields = [
            'board_id', 'column_id', 'assignee_id', 'priority',
            'label_ids', 'deadline_from', 'deadline_to', 'deadline', 'search',
        ]

    def filter_deadline(self, queryset, name, value):
        today = date.today()
        if value == 'overdue':
            return queryset.filter(deadline__date__lt=today, is_archived=False)
        elif value == 'today':
            return queryset.filter(deadline__date=today)
        elif value == 'this_week':
            return queryset.filter(
                deadline__date__gte=today,
                deadline__date__lte=today + timedelta(days=7),
            )
        return queryset
