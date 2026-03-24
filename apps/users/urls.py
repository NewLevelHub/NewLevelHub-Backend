from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

urlpatterns = [
    # Registration & Login
    path('register/', views.register_user, name='register'),
    path('login/', views.login_user, name='login'),
    path('logout/', views.logout_user, name='logout'),

    # JWT Token Management
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),

    # Mobile Biometric
    path('mobile/confirm/', views.mobile_biometric_confirm, name='mobile-biometric-confirm'),

    # Current User
    path('me/', views.get_current_user, name='current-user'),
    path('me/permissions/', views.get_current_user_permissions, name='current-user-permissions'),

    # Password Reset
    path('password/reset/', views.password_reset_request, name='password-reset'),
]
