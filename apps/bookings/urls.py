from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('resources', views.ResourceViewSet, basename='resource')
router.register('reservations', views.BookingViewSet, basename='booking')
router.register('recurring', views.RecurringBookingViewSet, basename='recurring-booking')
router.register('cancellation-audit', views.BookingCancellationAuditViewSet, basename='cancellation-audit')

urlpatterns = [
    path('', include(router.urls)),
    path('members/', views.BookingMembersView.as_view(), name='booking-members'),
]
