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
    # Drill-down must be declared before the team list to avoid Django matching user_id as 'team'.
    path(
        'onboarding/progress/team/<int:user_id>/',
        views.onboarding_team_member_progress,
        name='onboarding-team-member-progress',
    ),
    path('onboarding/progress/team/', views.onboarding_team_progress, name='onboarding-team-progress'),
    # Nested step management under a template.
    path(
        'onboarding/templates/<int:template_pk>/steps/',
        views.OnboardingStepViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='onboarding-template-steps-list',
    ),
    path(
        'onboarding/templates/<int:template_pk>/steps/<int:pk>/',
        views.OnboardingStepViewSet.as_view({
            'get': 'retrieve',
            'patch': 'partial_update',
            'delete': 'destroy',
        }),
        name='onboarding-template-steps-detail',
    ),
    # Assignment endpoints — my-assignment before the parameterised detail URL
    path(
        'onboarding/my-assignment/',
        views.my_onboarding_assignment,
        name='my-onboarding-assignment',
    ),
    path(
        'onboarding/assignments/',
        views.onboarding_assignments,
        name='onboarding-assignments',
    ),
    path(
        'onboarding/assignments/<int:user_id>/',
        views.onboarding_assignment_detail,
        name='onboarding-assignment-detail',
    ),
]
