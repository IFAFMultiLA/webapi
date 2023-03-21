from rest_framework import serializers

from .models import TrackingSession


class TrackingSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrackingSession
        fields = ['user_app_session', 'start_time', 'end_time', 'device_info']
