"""
URL configuration for NewLevelHub project.
"""
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)
from apps.core.views import (
    build_info,
    ping,
    server_time,
    system_environment,
    system_features,
    system_uptime_note,
)

urlpatterns = [
    # Admin
    path('admin/', admin.site.urls),

    # API v1
    path('api/v1/', include([
        path('auth/', include('apps.users.urls')),
        path('health/', include('apps.core.urls')),
        path('ping/', ping, name='ping'),
        path('time/', server_time, name='server-time'),
        path('build-info/', build_info, name='build-info'),
        path('system/features/', system_features, name='system-features'),
        path('system/environment/', system_environment, name='system-environment'),
        path('system/uptime-note/', system_uptime_note, name='system-uptime-note'),
        # path('bookings/', include('apps.booking.urls')),
        # path('crm/', include('apps.crm.urls')),
        # path('iot/', include('apps.iot.urls')),
    ])),

    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]
