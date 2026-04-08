from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

urlpatterns = [
    # Auth
    path('register/', views.register, name='register'),
    path('register/invite/', views.register_by_invite, name='register-by-invite'),
    path('login/', views.login, name='login'),
    path('logout/', views.logout, name='logout'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('email/verify/', views.verify_email, name='email-verify'),
    path('email/resend/', views.resend_verification_email, name='email-resend'),
    path('password/reset/', views.password_reset_request, name='password-reset-request'),
    path('password/reset/confirm/', views.password_reset_confirm, name='password-reset-confirm'),

    # Profile
    path('me/', views.me, name='me'),
    path('me/update/', views.update_profile, name='update-profile'),
    path('me/avatar/', views.delete_avatar, name='delete-avatar'),
    path('me/password/', views.change_password, name='change-password'),

    # Admin: user management
    path('users/', views.UserListView.as_view(), name='users-list'),
    path('users/<int:pk>/', views.UserDetailView.as_view(), name='users-detail'),
]
