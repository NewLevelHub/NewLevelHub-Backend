from django.contrib import admin
from .models import Company, CompanySettings, Invitation


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ['name', 'plan', 'is_active', 'employee_count', 'floor', 'office_number', 'created_at']
    list_filter = ['plan', 'is_active']
    search_fields = ['name', 'contact_email']


@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    list_display = ['company', 'vacation_days_per_year', 'onboarding_enabled']


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ['email', 'company', 'role', 'invited_by', 'is_used', 'expires_at']
    list_filter = ['is_used', 'role']
    search_fields = ['email']
