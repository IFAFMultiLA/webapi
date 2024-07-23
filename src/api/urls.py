"""
URL routing.

.. codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""

from django.urls import path
from django.conf import settings
from rest_framework.schemas import get_schema_view

from . import views


# map URL patterns to views
urlpatterns = [
    path("", views.index, name="index"),
    path("session/", views.app_session, name="session"),
    path("session_login/", views.app_session_login, name="session_login"),
    path("register_user/", views.register_user, name="register_user"),
    path("start_tracking/", views.start_tracking, name="start_tracking"),
    path("stop_tracking/", views.stop_tracking, name="stop_tracking"),
    path("track_event/", views.track_event, name="track_event"),
    path("user_feedback/", views.user_feedback, name="user_feedback"),
    path("gate/<str:sessioncode>", views.app_session_gate, name="gate"),
]

if settings.DEBUG:
    urlpatterns.append(
        path(
            "openapi",
            get_schema_view(title="MultiLA web API", description="MultiLA platform web API", version="0.1.0"),
            name="openapi-schema",
        )
    )
