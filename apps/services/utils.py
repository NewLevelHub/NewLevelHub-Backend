"""Shared utilities for the services app."""

from django.db.models import Case, Count, FloatField, IntegerField, Q, Value, When
from django.db.models.functions import Cast
from django.utils import timezone


def annotate_floor_occupancy(qs):
    """
    Annotate a Floor queryset with occupancy stats based on Resource.floor_fk.

    Adds three attributes to each Floor instance:
      - total_resources: count of active resources linked via floor_fk
      - booked_now:      count of those resources with a confirmed booking active right now
      - occupancy_pct:   integer 0–100, booked_now / total_resources * 100 (0 when no resources)

    This is the single source of truth for floor occupancy — used by both
    FloorViewSet (floor list API) and the superadmin dashboard floor_load widget
    so they always produce identical numbers.
    """
    now = timezone.now()
    return (
        qs
        .annotate(
            total_resources=Count(
                'resources',
                filter=Q(resources__is_active=True),
                distinct=True,
            ),
            booked_now=Count(
                'resources__bookings',
                filter=Q(
                    resources__is_active=True,
                    resources__bookings__status='confirmed',
                    resources__bookings__start_time__lte=now,
                    resources__bookings__end_time__gte=now,
                ),
                distinct=True,
            ),
        )
        .annotate(
            occupancy_pct=Case(
                When(total_resources=0, then=Value(0)),
                default=Cast(
                    Cast('booked_now', output_field=FloatField())
                    / Cast('total_resources', output_field=FloatField())
                    * Value(100.0),
                    output_field=IntegerField(),
                ),
                output_field=IntegerField(),
            ),
        )
    )
