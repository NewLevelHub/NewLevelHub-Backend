from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register('', views.CompanyViewSet, basename='company')

invitation_list = views.InvitationViewSet.as_view(
    {
        'get': 'list',
        'post': 'create',
    }
)
invitation_revoke = views.InvitationViewSet.as_view({'post': 'revoke'})
invitation_resend = views.InvitationViewSet.as_view({'post': 'resend'})

building_invitation_list = views.BuildingInvitationViewSet.as_view(
    {
        'get': 'list',
        'post': 'create',
    }
)
building_invitation_revoke = views.BuildingInvitationViewSet.as_view({'post': 'revoke'})
building_invitation_resend = views.BuildingInvitationViewSet.as_view({'post': 'resend'})

urlpatterns = [
    path(
        '<int:company_id>/calendar/',
        views.CompanyCalendarView.as_view(),
        name='company-calendar',
    ),
    path(
        '<int:company_id>/calendar/busy/',
        views.CompanyCalendarBusyView.as_view(),
        name='company-calendar-busy',
    ),
    path(
        '<int:company_id>/directory/',
        views.CompanyDirectoryView.as_view(),
        name='company-directory',
    ),
    path(
        '<int:company_id>/directory/<int:user_id>/',
        views.CompanyDirectoryProfileView.as_view(),
        name='company-directory-profile',
    ),
    path('<int:company_id>/invitations/', invitation_list, name='company-invitations'),
    path(
        '<int:company_id>/invitations/<int:id>/revoke/',
        invitation_revoke,
        name='company-invitation-revoke',
    ),
    path(
        '<int:company_id>/invitations/<int:id>/resend/',
        invitation_resend,
        name='company-invitation-resend',
    ),
    # Building-staff users (reception / service_manager) — read-only list for superadmin.
    path('building-staff/', views.BuildingStaffListView.as_view(), name='building-staff'),
    # Building-staff invites (reception / service_manager) — no company in URL.
    path('building-invites/', building_invitation_list, name='building-invitations'),
    path(
        'building-invites/<int:id>/revoke/',
        building_invitation_revoke,
        name='building-invitation-revoke',
    ),
    path(
        'building-invites/<int:id>/resend/',
        building_invitation_resend,
        name='building-invitation-resend',
    ),
    path(
        '<int:company_id>/members/<int:user_id>/activity/',
        views.CompanyMemberActivityView.as_view(),
        name='company-member-activity',
    ),
    path('', include(router.urls)),
]
