from django.db import models
from django.contrib.auth.models import User


def max_options_length(opts):
    return max(map(len, (lbl for lbl, _ in opts)))


class Application(models.Model):
    """A learning application located at a certain URL."""
    name = models.CharField('Name', max_length=64, unique=True, blank=False)
    url = models.URLField('URL', max_length=512, unique=True, blank=False)
    updated = models.DateTimeField('Last update', auto_now=True)
    updated_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f'Application #{self.pk} "{self.name}" at {self.url}'


class ApplicationConfig(models.Model):
    """A configuration for an application."""
    application = models.ForeignKey(Application, on_delete=models.CASCADE)
    label = models.CharField('Configuration label', max_length=128, blank=False,
                             help_text='A unique label to identify this configuration.')
    config = models.JSONField('Configuration', blank=True)
    updated = models.DateTimeField('Last update', auto_now=True)

    def __str__(self):
        return f'Application configuration #{self.pk} "{self.label}" for application #{self.application_id}'

    class Meta:
        constraints = [models.UniqueConstraint(fields=['application', 'label'], name='unique_app_label')]


class ApplicationSession(models.Model):
    """A session for a configured application that can be shared among participants using a unique session code."""
    AUTH_MODE_OPTIONS = (
        ('none', 'No authentication'),
        ('login', 'Login'),
    )

    code = models.CharField('Unique session code', max_length=10, primary_key=True)
    config = models.ForeignKey(ApplicationConfig, on_delete=models.CASCADE)
    auth_mode = models.CharField(choices=AUTH_MODE_OPTIONS, max_length=max_options_length(AUTH_MODE_OPTIONS),
                                 blank=False)

    def __str__(self):
        return f'Application session "{self.code}" for configuration #{self.config_id}'


class UserApplicationSession(models.Model):
    """A session tied to a user for a specific application session."""
    application_session = models.ForeignKey(ApplicationSession, on_delete=models.CASCADE)

    # the following depends on the auth_mode of the application_session:
    # - for auth_mode "login", the user must be set (it is a registered user that logged in when using the application)
    # - for auth_mode "none", the user is NULL
    # - in both cases, a unique code must be generated for the first login or visit (this code can then be
    #   stored via cookies)
    user = models.ForeignKey(User, null=True, default=None, on_delete=models.SET_NULL)
    code = models.CharField('Unique user session code', max_length=10, blank=False)

    created = models.DateTimeField('Creation time', auto_now_add=True)

    def __str__(self):
        return f'User session for user #{self.user_id} and application session "{self.application_session_id}"'

    class Meta:
        constraints = [models.UniqueConstraint(fields=['application_session', 'code'], name='unique_appsess_code')]


class TrackingSession(models.Model):
    """
    A tracking session is a session that allows to collect data for a specific user session after login and until
    logout / timeout.
    """
    user_app_session = models.ForeignKey(UserApplicationSession, on_delete=models.CASCADE)
    start_time = models.DateTimeField('Session start', auto_now_add=True)
    end_time = models.DateTimeField('Session end', null=True, default=None)
    device_info = models.TextField('User device information', blank=True)

    def __str__(self):
        return f'Tracking session #{self.pk} for user application session #{self.user_app_session_id} in time range ' \
               f'{self.start_time} to {self.end_time if self.end_time else "(ongoing)"}'


class TrackingEvent(models.Model):
    """An event tracked during interaction of a user within a tracking session of an application."""
    tracking_session = models.ForeignKey(TrackingSession, on_delete=models.CASCADE)
    time = models.DateTimeField(blank=False)   # this information must be submitted and is not set via auto_now_add
    type = models.CharField('Event type', max_length=128, blank=False)    # TODO: use discrete set of choices?
    value = models.JSONField('Event value', blank=True)

    def __str__(self):
        return f'Tracking event #{self.pk} for tracking session #{self.tracking_session_id} at {self.time} of type ' \
               f'"{self.type}"'
