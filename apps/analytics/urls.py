from django.urls import path
from . import views

urlpatterns = [
    path('superadmin/export/', views.SuperadminExportView.as_view(), name='analytics-superadmin-export'),
    path('company/export/', views.CompanyExportView.as_view(), name='analytics-company-export'),
    path('superadmin/', views.superadmin_dashboard, name='analytics-superadmin'),
    path('company/', views.company_dashboard, name='analytics-company'),
    path('resources/', views.resource_usage, name='analytics-resources'),
]
