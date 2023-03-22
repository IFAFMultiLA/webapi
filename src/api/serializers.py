from rest_framework import serializers

from .models import TrackingSession, TrackingEvent


class TrackingSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrackingSession
        fields = ['user_app_session', 'start_time', 'end_time', 'device_info']


class TrackingEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrackingEvent
        fields = ['tracking_session', 'time', 'type', 'value']
