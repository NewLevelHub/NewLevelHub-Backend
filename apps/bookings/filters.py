import django_filters
from .models import Resource, Booking
from .serializers import _EQUIPMENT_KEYS


class ResourceFilter(django_filters.FilterSet):
    """Каталог: type и resource_type — одно поле модели; equipment — ключи как в карточке переговорки."""

    type = django_filters.CharFilter(field_name='resource_type')
    resource_type = django_filters.CharFilter()
    floor = django_filters.NumberFilter()
    capacity_min = django_filters.NumberFilter(field_name='capacity', lookup_expr='gte')
    capacity_max = django_filters.NumberFilter(field_name='capacity', lookup_expr='lte')
    equipment = django_filters.CharFilter(method='filter_equipment')
    has_projector = django_filters.BooleanFilter()
    has_tv = django_filters.BooleanFilter()
    has_video_conf = django_filters.BooleanFilter()
    is_active = django_filters.BooleanFilter()

    class Meta:
        model = Resource
        fields = ['resource_type', 'floor', 'is_active']

    def filter_equipment(self, queryset, name, value):
        if value in (None, ''):
            return queryset
        raw = value if isinstance(value, (list, tuple)) else [value]
        tokens = []
        for part in raw:
            tokens.extend(str(part).replace(',', ' ').split())
        for token in tokens:
            key = token.strip().lower().replace('-', '_')
            field = _EQUIPMENT_KEYS.get(key)
            if field:
                queryset = queryset.filter(**{field: True})
        return queryset


class BookingFilter(django_filters.FilterSet):
    status = django_filters.CharFilter()
    resource_type = django_filters.CharFilter(field_name='resource__resource_type')
    date_from = django_filters.DateTimeFilter(field_name='start_time', lookup_expr='gte')
    date_to = django_filters.DateTimeFilter(field_name='end_time', lookup_expr='lte')
    company = django_filters.NumberFilter(field_name='company_id')

    class Meta:
        model = Booking
        fields = ['status', 'resource', 'user', 'company']
