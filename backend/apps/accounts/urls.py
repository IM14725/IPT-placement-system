from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard_redirect, name="dashboard"),
    path("auth/register/", views.register, name="register"),
    path("auth/login/", views.user_login, name="login"),
    path("auth/forgot-password/", views.forgot_password, name="forgot-password"),
    path("auth/reset-password/", views.reset_password, name="reset-password"),
    path("auth/logout/", views.user_logout, name="logout"),
    path("api/auth/my-token/", views.my_token, name="my-token"),
]