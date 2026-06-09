from rest_framework import serializers


class SuperAdminOverviewSerializer(serializers.Serializer):
    total_companies = serializers.IntegerField()
    active_companies = serializers.IntegerField()
    total_users = serializers.IntegerField()
    active_users_7d = serializers.IntegerField()
    bookings_today = serializers.IntegerField()
    guests_today = serializers.IntegerField()
    open_service_requests = serializers.IntegerField()


class ResourceUtilizationSerializer(serializers.Serializer):
    date = serializers.DateField()
    desk_bookings = serializers.IntegerField()
    room_bookings = serializers.IntegerField()
    parking_bookings = serializers.IntegerField()
    capsule_bookings = serializers.IntegerField()


class PeakHourSerializer(serializers.Serializer):
    day_of_week = serializers.IntegerField()
    hour = serializers.IntegerField()
    booking_count = serializers.IntegerField()


class WeekCountSerializer(serializers.Serializer):
    week = serializers.CharField()
    count = serializers.IntegerField()


class TypeCountSerializer(serializers.Serializer):
    type = serializers.CharField()
    count = serializers.IntegerField()


class TopResourceSerializer(serializers.Serializer):
    resource_id = serializers.IntegerField()
    name = serializers.CharField()
    resource_type = serializers.CharField()
    booking_count = serializers.IntegerField()


class TopCompanySerializer(serializers.Serializer):
    company_id = serializers.IntegerField()
    company_name = serializers.CharField()
    booking_count = serializers.IntegerField()


class LowUtilizationSerializer(serializers.Serializer):
    resource_id = serializers.IntegerField()
    name = serializers.CharField()
    resource_type = serializers.CharField()
    booking_count = serializers.IntegerField()
    utilization_percent = serializers.FloatField()


class SuperAdminDashboardSerializer(serializers.Serializer):
    """Superadmin analytics overview: period metadata plus nested counters."""

    period = serializers.CharField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    overview = SuperAdminOverviewSerializer()
    resource_utilization = ResourceUtilizationSerializer(many=True)
    peak_hours = PeakHourSerializer(many=True)
    new_registrations = WeekCountSerializer(many=True)
    service_requests_by_type = TypeCountSerializer(many=True)
    top_resources = TopResourceSerializer(many=True)
    top_companies = TopCompanySerializer(many=True)
    low_utilization = LowUtilizationSerializer(many=True)


class CompanyAdminDashboardSerializer(serializers.Serializer):
    employee_count = serializers.IntegerField()
    active_employees_7d = serializers.IntegerField()
    bookings_this_month = serializers.IntegerField()
    storage_used_bytes = serializers.IntegerField()
    storage_limit_bytes = serializers.IntegerField()
    active_tasks = serializers.IntegerField()
    guest_visits_this_month = serializers.IntegerField()


class CompanyStorageSerializer(serializers.Serializer):
    used = serializers.IntegerField()
    limit = serializers.IntegerField()


class CrmColumnTaskCountSerializer(serializers.Serializer):
    column_id = serializers.IntegerField()
    name = serializers.CharField()
    board_name = serializers.CharField()
    count = serializers.IntegerField()


class ActiveCrmTasksSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    todo = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    done = serializers.IntegerField()
    other = serializers.IntegerField()
    by_column = CrmColumnTaskCountSerializer(many=True)


class EmployeeActivitySerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    full_name = serializers.CharField()
    booking_count_30d = serializers.IntegerField()
    task_count_active = serializers.IntegerField()
    last_login = serializers.DateTimeField(allow_null=True)


class PendingLeaveSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    employee_name = serializers.CharField()
    leave_type = serializers.CharField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    created_at = serializers.DateTimeField()


class PendingGuestPassSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    guest_name = serializers.CharField()
    host_name = serializers.CharField()
    visit_date = serializers.DateTimeField()
    created_at = serializers.DateTimeField()


class PendingApprovalsSerializer(serializers.Serializer):
    leaves = PendingLeaveSerializer(many=True)
    guest_passes = PendingGuestPassSerializer(many=True)


class CompanyAnalyticsSerializer(serializers.Serializer):
    total_employees = serializers.IntegerField()
    active_7d = serializers.IntegerField()
    bookings_month = serializers.IntegerField()
    storage = CompanyStorageSerializer()
    active_crm_tasks = ActiveCrmTasksSerializer()
    guest_visits_month = serializers.IntegerField()
    employee_activity = EmployeeActivitySerializer(many=True)
    pending_approvals = PendingApprovalsSerializer()


class ResourceUsageRowSerializer(serializers.Serializer):
    resource_id = serializers.IntegerField()
    resource_name = serializers.CharField()
    resource_type = serializers.CharField()
    floor = serializers.IntegerField()
    total_bookings = serializers.IntegerField()
    avg_duration_minutes = serializers.FloatField()
    total_booked_minutes = serializers.FloatField()
    peak_hour = serializers.IntegerField(allow_null=True)
    peak_hour_bookings = serializers.IntegerField()


class ResourceUsageSerializer(serializers.Serializer):
    period = serializers.CharField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    results = ResourceUsageRowSerializer(many=True)


class DateCountSerializer(serializers.Serializer):
    date = serializers.DateField()
    count = serializers.IntegerField()
