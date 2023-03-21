from django.urls import path
from . import views


urlpatterns = [
    path('session/', views.app_session),
    path('session_login/', views.app_session_login),
    path('start_tracking/', views.start_tracking),
    path('stop_tracking/', views.stop_tracking),
    path('track_event/', views.track_event),
]
