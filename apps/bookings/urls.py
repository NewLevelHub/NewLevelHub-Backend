from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('resources', views.ResourceViewSet, basename='resource')
router.register('reservations', views.BookingViewSet, basename='booking')
router.register('recurring', views.RecurringBookingViewSet, basename='recurring-booking')

urlpatterns = [
    path('', include(router.urls)),
]
