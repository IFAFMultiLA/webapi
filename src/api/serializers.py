"""
Serializers for storing data via REST API.

.. codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from rest_framework import serializers

from .models import TrackingSession, TrackingEvent


# maximum age  of sent event timestamps
TIMESTAMP_VALIDATION_MAX_AGE = timedelta(minutes=5)
# timestamps "in the future" may happen due to slight clock differences
TIMESTAMP_VALIDATION_MAX_FUTURE = timedelta(seconds=5)


def _validate_timestamp(t):
    """
    Validate a timestamp datetime object `t`. If it's too long in the past (time buffer specified by
    `TIMESTAMP_VALIDATION_MAX_AGE`) or in the future, raise a `ValidationError`, otherwise return `t`.
    """
    now = datetime.now(ZoneInfo(settings.TIME_ZONE))
    if now - TIMESTAMP_VALIDATION_MAX_AGE <= t <= now + TIMESTAMP_VALIDATION_MAX_FUTURE:
        return t
    else:
        raise serializers.ValidationError('invalid timestamp')


class TrackingSessionSerializer(serializers.ModelSerializer):
    """Model serializer for TrackingSession model."""

    def validate_start_time(self, value):
        return _validate_timestamp(value)

    def validate_end_time(self, value):
        return _validate_timestamp(value)

    class Meta:
        model = TrackingSession
        fields = ['user_app_session', 'start_time', 'end_time', 'device_info']


class TrackingEventSerializer(serializers.ModelSerializer):
    """Model serializer for TrackingEvent model."""

    def validate_time(self, value):
        return _validate_timestamp(value)

    class Meta:
        model = TrackingEvent
        fields = ['tracking_session', 'time', 'type', 'value']
