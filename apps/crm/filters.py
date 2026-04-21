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

    class Meta:
        model = Task
        fields = ['board_id', 'column_id', 'assignee_id', 'priority', 'label_ids', 'deadline_from', 'deadline_to']
