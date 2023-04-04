"""
URL routing.
"""

from django.urls import path
from . import views


# map URL patterns to views
urlpatterns = [
    path('session/', views.app_session, name='session'),
    path('session_login/', views.app_session_login, name='session_login'),
    path('start_tracking/', views.start_tracking, name='start_tracking'),
    path('stop_tracking/', views.stop_tracking, name='stop_tracking'),
    path('track_event/', views.track_event, name='track_event'),
]
