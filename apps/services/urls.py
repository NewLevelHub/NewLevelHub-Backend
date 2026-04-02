from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('floors', views.FloorViewSet, basename='floor')
router.register('map-points', views.MapPointViewSet, basename='map-point')
router.register('requests', views.ServiceRequestViewSet, basename='service-request')
router.register('announcements', views.AnnouncementViewSet, basename='announcement')

urlpatterns = [
    path('', include(router.urls)),
]
