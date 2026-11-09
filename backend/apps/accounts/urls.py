from django.urls import path

from apps.accounts import views, views_otp

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard_redirect, name="dashboard"),
    path("auth/register/", views.register, name="register"),
    path("auth/login/", views.user_login, name="login"),
    path("auth/forgot-password/", views.forgot_password, name="forgot-password"),
    path("auth/reset-password/", views.reset_password, name="reset-password"),
    path("auth/reset-password/method/", views_otp.reset_method, name="reset-method"),
    path("auth/reset-password/verify/", views_otp.reset_verify, name="reset-verify"),
    path("auth/verify-email/", views_otp.verify_email, name="verify-email"),
    path("auth/logout/", views.user_logout, name="logout"),
    path("api/auth/ws-ticket/", views.ws_ticket, name="ws-ticket"),
    path("auth/notification-settings/", views.notification_settings, name="notification-settings"),
    path("auth/delete-account/", views.request_account_deletion, name="delete-account"),
    path("auth/task-status/<str:task_id>/", views.check_auth_task, name="auth-task-status"),
    path("auth/finalize/", views.finalize_auth, name="finalize-auth"),
]
