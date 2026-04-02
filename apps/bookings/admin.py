from django.contrib import admin
from .models import Resource, Booking, BookingParticipant, RecurringBooking, ResourceBlock


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'resource_type', 'floor', 'capacity', 'is_active', 'assigned_company']
    list_filter = ['resource_type', 'floor', 'is_active']
    search_fields = ['name']


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['resource', 'user', 'start_time', 'end_time', 'status', 'company']
    list_filter = ['status', 'resource__resource_type']
    search_fields = ['user__email', 'resource__name']
    date_hierarchy = 'start_time'


@admin.register(BookingParticipant)
class BookingParticipantAdmin(admin.ModelAdmin):
    list_display = ['booking', 'user']


@admin.register(RecurringBooking)
class RecurringBookingAdmin(admin.ModelAdmin):
    list_display = ['resource', 'user', 'day_of_week', 'start_time', 'end_time', 'is_active']
    list_filter = ['is_active', 'day_of_week']


@admin.register(ResourceBlock)
class ResourceBlockAdmin(admin.ModelAdmin):
    list_display = ['resource', 'blocked_by', 'start_time', 'end_time', 'reason']
