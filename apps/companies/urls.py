from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register('', views.CompanyViewSet, basename='company')

invitation_list = views.InvitationViewSet.as_view({
    'get': 'list',
    'post': 'create',
})
invitation_revoke = views.InvitationViewSet.as_view({'post': 'revoke'})
invitation_resend = views.InvitationViewSet.as_view({'post': 'resend'})

urlpatterns = [
    path('<int:company_id>/invitations/', invitation_list, name='company-invitations'),
    path(
        '<int:company_id>/invitations/<int:id>/revoke/',
        invitation_revoke,
        name='company-invitation-revoke',
    ),
    path(
        '<int:company_id>/invitations/<int:id>/resend/',
        invitation_resend,
router.register(r'', views.CompanyViewSet, basename='company')

_invitation = views.InvitationViewSet.as_view

urlpatterns = [
    path(
        '<int:company_pk>/invitations/',
        _invitation({'get': 'list', 'post': 'create'}),
        name='company-invitation-list',
    ),
    path(
        '<int:company_pk>/invitations/<int:pk>/',
        _invitation({
            'get': 'retrieve',
            'put': 'update',
            'patch': 'partial_update',
            'delete': 'destroy',
        }),
        name='company-invitation-detail',
    ),
    path(
        '<int:company_pk>/invitations/<int:pk>/revoke/',
        _invitation({'post': 'revoke'}),
        name='company-invitation-revoke',
    ),
    path(
        '<int:company_pk>/invitations/<int:pk>/resend/',
        _invitation({'post': 'resend'}),
        name='company-invitation-resend',
    ),
    path('', include(router.urls)),
]
