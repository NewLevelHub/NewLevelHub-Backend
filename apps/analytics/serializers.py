from rest_framework import serializers


class SuperAdminOverviewSerializer(serializers.Serializer):
    total_companies = serializers.IntegerField()
    active_companies = serializers.IntegerField()
    total_users = serializers.IntegerField()
    active_users_7d = serializers.IntegerField()
    bookings_today = serializers.IntegerField()
    guests_today = serializers.IntegerField()
    open_service_requests = serializers.IntegerField()


class SuperAdminDashboardSerializer(serializers.Serializer):
    """Superadmin analytics overview: period metadata plus nested counters."""

    period = serializers.CharField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    overview = SuperAdminOverviewSerializer()


class CompanyAdminDashboardSerializer(serializers.Serializer):
    employee_count = serializers.IntegerField()
    active_employees_7d = serializers.IntegerField()
    bookings_this_month = serializers.IntegerField()
    storage_used_bytes = serializers.IntegerField()
    storage_limit_bytes = serializers.IntegerField()
    active_tasks = serializers.IntegerField()
    guest_visits_this_month = serializers.IntegerField()


class ResourceUsageSerializer(serializers.Serializer):
    resource_type = serializers.CharField()
    total_bookings = serializers.IntegerField()
    avg_duration_minutes = serializers.FloatField()


class DateCountSerializer(serializers.Serializer):
    date = serializers.DateField()
    count = serializers.IntegerField()
