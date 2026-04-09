from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
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
