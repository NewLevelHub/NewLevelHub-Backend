from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('', views.CompanyViewSet, basename='company')
router.register('invitations', views.InvitationViewSet, basename='invitation')

urlpatterns = [
    path('', include(router.urls)),
]
