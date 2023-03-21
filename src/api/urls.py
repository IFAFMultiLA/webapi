from django.urls import path
from . import views


urlpatterns = [
    path('session/', views.app_session),
]
