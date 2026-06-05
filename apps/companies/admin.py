from django.contrib import admin
from django import forms
from .models import Company, CompanySettings, Invitation


class CompanyAdminForm(forms.ModelForm):
    storage_limit_gb = forms.DecimalField(
        max_digits=8,
        decimal_places=3,
        min_value=0,
        label='Storage limit (GB, e.g. 0.1 = 100 MB)',
        help_text='Enter storage limit in gigabytes. Use decimals for sub-GB values, e.g. 0.1 for 100 MB.',
    )

    class Meta:
        model = Company
        fields = '__all__'


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    form = CompanyAdminForm
    list_display = ['name', 'plan', 'is_active', 'employee_count', 'storage_limit_gb', 'floor', 'office_number',
                    'created_at']
    list_filter = ['plan', 'is_active']
    search_fields = ['name']


@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    list_display = ['company', 'vacation_days_per_year', 'onboarding_enabled']


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ['email', 'company', 'role', 'invited_by', 'is_used', 'expires_at']
    list_filter = ['is_used', 'role']
    search_fields = ['email']
