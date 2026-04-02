"""URL configuration for NewLevelHub project."""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

urlpatterns = [
    path('admin/', admin.site.urls),

    # API v1
    path('api/v1/', include([
        path('', include('apps.core.urls')),
        path('auth/', include('apps.users.urls')),
        path('companies/', include('apps.companies.urls')),
        path('bookings/', include('apps.bookings.urls')),
        path('crm/', include('apps.crm.urls')),
        path('storage/', include('apps.storage.urls')),
        path('hr/', include('apps.hr.urls')),
        path('access/', include('apps.access.urls')),
        path('services/', include('apps.services.urls')),
        path('notifications/', include('apps.notifications.urls')),
        path('analytics/', include('apps.analytics.urls')),
    ])),

    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
