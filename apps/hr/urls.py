from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('leaves', views.LeaveRequestViewSet, basename='leave-request')
router.register('onboarding/templates', views.OnboardingTemplateViewSet, basename='onboarding-template')
router.register('onboarding/progress', views.UserOnboardingProgressViewSet, basename='onboarding-progress')

urlpatterns = [
    path('', include(router.urls)),
]
