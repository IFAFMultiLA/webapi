from django.urls import path
from . import views


urlpatterns = [
    path('session/', views.app_session),
    path('session_login/', views.app_session_login),
]
