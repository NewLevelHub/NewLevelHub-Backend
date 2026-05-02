import django_filters
from .models import ServiceRequest


class ServiceRequestFilter(django_filters.FilterSet):
    type = django_filters.CharFilter(field_name='request_type')

    class Meta:
        model = ServiceRequest
        fields = {
            'request_type': ['exact'],
            'status': ['exact'],
            'urgency': ['exact'],
            'floor': ['exact'],
        }
