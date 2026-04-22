from django.contrib import admin
from .models import LeaveRequest, LeaveBalance, OnboardingTemplate, OnboardingStep, UserOnboardingProgress


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ['user', 'leave_type', 'status', 'start_date', 'end_date', 'company']
    list_filter = ['status', 'leave_type']


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ['user', 'year', 'total_days', 'used_days', 'remaining_days']


class OnboardingStepInline(admin.TabularInline):
    model = OnboardingStep
    extra = 1


@admin.register(OnboardingTemplate)
class OnboardingTemplateAdmin(admin.ModelAdmin):
    list_display = ['title', 'company', 'is_active']
    inlines = [OnboardingStepInline]


@admin.register(UserOnboardingProgress)
class UserOnboardingProgressAdmin(admin.ModelAdmin):
    list_display = ['user', 'step', 'is_completed', 'completed_at']
