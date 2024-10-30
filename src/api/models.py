"""
Model definitions.

.. codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""

import hashlib
import json
from datetime import datetime

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse

HASH_KEY = settings.SECRET_KEY.encode()[:32]  # hash salt
APPLICATION_CONFIG_DEFAULT_JSON = {
    "exclude": [],
    "js": [],
    "css": [],
    "feedback": True,
    "summary": True,
    "reset_button": True,
    "tracking": {
        "ip": True,
        "user_agent": True,
        "device_info": True,
        "visibility": True,
        "mouse": True,  # mouse movements
        "clicks": True,  # mouse clicks
        "scrolling": True,
        "inputs": True,
        "attribute_changes": False,
        "chapters": True,
        "summary": True,
        "exercise_hint": True,
        "exercise_submitted": True,
        "exercise_result": True,
        "question_submission": True,
        "video_progress": True,
    },
}

if settings.CHATBOT_API is not None:
    APPLICATION_CONFIG_DEFAULT_JSON["chatbot"] = False
    APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["chatbot"] = True


def max_options_length(opts):
    """
    Get maximum length of all labels in options sequence `opts`.
    """
    if opts:
        return max(map(len, (lbl for lbl, _ in opts)))
    else:
        return 0


def current_time_bytes():
    """
    Get the current time encoded as hex byte string.
    """
    return datetime.now().timestamp().hex().encode()


code_gen_counter = 0


def generate_hash_code(data, size):
    """Generate a hash code of size `size` derived from given data `data`."""
    global code_gen_counter
    code = hashlib.blake2b(
        data + current_time_bytes(), digest_size=size, key=HASH_KEY, salt=code_gen_counter.to_bytes(16, "big")
    ).hexdigest()
    code_gen_counter += 1
    return code


def application_session_url(application, sess_code):
    baseurl = application.url
    if not baseurl.endswith("/"):
        baseurl += "/"
    return f"{baseurl}?sess={sess_code}"


class Application(models.Model):
    """A learning application located at a certain URL."""

    name = models.CharField("Name", max_length=64, unique=True, blank=False)
    url = models.URLField("URL", max_length=512, unique=True, blank=False)
    local_appdir = models.CharField("Local app deployment directory", default=None, max_length=512, null=True)
    updated = models.DateTimeField("Last update", auto_now=True)
    updated_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    default_application_session = models.ForeignKey("ApplicationSession", null=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"{self.name} at {self.url} (#{self.pk})"

    class Meta:
        ordering = ["name"]


def application_config_default_json_instance():
    return APPLICATION_CONFIG_DEFAULT_JSON


class ApplicationConfig(models.Model):
    """A configuration for an application."""

    application = models.ForeignKey(Application, on_delete=models.CASCADE)
    label = models.CharField(
        "Configuration label", max_length=128, blank=False, help_text="A unique label to identify this configuration."
    )
    config = models.JSONField("Configuration", blank=True, default=application_config_default_json_instance)
    app_content = models.TextField(
        "Application content",
        null=True,
        blank=True,
        default=None,
        help_text="Learning app content as retrieved from the app URL and processed for usage with the chatbot API.",
    )
    updated = models.DateTimeField("Last update", auto_now=True)
    updated_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f'{self.application.name} (#{self.application_id}) / configuration "{self.label}" (#{self.id})'

    class Meta:
        constraints = [models.UniqueConstraint(fields=["application", "label"], name="unique_app_label")]
        verbose_name = "Application configuration"


class ApplicationSession(models.Model):
    """A session for a configured application that can be shared among participants using a unique session code."""

    AUTH_MODE_OPTIONS = (
        ("none", "No authentication"),
        ("login", "Login"),
    )

    code = models.CharField("Unique session code", max_length=10, primary_key=True)
    config = models.ForeignKey(ApplicationConfig, on_delete=models.CASCADE)
    auth_mode = models.CharField(
        choices=AUTH_MODE_OPTIONS, max_length=max_options_length(AUTH_MODE_OPTIONS), blank=False
    )
    description = models.CharField("Description", max_length=256, blank=True, default="")
    is_active = models.BooleanField("Is active", default=True)
    updated = models.DateTimeField("Last update", auto_now=True)
    updated_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    def generate_code(self, force=False):
        """
        Generate a unique code for this application session. If `force` is True, overwrite an already given code.
        """
        if self.code and not force:
            raise ValueError("`self.code` is already given and overwriting is disabled (`force` is False)")
        if not hasattr(self, "config"):
            raise ValueError("`self.config` must be set to generate a code")

        # generate a code of length 10 characters (hexdigest, i.e. numbers 0-9 and characters a-f); the code is derived
        # from the configuration, the current time and a counter
        self.code = generate_hash_code(json.dumps(self.config.config).encode(), 5)

        return self.code

    def session_url(self):
        """
        Return a URL pointing to an application with the session code attached.
        """
        return application_session_url(self.config.application, self.code)

    def __str__(self):
        return f'Application session "{self.code}" for configuration "{self.config.label}"'


class ApplicationSessionGate(models.Model):
    """
    A gate to bundle several application sessions especially for testing different variants of an app or different
    apps.
    """

    code = models.CharField("Unique gate session code", max_length=10, primary_key=True)
    label = models.CharField(
        "Label", max_length=128, blank=False, help_text="A unique label to identify this application gate."
    )
    description = models.TextField("Description", max_length=2048, blank=True, default="")
    app_sessions = models.ManyToManyField(
        ApplicationSession,
        verbose_name="Application sessions",
        help_text="Application sessions to which this gate forwards",
    )
    is_active = models.BooleanField("Is active", default=True)
    next_forward_index = models.PositiveIntegerField(default=0)  # stores index to next target app session
    updated = models.DateTimeField("Last update", auto_now=True)
    updated_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    def generate_code(self, force=False):
        """
        Generate a unique code for this application session. If `force` is True, overwrite an already given code.
        """
        if self.code and not force:
            raise ValueError("`self.code` is already given and overwriting is disabled (`force` is False)")

        # generate a code of length 10 characters (hexdigest, i.e. numbers 0-9 and characters a-f); the code is derived
        # from the current time and a counter
        self.code = generate_hash_code("appsessiongate".encode(), 5)

        return self.code

    def session_url(self):
        """
        Return a URL pointing to an application with the session code attached.
        """
        if self.code:
            return reverse("gate", args=(self.code,))
        else:
            return ""

    def __str__(self):
        return f'Application session gate "{self.code}"'

    class Meta:
        constraints = [models.UniqueConstraint(fields=["label"], name="unique_label")]


class UserApplicationSession(models.Model):
    """A session tied to a user for a specific application session."""

    application_session = models.ForeignKey(ApplicationSession, on_delete=models.CASCADE)

    # the following depends on the auth_mode of the application_session:
    # - for auth_mode "login", the user must be set (it is a registered user that logged in when using the application)
    # - for auth_mode "none", the user is NULL
    # - in both cases, a unique code must be generated for the first login or visit (this code can then be
    #   stored via cookies)
    user = models.ForeignKey(User, null=True, default=None, on_delete=models.SET_NULL)
    code = models.CharField("Unique user session code", max_length=64, blank=False)

    created = models.DateTimeField("Creation time", auto_now_add=True)

    def generate_code(self, force=False):
        """
        Generate a unique code for this user application session (i.e. a user authentication token).
        If `force` is True, overwrite an already given code.
        """
        if self.code and not force:
            raise ValueError("`self.code` is already given and overwriting is disabled (`force` is False)")

        # generate a code of length 32 characters (hexdigest, i.e. numbers 0-9 and characters a-f); the code is derived
        # from the app session code, the current time and a counter
        self.code = generate_hash_code(self.application_session.code.encode(), 32)

        return self.code

    def __str__(self):
        return (
            f'User session #{self.pk} "{self.code}" for user #{self.user_id} and application session '
            f'"{self.application_session_id}"'
        )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["application_session", "code"], name="unique_appsess_code")]


class TrackingSession(models.Model):
    """
    A tracking session is a session that allows to collect data for a specific user session after login / first visit
    and until the tracking session is explicitly closed or timed out.
    """

    user_app_session = models.ForeignKey(UserApplicationSession, on_delete=models.CASCADE)
    # this information must be submitted and is not set via auto_now_add
    start_time = models.DateTimeField("Session start", blank=False)
    end_time = models.DateTimeField("Session end", null=True, default=None)
    device_info = models.JSONField("User device information", blank=True, null=True)

    def __str__(self):
        return (
            f'Tracking session #{self.pk} for user application session #{self.user_app_session_id} in time range '
            f'{self.start_time} to {self.end_time if self.end_time else "(ongoing)"}'
        )


class UserFeedback(models.Model):
    """
    User feedback given by a user (identified via user application session in field `user_app_session`) for a specific
    content section of an application. If this feedback is given during a tracking session, it is linked to it via the
    `tracking_session` field. If tracking is disabled or declined the `tracking_session` field is set to NULL.

    Only one user feedback is allowed by user application session and content section (see "unique constraint" in
    `Meta` class).
    """

    # the user application session for which the feedback was given;
    # this is set even when tracking was disabled or declined
    user_app_session = models.ForeignKey(UserApplicationSession, on_delete=models.CASCADE)

    # the tracking session in which the feedback was given; if tracking was disabled or declined by the user, this field
    # will be NULL
    tracking_session = models.ForeignKey(TrackingSession, null=True, default=None, on_delete=models.CASCADE)

    # an HTML XPath string referring to the content section for which the feedback was given
    content_section = models.CharField("Content section identifier", max_length=1024, blank=False)

    # quantitative feedback given as integer score in range [1, 5];
    # NULL means no score given by user (either because quant. feedback was disabled or because user didn't provide
    # such feedback)
    score = models.SmallIntegerField(
        "Feedback score", null=True, default=None, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )

    # qualitative feedback given as free form text;
    # NULL means no feedback given by user because qual. feedback was disabled; if qual. feedback was enabled but
    # no such feedback was given by the user, the field contains an empty string
    text = models.TextField("Feedback text", null=True, default=None, blank=True)

    created = models.DateTimeField("Creation time", auto_now_add=True)

    def __str__(self):
        return (
            f"User feedback #{self.pk} for user application session #{self.user_app_session_id} on content "
            f"section {self.content_section}, created on {self.created}"
        )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user_app_session", "content_section"], name="unique_userappsess_content_section"
            ),
            models.CheckConstraint(
                check=Q(score__isnull=False) | Q(text__isnull=False), name="either_score_or_text_must_be_given"
            ),
        ]


class TrackingEvent(models.Model):
    """An event tracked during interaction of a user within a tracking session of an application."""

    tracking_session = models.ForeignKey(TrackingSession, on_delete=models.CASCADE)
    time = models.DateTimeField(blank=False)  # this information must be submitted and is not set via auto_now_add
    type = models.CharField("Event type", max_length=128, blank=False)  # TODO: use discrete set of choices?
    value = models.JSONField("Event value", blank=True, null=True)

    def __str__(self):
        return (
            f"Tracking event #{self.pk} for tracking session #{self.tracking_session_id} at {self.time} of type "
            f'"{self.type}"'
        )
