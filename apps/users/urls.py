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
    path('roles/', views.list_user_roles, name='user-roles'),
    path('me/permissions/', views.get_current_user_permissions, name='current-user-permissions'),
    path('profile/', views.get_my_profile, name='my-profile'),
    path('profile/update/', views.update_my_profile, name='my-profile-update'),

    # User Management (admin/supermentor)
    path('users/', views.list_users, name='users-list'),
    path('users/create/', views.create_user, name='users-create'),
    path('users/<int:user_id>/', views.get_user_by_id, name='users-detail'),
    path('users/<int:user_id>/update/', views.update_user_by_id, name='users-update'),
    path('users/<int:user_id>/delete/', views.delete_user_by_id, name='users-delete'),

    # Password Reset
    path('password/reset/', views.password_reset_request, name='password-reset'),
]
