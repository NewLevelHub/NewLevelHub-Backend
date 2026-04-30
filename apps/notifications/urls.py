from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('', views.NotificationViewSet, basename='notification')

urlpatterns = [
    path('preferences/', views.notification_preferences, name='notification-preferences'),
    path('do-not-disturb/', views.do_not_disturb, name='notification-do-not-disturb'),
    path('', include(router.urls)),
]
