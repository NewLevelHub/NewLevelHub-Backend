from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('passes', views.GuestPassViewSet, basename='guest-pass')
router.register('logs', views.AccessLogViewSet, basename='access-log')

urlpatterns = [
    path('', include(router.urls)),
    path('validate/', views.validate_qr, name='validate-qr'),
]
