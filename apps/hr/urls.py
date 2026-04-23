from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('leaves', views.LeaveRequestViewSet, basename='leave-request')
router.register('onboarding/templates', views.OnboardingTemplateViewSet, basename='onboarding-template')

urlpatterns = [
    path('', include(router.urls)),
    path('onboarding/progress/', views.onboarding_progress, name='onboarding-progress'),
    path(
        'onboarding/progress/steps/<int:step_id>/complete/',
        views.complete_onboarding_step,
        name='onboarding-progress-step-complete',
    ),
    path('onboarding/progress/team/', views.onboarding_team_progress, name='onboarding-team-progress'),
]
