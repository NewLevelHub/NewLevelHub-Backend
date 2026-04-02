from django.contrib import admin
from .models import GuestPass, AccessLog


@admin.register(GuestPass)
class GuestPassAdmin(admin.ModelAdmin):
    list_display = ['guest_name', 'guest_email', 'status', 'created_by', 'company', 'valid_from', 'valid_until']
    list_filter = ['status', 'usage_type']
    search_fields = ['guest_name', 'guest_email']


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    list_display = ['guest_pass', 'user', 'checked_by', 'method', 'is_entry', 'created_at']
    list_filter = ['method', 'is_entry']
    date_hierarchy = 'created_at'
