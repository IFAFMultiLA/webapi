"""
Admin backend.

ModelAdmin classes to generate the django administration backend.

.. codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""

import csv
import json
import os.path
import shutil
import tempfile
import threading
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from glob import glob
from time import sleep
from urllib.parse import urlsplit, urlunsplit
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import connection as db_conn
from django.db.models import Avg, Count, F, Max
from django.http import (
    FileResponse,
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotFound,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, re_path, reverse
from django.utils.safestring import mark_safe
from django.utils.text import Truncator

from .models import (
    APPLICATION_CONFIG_DEFAULT_JSON,
    Application,
    ApplicationConfig,
    ApplicationSession,
    ApplicationSessionGate,
    TrackingEvent,
    TrackingSession,
    UserFeedback,
)

DEFAULT_TZINFO = ZoneInfo(settings.TIME_ZONE)

can_upload_apps = settings.APPS_DEPLOYMENT and os.access(settings.APPS_DEPLOYMENT["upload_path"], os.W_OK)

if can_upload_apps:
    from .admin_appdeploy import get_deployed_app_info, handle_uploaded_app_deploy_file, remove_deployed_app


# --- shared utilities ---


def utc_to_default_tz(t):
    """
    Helper function to add the default time zone set in `settings.TIME_ZONE` and its respective offset to a datetime
    object `t`.

    Note that PostgreSQL doesn't save the timezone for a timestamp column. It should be recorded in a separated column.
    To circumvent that, we use a global default timezone for now.
    """
    return (t + t.replace(tzinfo=DEFAULT_TZINFO).utcoffset()).replace(tzinfo=DEFAULT_TZINFO)


def format_value_as_json(value, maxlines=None):
    """
    Helper function to format a Python value `value` as JSON string.
    Set `maxlines` to a positive integer to truncate the JSON string to `maxlines` lines at maximum.
    """
    if maxlines is not None and (not isinstance(maxlines, int) or maxlines <= 0):
        raise ValueError("if `maxlines` is given, it must be a strictly positive integer")

    formatted_lines = json.dumps(value, indent=2).split("\n")
    if maxlines is not None and len(formatted_lines) > maxlines:
        formatted_lines = formatted_lines[:maxlines] + ["..."]

    return "\n".join(formatted_lines)


class JSONEncoderWithIdent(json.JSONEncoder):
    def __init__(self, *args, indent, **kwargs):
        super().__init__(*args, indent=2, **kwargs)


class ApplicationForm(forms.ModelForm):
    """
    Custom form for adding new applications.

    Adds an extra field and validations for app deployment via upload.
    """

    app_upload = forms.FileField(
        label="Upload app",
        help_text="Upload a ZIP file containing the R application and a renv lockfile.",
        required=False,
    )

    def clean_app_upload(self):
        """Custom file upload cleaning. Check that the uploaded file is a ZIP archive."""
        app_upload = self.cleaned_data["app_upload"]

        if app_upload and app_upload.content_type not in {
            "application/zip",
            "application/x-zip-compressed",
            "application/zip-compressed",
        }:
            raise ValidationError("Uploaded file does not have the correct file type. A ZIP file is required.")

        return app_upload

    def clean(self):
        """Custom form cleaning. Check that an URL must be set manually when no app was uploaded."""
        cleaned_data = super().clean()

        if can_upload_apps:
            app_upload = cleaned_data.get("app_upload")
            url = cleaned_data.get("url")

            if not app_upload and not url:
                raise ValidationError("If not uploading an app, you must specify an URL.")
        else:
            return cleaned_data

    class Meta:  # looks like it's not (easily) possible to define fieldsets and/or a custom template in a ModelForm
        model = Application
        exclude = ["updated", "updated_by"]


# build chatbot API options from settings
chatbot_api_choices = []
if settings.CHATBOT_API:
    chatbot_api_choices.append((None, "(disabled)"))  # first choice: no chat API usage
    # add available providers with their respective models
    for provider, opts in settings.CHATBOT_API["providers"].items():
        if " | " in provider:
            raise ValueError("the provider name is not allowed to use the string ' | '")
        for model in opts["available_models"]:
            chatbot_api_choices.append((f"{provider} | {model}", f"{provider} / {model}"))


class ApplicationConfigForm(forms.ModelForm):
    """
    Custom form for `ApplicationConfig` with additional form fields for options stored in the `config` JSON field.
    """

    feedback = forms.BooleanField(
        label="Enable user feedback",
        help_text="Enables user feedback elements at the end of each chapter.",
        initial=True,
        required=False,
    )
    summary = forms.BooleanField(
        label="Enable summary panel",
        help_text="Enables dynamic summary panel displayed on the right side of the " "application.",
        initial=True,
        required=False,
    )
    if settings.CHATBOT_API:
        chatbot = forms.ChoiceField(
            label="Enable chatbot choosing a provider and model",
            help_text="Provide an interactive assistant in the application.",
            choices=chatbot_api_choices,
            initial=None,
            required=False,
        )
        chatbot_system_prompt = forms.CharField(
            label="Chat API system prompt template",
            help_text="Use placeholder $doc_text for learning app text.",
            required=False,
            widget=forms.Textarea(attrs={"cols": 103}),
        )
        chatbot_user_prompt = forms.CharField(
            label="Chat API user prompt template",
            help_text="Use placeholders $doc_text for learning app text and $question for user submitted question.",
            required=False,
            widget=forms.Textarea(attrs={"cols": 103}),
        )
    reset_button = forms.BooleanField(
        label="Enable reset button",
        help_text="Show a reset button to clear all inputs and restart the tutorial.",
        initial=True,
        required=False,
    )
    track_ip = forms.BooleanField(
        label="Record user IP",
        help_text="Record the user's IP address.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["ip"],
        required=False,
    )
    track_user_agent = forms.BooleanField(
        label="Record user agent",
        help_text="Record the user's browser.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["user_agent"],
        required=False,
    )
    track_device_info = forms.BooleanField(
        label="Track device information",
        help_text="Enable tracking user device information (form factor, window size and content size).",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["device_info"],
        required=False,
    )
    if settings.CHATBOT_API:
        track_chatbot = forms.BooleanField(
            label="Track communication with chatbot",
            help_text="Enable tracking chatbot communication if chatbot feature is enabled.",
            initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["chatbot"],
            required=False,
        )
    track_visibility = forms.BooleanField(
        label="Track browser visibility state",
        help_text="Enable tracking the state of the user's browser window visibility.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["visibility"],
        required=False,
    )
    track_mouse = forms.BooleanField(
        label="Track mouse movements",
        help_text="Enable tracking mouse movements.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["mouse"],
        required=False,
    )
    track_clicks = forms.BooleanField(
        label="Track mouse clicks",
        help_text="Enable tracking mouse clicks or touches when the user device has no mouse.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["clicks"],
        required=False,
    )
    track_scrolling = forms.BooleanField(
        label="Track scrolling",
        help_text="Enable tracking when the user scrolls through the application.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["scrolling"],
        required=False,
    )
    track_inputs = forms.BooleanField(
        label="Track form inputs",
        help_text="Enable tracking form inputs.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["inputs"],
        required=False,
    )
    track_attribute_changes = forms.BooleanField(
        label="Track HTML element attribute changes",
        help_text="Warning: When enabled, this generates a lot of usually " "unnecessary data.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["attribute_changes"],
        required=False,
    )
    track_chapters = forms.BooleanField(
        label="Tracking chapter changes",
        help_text="Enable tracking of switching between chapters.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["chapters"],
        required=False,
    )
    track_summary = forms.BooleanField(
        label="Track summary panel events",
        help_text="Enable tracking of summary panel events (in case the summary panel is enabled).",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["summary"],
        required=False,
    )
    track_exercise_hint = forms.BooleanField(
        label="Track exercise hint requests",
        help_text="Enable tracking of exercise hint displays.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["exercise_hint"],
        required=False,
    )
    track_exercise_submitted = forms.BooleanField(
        label="Track exercise submissions",
        help_text="Enable tracking of exercise submissions.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["exercise_submitted"],
        required=False,
    )
    track_exercise_result = forms.BooleanField(
        label="Track exercise result displays",
        help_text="Enable tracking of exercise result displays.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["exercise_result"],
        required=False,
    )
    track_question_submission = forms.BooleanField(
        label="Track question submissions",
        help_text="Enable tracking of question submissions.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["question_submission"],
        required=False,
    )
    track_video_progress = forms.BooleanField(
        label="Track video display progress",
        help_text="Enable tracking of video display events.",
        initial=APPLICATION_CONFIG_DEFAULT_JSON["tracking"]["video_progress"],
        required=False,
    )
    exclude = forms.CharField(
        label="CSS selectors for excluding content",
        help_text="Provide CSS selectors separated by comma, e.g. " '"#additional_text, .code_instructions".',
        required=False,
        widget=forms.TextInput(attrs={"size": 100}),
    )
    js = forms.CharField(
        label="Additional JavaScript files to load",
        help_text="Provide paths/URLs to JavaScript files separated by comma, e.g. "
        '"www/custom.js, https://example.com/myscripts.js".',
        required=False,
        widget=forms.TextInput(attrs={"size": 100}),
    )
    css = forms.CharField(
        label="Additional CSS files to load",
        help_text="Provide paths/URLs to CSS files separated by comma, e.g. "
        '"www/custom.css, https://example.com/myscripts.css".',
        required=False,
        widget=forms.TextInput(attrs={"size": 100}),
    )
    additional_json = forms.JSONField(
        label="Additional configuration",
        help_text="Provide additional configuration as JSON. This will be merged with "
        "the above settings, but will override them if necessary.",
        required=False,
        widget=forms.Textarea(attrs={"cols": 103}),
        encoder=JSONEncoderWithIdent,
        initial=None,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # set initial values for config fields
        additional_config = {}
        for k, v in self.instance.config.items():
            if k in APPLICATION_CONFIG_DEFAULT_JSON:  # standard option
                if isinstance(v, list):
                    v = ", ".join(v)

                if k == "tracking":
                    for tracking_k, tracking_v in v.items():
                        self.initial["track_" + tracking_k] = tracking_v
                else:
                    self.initial[k] = v
            else:
                # non-standard option
                additional_config[k] = v

        self.initial["additional_json"] = additional_config

    def save(self, commit=True):
        """Custom save logic to transform form field values into config dict."""

        # make copy of default config
        config = APPLICATION_CONFIG_DEFAULT_JSON.copy()

        # apply fields with comma-separated values by turning them into a list
        for f in ("exclude", "js", "css"):
            config[f] = [v.strip() for v in self.cleaned_data.get(f, "").split(",") if v.strip()]

        # apply all other fields
        fields = ["feedback", "summary", "reset_button"]
        if settings.CHATBOT_API:
            fields.extend(["chatbot", "chatbot_system_prompt", "chatbot_user_prompt"])
        for f in fields:
            config[f] = self.cleaned_data.get(f, APPLICATION_CONFIG_DEFAULT_JSON[f])
        for f, default_val in APPLICATION_CONFIG_DEFAULT_JSON["tracking"].items():
            config["tracking"][f] = self.cleaned_data.get("track_" + f, default_val)

        # apply addition options supplied as JSON
        if additional_json := self.cleaned_data.get("additional_json", None):
            config.update(additional_json)

        # set the config dict and save the model
        self.instance.config = config

        return super().save(commit=commit)

    class Meta:  # looks like it's not (easily) possible to define fieldsets and/or a custom template in a ModelForm
        model = ApplicationConfig
        exclude = ["config", "updated_by"]
        if settings.CHATBOT_API:
            widgets = {
                "app_content": forms.Textarea(attrs={"disabled": True, "cols": 103}),
            }


# --- model admins ---


def app_config_form_fields(default_fields, obj=None):
    """Helper function to remove some fields when creating a new configuration."""
    additional_chatbot_fields = ("app_content", "chatbot_system_prompt", "chatbot_user_prompt")
    fields = [f for f in default_fields if f not in additional_chatbot_fields]

    if obj is None:
        return [f for f in fields if f not in {"application", "updated", "updated_by"}]
    elif settings.CHATBOT_API and obj.config.get("chatbot"):
        fields.extend(additional_chatbot_fields)

    return fields


class ApplicationConfigInline(admin.StackedInline):
    """
    Inline admin for application configurations used in `ApplicationAdmin` below.
    """

    model = ApplicationConfig
    form = ApplicationConfigForm

    def get_fields(self, request, obj=None):
        """Customize fields: Remove some fields when creating a new configuration."""
        default_fields = super().get_fields(request, obj=obj)
        if isinstance(obj, Application):
            try:
                obj = ApplicationConfig.objects.filter(application=obj.id).first()
            except ApplicationConfig.DoesNotExist:
                return default_fields
        return app_config_form_fields(default_fields, obj)

    def get_extra(self, request, obj=None, **kwargs):
        """No extra form if editing, otherwise display a single extra form."""
        return 0 if obj else 1


def add_default_session_to_app_config(app_config):
    """Add a default application session for a given application configuration `app_config`."""
    default_app_sess = ApplicationSession(
        config=app_config,
        auth_mode=ApplicationSession.AUTH_MODE_OPTIONS[0][0],
        description=app_config.label,
        updated_by=app_config.updated_by,
    )
    default_app_sess.generate_code()
    default_app_sess.save()

    return default_app_sess


class ApplicationAdmin(admin.ModelAdmin):
    """
    Admin for Application model.
    """

    form = ApplicationForm
    fields = ["name", "app_upload", "local_appdir", "url", "default_application_session", "updated", "updated_by"]
    readonly_fields = ["local_appdir", "updated", "updated_by"]
    list_display = ["name_w_url", "configurations_and_sessions", "updated", "updated_by"]
    inlines = [ApplicationConfigInline]
    change_form_template = "admin/app_change_form.html" if can_upload_apps else None

    def __init__(self, *args, **kwargs):
        """
        Custom init method to define additional attributes.
        """
        super().__init__(*args, **kwargs)
        self._cur_changelist_request = None
        self._apps_confs_sessions = None

    @admin.display(ordering="name", description="Name")
    def name_w_url(self, obj):
        """Application name with URL."""
        change_url = reverse("admin:api_application_change", args=(obj.id,))
        return mark_safe(
            f'<p><a href="{change_url}">{obj.name}</a></p>' f'<p><a href="{obj.url}" target="_blank">{obj.url}</a></p>'
        )

    @admin.display(ordering=None, description="Configurations and sessions")
    def configurations_and_sessions(self, obj):
        """
        Display all configurations and sessions per application in a hierarchical way (nested lists).
        """
        configs = self._apps_confs_sessions.get(obj.id, {})

        rows = []
        # iterate through configurations of this application
        for config_id, config_data in configs.items():
            config_url = reverse("admin:api_applicationconfig_change", args=[config_id])
            config_label = config_data["config_label"]

            rows.append(f'<tr><th colspan="2"><a href="{config_url}?application={obj.id}">{config_label}</a></th></tr>')

            # iterate through sessions of this application configuration
            for sess_code, (sess_descr, sess_active) in config_data["sessions"].items():
                sess_url = reverse("admin:api_applicationsession_change", args=[sess_code])
                if sess_descr:
                    sess_descr = f" – {Truncator(sess_descr).chars(25)}"
                if obj.default_application_session and obj.default_application_session.code == sess_code:
                    sess_item_style = "font-weight: bold;"
                else:
                    sess_item_style = ""
                if sess_active:
                    sess_inactive_notice = ""
                else:
                    sess_inactive_notice = " (inactive)"
                    sess_item_style += "color: gray;"

                gate_url = reverse("gate", args=(sess_code,))
                if hasattr(settings, "BASE_URL"):
                    base_url = settings.BASE_URL
                    if base_url.endswith("/"):
                        base_url = base_url[:-1]
                    full_gate_url = base_url + gate_url
                else:
                    full_gate_url = self._cur_changelist_request.build_absolute_uri(gate_url)

                rows.append(
                    f"<tr><td>"
                    f'<a href="{sess_url}?config={config_id}" style="{sess_item_style}">'
                    f"{sess_code}{sess_descr}{sess_inactive_notice}</a></td>"
                    f'<td><a href="{full_gate_url}" style="{sess_item_style}" target="_blank">{full_gate_url}'
                    f"</a></td></tr>"
                )

            rows.append(
                f'<tr><td colspan="2">'
                f'<a href="{reverse("admin:api_applicationsession_add")}?config={config_id}" class="addlink">'
                f'Add session</a></td></tr>'
            )
        rows.append(
            f'<tr><td colspan="2" class="add_config">'
            f'<a href="{reverse("admin:api_applicationconfig_add")}?application={obj.id}" class="addlink">'
            f'Add configuration</a></td></tr>'
        )

        return mark_safe(
            f'<table width="100%" class="configs_and_sessions">'
            f'<colgroup><col width="33%" /></col width="67%" /></colgroup>'
            f'{"".join(rows)}</table>'
        )

    def changelist_view(self, request, extra_context=None):
        """
        Customized changelist view. Retrieve application configurations and sessions and store them in a hierarchical
        dict in `self._apps_confs_sessions` to be used in `configurations_and_sessions` and `session_urls` methods.
        """
        self._cur_changelist_request = request
        self._apps_confs_sessions = defaultdict(dict)
        # load application sessions per application configurations per applications
        qs_app_sessions = (
            Application.objects.prefetch_related("applicationconfig", "applicationsession")
            .order_by("id", "applicationconfig__label")
            .values_list(
                "id",
                "applicationconfig__id",
                "applicationconfig__label",
                "applicationconfig__applicationsession__code",
                "applicationconfig__applicationsession__description",
                "applicationconfig__applicationsession__is_active",
            )
        )
        # iterate through application sessions and build hierarchical dict:
        # {
        #   <app_id>: {
        #     <config_id>: {
        #       "config_label": "<config. label>",
        #       "sessions": {
        #         <session code>: ["<session description>", <is_active>],
        #         ...
        #       },
        #       ...
        #     },
        #     ...
        # }
        for app_id, config_id, config_label, sess_code, sess_descr, sess_active in qs_app_sessions:
            if config_id is None:
                continue

            if config_id in self._apps_confs_sessions[app_id]:
                conf_dict = self._apps_confs_sessions[app_id][config_id]
            else:
                conf_dict = {"config_label": config_label, "sessions": {}}

            if sess_code:
                conf_dict["sessions"][sess_code] = [sess_descr, sess_active]

            self._apps_confs_sessions[app_id][config_id] = conf_dict

        return super().changelist_view(request, extra_context=extra_context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if can_upload_apps and object_id:
            obj = get_object_or_404(Application, pk=object_id)
            if obj.local_appdir:
                extra_context = extra_context or {}
                try:
                    extra_context.update(
                        {"show_app_monitor": obj.local_appdir, "app_info": get_deployed_app_info(obj.local_appdir)}
                    )
                except Exception as e:
                    messages.error(
                        request,
                        f"An error occurred while trying to get information about the " f"deployed application: {e}.",
                    )
        return super().changeform_view(request, object_id=object_id, form_url=form_url, extra_context=extra_context)

    def save_form(self, request, form, change):
        """
        Customized form saving used for handling app deployments via the admin interface.
        """

        if can_upload_apps and "app_upload" in request.FILES:
            try:
                url_safe_app_name = handle_uploaded_app_deploy_file(
                    request.FILES["app_upload"],
                    form.instance.name,
                    app_name=form.instance.local_appdir if change and form.instance.local_appdir else None,
                    replace=change,
                )
                app_base_url = (
                    settings.APPS_DEPLOYMENT["base_url"]
                    if settings.APPS_DEPLOYMENT["base_url"].endswith("/")
                    else settings.APPS_DEPLOYMENT["base_url"] + "/"
                )
                form.instance.local_appdir = url_safe_app_name
                form.instance.url = app_base_url + url_safe_app_name
            except Exception as e:
                messages.error(request, f"An error occurred while trying to deploy the uploaded app: {e}.")
        return super().save_form(request, form, change)

    def save_model(self, request, obj, form, change):
        """
        Custom model save method to add current user to the `updated_by` field.
        """
        obj.updated_by = request.user
        return super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        if can_upload_apps and obj.local_appdir:
            try:
                remove_deployed_app(obj.local_appdir)
                messages.success(request, f'Deployed app was removed from directory "{obj.local_appdir}"')
            except Exception as e:
                messages.error(request, f'Could not remove the app at directory "{obj.local_appdir}": {e}')

    def get_fields(self, request, obj=None):
        """
        Return different fields depending on update or create mode.
        """
        fields = super().get_fields(request, obj=obj)
        if obj:
            # on update return all fields
            if can_upload_apps:
                return fields
            else:
                return [f for f in fields if f not in {"app_upload", "local_appdir"}]
        else:
            # on create, don't show "default application session" field, as we can't possibly have created any
            # application session for this application, yet
            dismiss_fields = {"default_application_session", "updated_by", "updated", "local_appdir"}
            if not can_upload_apps:
                dismiss_fields.add("app_upload")

            return [f for f in fields if f not in dismiss_fields]

    def get_queryset(self, request):
        """Custom queryset for more efficient queries."""
        return super().get_queryset(request).select_related("updated_by", "default_application_session")

    def get_form(self, request, obj=None, change=False, **kwargs):
        """
        Customize form to display application sessions choices on update.
        """
        form = super().get_form(request, obj=obj, change=change, **kwargs)

        if settings.APPS_DEPLOYMENT:
            if not os.access(settings.APPS_DEPLOYMENT["upload_path"], os.W_OK):
                messages.warning(request, "App upload path is not writable.")
            else:
                form.base_fields["url"].required = False
                form.base_fields["url"].help_text = "URL field will be automatically set upon upload."

        if obj:
            # on update, set up the "default application session" field to fetch all related application sessions
            # as options for selection
            form.base_fields["default_application_session"].required = False
            form.base_fields["default_application_session"].queryset = ApplicationSession.objects.filter(
                config__application=obj
            )

        return form

    def response_post_save_add(self, request, obj):
        """
        Custom response function to additionally add a default application session for each application configuration
        that was added.
        """
        for app_config in ApplicationConfig.objects.filter(application=obj):
            add_default_session_to_app_config(app_config)

        return super().response_post_save_add(request, obj)


class ApplicationConfigAdmin(admin.ModelAdmin):
    """
    Admin for ApplicationConfig model.

    This admin is only for creating, editing and deleting application configurations and not for displaying a list
    of application configurations. The application admin shows the configurations per application and links to the
    create / edit forms of this admin.
    """

    readonly_fields = ["updated", "updated_by", "application"]
    list_display = []
    form = ApplicationConfigForm

    @staticmethod
    def _get_application_id(request):
        """Get current application ID either from URL parameter or from session."""
        application_id = request.GET.get("application", None)
        if application_id:
            # given from the URL parameter -> store in session
            request.session["application"] = application_id
        else:
            # try to get from session
            application_id = request.session.get("application", None)

        return application_id

    def get_fields(self, request, obj=None):
        """Customize fields: Remove some fields when creating a new configuration."""
        return app_config_form_fields(super().get_fields(request, obj=obj), obj)

    def add_view(self, request, form_url="", extra_context=None):
        """Customize view: Make sure to get the current application ID for which this configuration is created."""
        if not self._get_application_id(request):
            messages.error(request, "No application selected.")
            return redirect(reverse("admin:api_application_changelist"))

        return super().add_view(request, form_url=form_url, extra_context=extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Customize view: Make sure to get the current application ID for which this configuration is updated."""
        if not self._get_application_id(request):
            messages.error(request, "No application selected.")
            return redirect(reverse("admin:api_application_changelist"))

        return super().change_view(request, object_id=object_id, form_url=form_url, extra_context=extra_context)

    def changelist_view(self, request, extra_context=None):
        """Redirect to application changelist, where configurations are also shown."""
        return redirect(reverse("admin:api_application_changelist"))

    def response_post_save_add(self, request, obj):
        """After saving a new application configuration, forward to the application changelist."""
        return redirect(reverse("admin:api_application_changelist"))

    def response_post_save_change(self, request, obj):
        """After updating an existing application configuration, forward to the application changelist."""
        return redirect(reverse("admin:api_application_changelist"))

    def response_delete(self, request, obj_display, obj_id):
        """After deleting an application configuration, forward to the application changelist."""
        response = super().response_delete(request, obj_display=obj_display, obj_id=obj_id)
        if isinstance(response, HttpResponseRedirect) and response.url == reverse(
            "admin:api_applicationconfig_changelist"
        ):
            response = redirect(reverse("admin:api_application_changelist"))
        return response

    def save_model(self, request, obj, form, change):
        """
        Custom model save method to add current user to the `updated_by` field, assign the selected
        application's ID and optionally fetch the app content text for use with the chatbot.
        """
        obj.updated_by = request.user
        obj.application_id = self._get_application_id(request)
        super().save_model(request, obj, form, change)

        if not change:
            add_default_session_to_app_config(obj)

        # if the chatbot feature is enabled for this app config, start a background thread that will download and
        # prepare the application content
        if settings.CHATBOT_API and obj.config.get("chatbot", None):

            def fetch_app_content(app_config_id):
                import requests
                from bs4 import BeautifulSoup

                text = ""
                default_lang = "en"
                sys_prompt = settings.CHATBOT_API["system_role_templates"][default_lang]
                usr_prompt = settings.CHATBOT_API["user_role_templates"][default_lang]

                # make sure the current app config was saved before in the main thread
                tries = 0
                while True:
                    try:
                        app_config = ApplicationConfig.objects.get(id=app_config_id)
                        savetime_diff = datetime.now(ZoneInfo(settings.TIME_ZONE)) - app_config.updated
                        if savetime_diff < timedelta(seconds=5) or tries > 20:
                            break
                    except ApplicationConfig.DoesNotExist:
                        pass
                    sleep(0.25)
                    tries += 1

                # fetch the app HTML
                response = requests.get(app_config.application.url)

                if response.ok:
                    # extract text from the main content of the learning app's HTML and assign an identifier to each
                    # content element so that the chatbot can refer to that and the app is able to jump to the correct
                    # place; it's important that the identifiers are assigned in the same way as in the learning app
                    content_elems = [
                        "h2",
                        "h3",
                        "h4",
                        "h5",
                        "h6",
                        "p",
                        "ul",
                        "ol",
                        "table",
                        "div.figure",
                        "div.section",
                    ]
                    selector = ", ".join(f".section.level2 > {e}" for e in content_elems)

                    try:
                        html = BeautifulSoup(response.content, features="html.parser")

                        # also try to find out the app's language and use the appropriate prompot
                        try:
                            app_learnrextra_config = json.loads(html.select_one("#learnrextra-config").text)
                            app_lang = app_learnrextra_config["language"]
                            sys_prompt = settings.CHATBOT_API["system_role_templates"].get(app_lang, sys_prompt)
                            usr_prompt = settings.CHATBOT_API["user_role_templates"].get(app_lang, usr_prompt)
                        except Exception:
                            pass

                        text = html.select_one("h1.title").text + "\n\n"
                        skip_classes = ("tracking_consent_text", "data_protection_text")
                        i = 0
                        for elem in html.select(selector):
                            cls = elem.get("class", "")
                            if any(c in cls for c in skip_classes):
                                continue

                            # special treatment for tables: copy them, remove them, ...
                            tbls = deepcopy(elem.select("table"))
                            for tbl in elem.select("table"):
                                tbl.decompose()

                            elem_text = elem.text.strip()

                            # now add the table contents from the copies as plain HTML so that the LLM hopefully
                            # understands the structure
                            for tbl in tbls:
                                elem_text += "\n\n" + str(tbl)
                            text += f"mainContentElem-{i}: {elem_text}\n\n"
                            i += 1
                    except Exception:
                        pass

                if not app_config.config["chatbot_system_prompt"]:
                    app_config.config["chatbot_system_prompt"] = sys_prompt
                if not app_config.config["chatbot_user_prompt"]:
                    app_config.config["chatbot_user_prompt"] = usr_prompt
                app_config.app_content = text
                app_config.save()

            # run this in a separate thread, as fetching the app content via HTTP may take some time
            threading.Thread(target=fetch_app_content, args=[obj.pk], daemon=True).start()


class ApplicationSessionAdmin(admin.ModelAdmin):
    """
    Admin for ApplicationSession model.

    This admin is only for creating, editing and deleting application sessions and not for displaying a list
    of application sessions. The application admin shows the sessions per application and configuration and links to the
    create / edit forms of this admin.
    """

    fields = ["code", "session_gate_url", "description", "auth_mode", "is_active", "updated", "updated_by"]
    readonly_fields = ["config", "code", "session_gate_url", "updated", "updated_by"]
    list_display = []

    def __init__(self, *args, **kwargs):
        """
        Custom init method to define additional attributes.
        """
        super().__init__(*args, **kwargs)
        self._cur_change_request = None

    @staticmethod
    def _get_config_id(request):
        """Get current configuration ID either from URL parameter or from session."""
        config_id = request.GET.get("config", None)
        if config_id:
            # given from the URL parameter -> store in session
            request.session["config"] = config_id
        else:
            # try to get from session
            config_id = request.session.get("config", None)

        return config_id

    @admin.display(description="Session URL")
    def session_gate_url(self, obj):
        """Display session gate URL (readonly) in change form."""
        gate_url = reverse("gate", args=(obj.code,))
        if hasattr(settings, "BASE_URL"):
            base_url = settings.BASE_URL
            if base_url.endswith("/"):
                base_url = base_url[:-1]
            full_gate_url = base_url + gate_url
        else:
            full_gate_url = self._cur_change_request.build_absolute_uri(gate_url)

        redir_url = obj.session_url()

        return mark_safe(
            f'<a href="{full_gate_url}" target="_blank">{full_gate_url}</a> &rArr; '
            f'<a href="{redir_url}" target="_blank">{redir_url}</a>'
        )

    def get_fields(self, request, obj=None):
        """Customize fields: Remove some fields when creating a new application session."""
        fields = super().get_fields(request, obj=obj)
        if obj is None:
            return [f for f in fields if f not in {"code", "session_gate_url", "updated", "updated_by"}]
        return fields

    def save_model(self, request, obj, form, change):
        """
        Custom model save method to add current user to the `updated_by` field, assign the selected configuration's ID
        and generate a session code for newly created sessions.
        """
        obj.config_id = self._get_config_id(request)
        obj.updated_by = request.user

        if not change:
            obj.generate_code()
        return super().save_model(request, obj, form, change)

    def add_view(self, request, form_url="", extra_context=None):
        """
        Customize view: Make sure to get the current configuration ID for which this application session is created.
        """
        if not self._get_config_id(request):
            messages.error(request, "No application configuration selected.")
            return redirect(reverse("admin:api_application_changelist"))

        return super().add_view(request, form_url=form_url, extra_context=extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """
        Customize view: Make sure to get the current configuration ID for which this application session is updated.
        """
        self._cur_change_request = request

        if not self._get_config_id(request):
            messages.error(request, "No application configuration selected.")
            return redirect(reverse("admin:api_application_changelist"))

        return super().change_view(request, object_id=object_id, form_url=form_url, extra_context=extra_context)

    def changelist_view(self, request, extra_context=None):
        """Redirect to application changelist, where configurations are also shown."""
        return redirect(reverse("admin:api_application_changelist"))

    def response_post_save_add(self, request, obj):
        """After saving a new application session, forward to the application changelist."""
        return redirect(reverse("admin:api_application_changelist"))

    def response_post_save_change(self, request, obj):
        """After updating an existing application session, forward to the application changelist."""
        return redirect(reverse("admin:api_application_changelist"))

    def response_delete(self, request, obj_display, obj_id):
        """After deleting an application session, forward to the application changelist."""
        response = super().response_delete(request, obj_display=obj_display, obj_id=obj_id)
        if isinstance(response, HttpResponseRedirect) and response.url == reverse(
            "admin:api_applicationsession_changelist"
        ):
            response = redirect(reverse("admin:api_application_changelist"))
        return response


class ApplicationSessionGateAppSessionsInlineModelChoiceField(forms.ModelChoiceField):
    """
    Custom model choice field for application sessions used in inline admin `ApplicationSessionGateAppSessionsInline`.
    """

    def label_from_instance(self, app_session):
        """Custom label for application session model choice field."""
        label = f"{app_session.config.application.name} / {app_session.config.label} / {app_session.code}"
        if not app_session.is_active:
            label += " (inactive)"
        return label


class ApplicationSessionGateAppSessionsInline(admin.TabularInline):
    """Inline admin for assigning application sessions to gates."""

    model = ApplicationSessionGate.app_sessions.through
    min_num = 1
    extra = 2
    verbose_name = "application session"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "applicationsession":
            kwargs["queryset"] = ApplicationSession.objects.select_related("config", "config__application").order_by(
                "config__application__name", "config__label", "code"
            )
            kwargs["form_class"] = ApplicationSessionGateAppSessionsInlineModelChoiceField

        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ApplicationSessionGateAdmin(admin.ModelAdmin):
    """Model admin for application session gates."""

    fields = ["code", "session_url", "is_active", "label", "description", "updated", "updated_by"]
    readonly_fields = ["code", "session_url", "updated", "updated_by"]
    list_display = ["label", "code", "session_url", "is_active", "sessions_in_gate", "updated", "updated_by"]
    ordering = ["label"]
    inlines = [ApplicationSessionGateAppSessionsInline]

    def __init__(self, model, admin_site):
        super().__init__(model=model, admin_site=admin_site)
        self._cur_changelist_request = None

    def get_queryset(self, request):
        """Custom queryset for more efficient queries."""
        return super().get_queryset(request).select_related("updated_by")

    def get_fields(self, request, obj=None):
        """Customize fields: Remove some fields when creating a new gate."""
        fields = super().get_fields(request, obj=obj)
        if obj is None:
            return [f for f in fields if f not in {"code", "session_url", "updated", "updated_by"}]
        return fields

    @admin.display(ordering=None, description="URL")
    def session_url(self, obj):
        """Custom display field for URL pointing to application with session code attached."""
        if obj is None or not obj.session_url():
            return ""

        if hasattr(settings, "BASE_URL"):
            base_url = settings.BASE_URL
            if base_url.endswith("/"):
                base_url = base_url[:-1]
            sess_url = base_url + obj.session_url()
        else:
            sess_url = self._cur_changelist_request.build_absolute_uri(obj.session_url())

        return mark_safe(f'<a href="{sess_url}" target="_blank">{sess_url}</a>')

    @admin.display(ordering=None, description="Active sessions in gate")
    def sessions_in_gate(self, obj):
        """
        Custom field to show all assigned *active* sessions in each gate. Highlight next session code with bold font.
        """
        links = []
        for i, app_sess_code in enumerate(obj.app_sessions.filter(is_active=True).order_by("code").values("code")):
            app_sess_code = app_sess_code["code"]
            link = (
                f'<a href="{reverse("admin:api_applicationsession_change", args=[app_sess_code])}">'
                f'{app_sess_code}</a>'
            )
            if i == obj.next_forward_index:  # highlight next session code with bold font
                link = f"<strong>{link}</strong>"
            links.append(link)

        return mark_safe(", ".join(links))

    def changelist_view(self, request, extra_context=None):
        self._cur_changelist_request = request
        return super().changelist_view(request, extra_context=extra_context)

    def save_model(self, request, obj, form, change):
        """
        Custom model save method to add current user to the `updated_by` field and generate a gate session code for
        newly created gates.
        """
        obj.updated_by = request.user

        if not change:
            obj.generate_code()
        return super().save_model(request, obj, form, change)


class UserFeedbackAdmin(admin.ModelAdmin):
    """
    Model admin for UserFeedback model.

    This model admin is used as "read-only" admin (apart from the possibility to delete objects) for displaying a list
    of user feedback items for an application, an application config. or application session.
    """

    list_display = ["tracking_session_short", "created", "content_section", "score", "text_truncated"]
    list_display_links = ["created", "content_section", "score", "text_truncated"]
    list_filter = ["created", "content_section", "score"]
    fields = ["tracking_session", "created", "content_section", "score", "text"]
    readonly_fields = ["tracking_session", "created", "content_section", "score", "text"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_list_display(self, request):
        prepend = []

        if hasattr(request, "session"):
            # prepend additional column(s) depending on filter
            filter_by = request.session["filter_by"]
            if filter_by == "application":
                prepend = ["app_config_short", "app_session_short"]
            elif filter_by == "applicationconfig":
                prepend = ["app_session_short"]
            else:
                prepend = []

        return prepend + super().get_list_display(request)

    def get_queryset(self, request):
        """
        Custom queryset for filtering data for a specific application, application configuration or application session.
        """

        qs = super().get_queryset(request)

        if hasattr(request, "session"):
            filter_id = request.session["filter_id"]
            filter_by = request.session["filter_by"]
            if filter_by == "application":
                filterkwargs = dict(user_app_session__application_session__config__application=filter_id)
            elif filter_by == "applicationconfig":
                filterkwargs = dict(user_app_session__application_session__config=filter_id)
            elif filter_by == "applicationsession":
                filterkwargs = dict(user_app_session__application_session=filter_id)
            else:
                raise ValueError(f"unexpected value for `filter_by`: {filter_by}")

            return qs.select_related().filter(**filterkwargs)
        else:
            return qs

    @admin.display(description="Application configuration")
    def app_config_short(self, obj):
        pk = obj.user_app_session.application_session.config.pk
        app_config_url = reverse("multila_admin:api_applicationconfig_change", args=[pk])
        return mark_safe(f'<a href="{app_config_url}">#{pk}')

    @admin.display(description="Application session")
    def app_session_short(self, obj):
        pk = obj.user_app_session.application_session.pk
        app_sess_url = reverse("multila_admin:api_applicationsession_change", args=[pk])
        return mark_safe(f'<a href="{app_sess_url}">{pk}')

    @admin.display(description="Tracking session")
    def tracking_session_short(self, obj):
        if obj.tracking_session:
            tracking_sess_url = reverse("multila_admin:api_trackingsession_change", args=[obj.tracking_session.pk])
            return mark_safe(f'<a href="{tracking_sess_url}">#{obj.tracking_session.pk}')
        else:
            return "-"

    @admin.display(description="Text")
    def text_truncated(self, obj):
        return Truncator(obj.text).words(5)

    @admin.display(description="Application configuration")
    def applicationconfig(self, obj):
        return obj.user_app_session.application_session.config

    @admin.display(description="Application session")
    def applicationsession(self, obj):
        return obj.user_app_session.application_session

    def changelist_view(self, request, extra_context=None):
        """
        Custom change list view that handles the filter parameter and fetches additional data.
        """

        # determine the passed GET parameter used for filtering the user feedback items
        # will also need to remove these parameters from the GET request, otherwise the super() call will produce an
        # error
        request.GET._mutable = True
        filter_id = None
        filter_by = None
        required_get_params = ("application", "applicationconfig", "applicationsession")
        for param in required_get_params:
            try:
                val = request.GET.pop(param + "_id")
                if isinstance(val, list) and len(val) == 1:
                    try:
                        filter_id = int(val[0])
                    except ValueError:
                        filter_id = val[0]
                    filter_by = param
            except KeyError:
                pass

        if not filter_id and hasattr(request, "session") and "filter_id" in request.session:
            filter_id = request.session["filter_id"]
        if not filter_by and hasattr(request, "session") and "filter_by" in request.session:
            filter_by = request.session["filter_by"]

        if not filter_id or not filter_by:
            required_get_params_text = ", ".join(p + "_id" for p in required_get_params)
            raise ValueError(f"this view requires one of the followong GET parameters: {required_get_params_text}")

        if hasattr(request, "session"):
            request.session["filter_id"] = filter_id
            request.session["filter_by"] = filter_by

        if filter_by == "application":
            filter_class = Application
        elif filter_by == "applicationconfig":
            filter_class = ApplicationConfig
        else:  # filter_by == 'applicationsession':
            filter_class = ApplicationSession

        filter_obj = get_object_or_404(filter_class, pk=filter_id)
        extra_context = extra_context or {}
        extra_context.update(
            {"title": "Data manager", "subtitle": f"User feedback items for {filter_obj}", "app_label": "datamanager"}
        )

        return super().changelist_view(request, extra_context=extra_context)


class TrackingSessionAppSessionsFilter(admin.SimpleListFilter):
    """Custom filter used in `TrackingSessionAdmin` for filtering application sessions."""

    title = "Application session"
    parameter_name = "appsession"

    def lookups(self, request, model_admin):
        """Yield tuples of (app. session code, app. session description)."""
        qs = model_admin.get_queryset(request)
        relfield = "user_app_session__application_session"
        for code, descr in qs.values_list(relfield + "__code", relfield + "__description").distinct():
            yield code, f"{code} – {descr}" if descr else code

    def queryset(self, request, queryset):
        """Filter queryset by application session."""
        if self.value():
            return queryset.filter(user_app_session__application_session=self.value())
        return queryset


class TrackingSessionAdmin(admin.ModelAdmin):
    """
    Model admin for TrackingSession model.

    This model admin is used as "read-only" admin (apart from the possibility to delete objects) for displaying a list
    of tracking sessions via the "changelist" action.
    """

    list_display = [
        "tracking_sess_id",
        "app_config_sess",
        "session_url",
        "start_time",
        "end_time",
        "n_events",
        "options",
    ]
    list_select_related = True
    list_filter = [
        "user_app_session__application_session__config__application__name",
        TrackingSessionAppSessionsFilter,
        "start_time",
        "end_time",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(n_events=Count("trackingevent"))

    @admin.display(description="ID")
    def tracking_sess_id(self, obj):
        events_view_url = reverse("multila_admin:api_trackingevent_changelist") + f"?tracking_sess_id={obj.pk}"
        return mark_safe(f'<a href="{events_view_url}">{obj.pk}</a>')

    @admin.display(
        ordering="user_app_session__application_session__config__application__name",
        description="Application / Config / Session",
    )
    def app_config_sess(self, obj):
        """Custom display field combining the application name, configuration label and application session."""
        app_sess = obj.user_app_session.application_session
        app_url = reverse("multila_admin:api_application_change", args=[app_sess.config.application_id])
        config_url = reverse("multila_admin:api_applicationconfig_change", args=[app_sess.config_id])
        sess_url = reverse("multila_admin:api_applicationsession_change", args=[app_sess.code])

        return mark_safe(
            f'<a href="{app_url}">{app_sess.config.application.name}</a> / '
            f'<a href="{config_url}">{app_sess.config.label}</a> / '
            f'<a href="{sess_url}">{app_sess.code}</a>'
        )

    @admin.display(ordering=None, description="URL")
    def session_url(self, obj):
        """Custom display field for URL pointing to application with session code attached."""
        sess_url = obj.user_app_session.application_session.session_url()
        return mark_safe(f'<a href="{sess_url}" target="_blank">{sess_url}</a>')

    @admin.display(ordering="n_events", description="Num. events")
    def n_events(self, obj):
        """Custom display field to show the number of tracked events per tracking session."""
        return obj.n_events

    @admin.display(ordering=None, description="Options")
    def options(self, obj):
        """Event viewer and replay buttons."""
        events_view_url = reverse("multila_admin:api_trackingevent_changelist") + f"?tracking_sess_id={obj.pk}"
        replay_url = reverse("multila_admin:trackingsessions_replay", args=[obj.pk])
        events_view_link = f'<a href="{events_view_url}" style="font-weight:bold;font-size:1.5em">&#8505;</a>&nbsp;'
        if obj.n_events > 0:
            link = f'{events_view_link}<a href="{replay_url}" style="font-weight:bold;font-size:1.5em">&#8634;</a>'
        else:
            link = events_view_link
        return mark_safe(link)


class TrackingEventAdmin(admin.ModelAdmin):
    """
    Model admin for TrackingEvent model.

    This model admin is used as "read-only" admin (apart from the possibility to delete objects) for displaying a list
    of tracking events of a tracking session via the "changelist" action.
    """

    list_display = ["time", "type", "value_formatted"]
    list_filter = ["time", "type"]
    fields = ["tracking_session", ("time", "type"), "value_formatted_rofield"]
    readonly_fields = ["value_formatted_rofield"]
    change_list_template = "admin/trackingevent_change_list.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        """
        Custom queryset for filtering data for a specific tracking session given by `tracking_sess_id` URL parameter.
        """

        qs = super().get_queryset(request)

        tracking_sess_id = request.session.get("tracking_sess_id") if hasattr(request, "session") else None

        if tracking_sess_id is None:
            return qs
        else:
            return qs.filter(tracking_session=tracking_sess_id)

    @admin.display(ordering=None, description="Value (JSON)")
    def value_formatted(self, obj):
        return mark_safe(f"<pre>{format_value_as_json(obj.value, maxlines=5)}</pre>")

    @admin.display(description="Value (JSON)")
    def value_formatted_rofield(self, obj):
        return mark_safe(f"<pre>{format_value_as_json(obj.value)}</pre>")

    def changelist_view(self, request, extra_context=None):
        tracking_sess_id = None

        try:
            # need to get and remove the tracking_sess_id parameter (if given), otherwise the super() call will produce
            # an error
            request.GET._mutable = True
            param = request.GET.pop("tracking_sess_id")
            if isinstance(param, list) and len(param) == 1:
                tracking_sess_id = int(param[0])
        except (TypeError, KeyError):
            pass

        if tracking_sess_id is None:
            tracking_sess_id_from_session = (
                request.session.get("tracking_sess_id") if hasattr(request, "session") else None
            )

            if tracking_sess_id_from_session is None:
                return super().changelist_view(request, extra_context=extra_context)
            else:
                tracking_sess_id = tracking_sess_id_from_session

        if hasattr(request, "session"):
            request.session["tracking_sess_id"] = tracking_sess_id

        tracking_sess = get_object_or_404(TrackingSession.objects.select_related(), pk=tracking_sess_id)
        extra_context = extra_context or {}
        extra_context.update(
            {
                "title": "Data manager",
                "subtitle": f"Tracking events for tracking session #{tracking_sess_id}",
                "app_label": "datamanager",
                "tracking_sess": tracking_sess,
            }
        )

        return super().changelist_view(request, extra_context=extra_context)


# --- custom admin site ---


def _path_to_export_file(file):
    """
    Helper function to get absolute path to exportable `file`. If `file` does not exist at data export directory or
    `file` string is malformed, return a HTTP error response.
    """
    if not file or file.startswith(".") or file.count(".") != 1:
        return HttpResponseForbidden()

    fpath = os.path.join(settings.DATA_EXPORT_DIR, file)

    if not os.path.exists(fpath):
        return HttpResponseNotFound()

    return fpath


class MultiLAAdminSite(admin.AdminSite):
    """
    Custom administration site.
    """

    site_header = "MultiLA Administration Interface"
    site_url = None  # disable "view on site" link since there is no real frontend

    def get_urls(self):
        """
        Expand admin URLs by custom views.
        """
        urls = super().get_urls()
        custom_urls = [
            path("data/view/", self.admin_view(self.dataview), name="dataview"),
            path("data/export/", self.admin_view(self.dataexport), name="dataexport"),
            path("data/export_filelist/", self.admin_view(self.dataexport_filelist), name="dataexport_filelist"),
            re_path(
                r"^data/export_download/(?P<file>[\w._-]+)$",
                self.admin_view(self.dataexport_download),
                name="dataexport_download",
            ),
            re_path(
                r"^data/export_delete/(?P<file>[\w._-]+)$",
                self.admin_view(self.dataexport_delete),
                name="dataexport_delete",
            ),
            path(
                "data/trackingsession/replay/<int:tracking_sess_id>",
                self.admin_view(self.trackingsession_replay),
                name="trackingsessions_replay",
            ),
            path(
                "data/trackingsession/replay/<int:tracking_sess_id>/chunk/<int:i>",
                self.admin_view(self.trackingsession_replay_datachunk),
                name="trackingsession_replay_datachunk",
            ),
        ]
        return custom_urls + urls

    def get_app_list(self, request, app_label=None):
        """
        Expand app list by custom views for a "data manager".
        """

        # first, remove the TrackingSession admin from the default admin menu, because we will list it under the
        # custom "datamanager" app
        app_list = super().get_app_list(request)
        remove_apps = {"ApplicationConfig", "ApplicationSession", "UserFeedback", "TrackingSession", "TrackingEvent"}
        for app in app_list:
            if app["app_label"] == "api":
                app["models"] = [m for m in app["models"] if m["object_name"] not in remove_apps]

        # add a custom data manager app
        datamanager_app = {
            "name": "Data manager",
            "app_label": "datamanager",
            "app_url": reverse("multila_admin:dataview"),
            "has_module_perms": request.user.is_superuser,
            "models": [  # "models" actually refer only to custom views – there are no specific datamanager models
                {
                    "model": None,
                    "object_name": "View",
                    "name": "View",
                    "perms": {"add": False, "change": False, "delete": False, "view": True},
                    "admin_url": reverse("multila_admin:dataview"),
                    "add_url": None,
                },
                {
                    "model": None,
                    "object_name": "Export",
                    "name": "Export",
                    "perms": {"add": False, "change": False, "delete": False, "view": True},
                    "admin_url": reverse("multila_admin:dataexport"),
                    "add_url": None,
                },
                {
                    "model": TrackingSession,
                    "object_name": "TrackingSession",
                    "name": "Tracking sessions",
                    "perms": {"add": False, "change": False, "delete": False, "view": True},
                    "admin_url": reverse("multila_admin:api_trackingsession_changelist"),
                    "add_url": None,
                },
            ],
        }

        app_list.append(datamanager_app)

        return app_list

    def dataview(self, request):
        """
        Custom view for "dataview", i.e. overview of collected data.
        """

        # data formatting functions for the table; see column_formating below
        def default_format(v, _=None, __=None):
            return "-" if v is None else v

        def format_rounded(v, _, __):
            if v is None:
                return default_format(v)
            else:
                return str(round(v, 2))

        def format_timedelta(t, _, __):
            if t is None:
                return default_format(t)
            return (
                f"{int(t.total_seconds() // 60)}min "
                f"{round((t.total_seconds() / 60 - t.total_seconds() // 60) * 60)}s"
            )

        def format_datetime(t, _, __):
            if t is None:
                return default_format(t)
            return utc_to_default_tz(t).strftime("%Y-%m-%d %H:%M:%S")

        def format_app_config(v, row, __):
            if v is None:
                return default_format(v)
            app_config_url = reverse("multila_admin:api_applicationconfig_change", args=[row["applicationconfig"]])
            return mark_safe(f"<a href={app_config_url}>{v}</a>")

        def format_app_session(v, row, __):
            if v is None:
                return default_format(v)
            app_config_url = reverse(
                "multila_admin:api_applicationsession_change", args=[row["applicationconfig__applicationsession"]]
            )
            return mark_safe(f"<a href={app_config_url}>{v}</a>")

        def format_userfeedback_link(v, row, groupby):
            if v == 0:
                return default_format(v)

            userfeedback_url = reverse("multila_admin:api_userfeedback_changelist") + "?"

            if groupby == "app":
                userfeedback_url += f"application_id={row['id']}"
            elif groupby == "app_config":
                userfeedback_url += f"applicationconfig_id={row['applicationconfig']}"
            elif groupby == "app_session":
                userfeedback_url += f"applicationsession_id={row['applicationconfig__applicationsession']}"
            else:
                raise ValueError(f"unexpected `groupby` value: {groupby}")

            return mark_safe(f"<a href={userfeedback_url}>&#8505; {v}</a>")

        # aggregation level choices for form
        configform_groupby_choices = [
            ("app", "Application"),
            ("app_config", "Application config."),
            ("app_session", "Application session"),
        ]

        # map field names to "human readable" column names
        column_descriptions = {
            "applicationconfig__label": "Application config.",
            "applicationconfig__applicationsession__code": "App. session code",
            "applicationconfig__applicationsession__auth_mode": "Auth. mode",
            "n_users": "Total num. users",
            "n_nonanon_users": "Registered users",
            "n_nonanon_logins": "Logins of registered users",
            "n_feedback": "Num. of feedback items",
            "avg_feedback_score": "Avg. feedback score",
            "n_trackingsess": "Num. tracking sessions",
            "most_recent_trackingsess": "Most recently started tracking session",
            "avg_trackingsess_duration": "Avg. tracking session duration",
            "n_events": "Num. recorded tracking events",
            "most_recent_event": "Most recently recorded event",
        }

        # map field names to custom formating functions
        column_formating = {
            "n_feedback": format_userfeedback_link,
            "avg_feedback_score": format_rounded,
            "avg_trackingsess_duration": format_timedelta,
            "most_recent_trackingsess": format_datetime,
            "most_recent_event": format_datetime,
            "applicationconfig__label": format_app_config,
            "applicationconfig__applicationsession__code": format_app_session,
        }

        # form to select the aggregation level
        class ConfigForm(forms.Form):
            groupby = forms.ChoiceField(label="Group by", choices=configform_groupby_choices, required=False)

        # handle request
        if request.method == "POST":  # form was submitted: populate with submitted data
            configform = ConfigForm(request.POST)

            if configform.is_valid():  # store submitted data in session
                request.session["dataview_configform"] = configform.cleaned_data
        else:  # form was not submitted: populate with default values stored in sesssion
            configform = ConfigForm(request.session.get("dataview_configform", {}))

        # get aggregation level "groupby" stored in session
        viewconfig = request.session.get("dataview_configform", {})
        groupby = viewconfig.get("groupby", configform_groupby_choices[0][0])

        # table expressions
        usersess_expr = "applicationconfig__applicationsession__userapplicationsession"
        trackingsess_expr = usersess_expr + "__trackingsession"
        trackingevent_expr = trackingsess_expr + "__trackingevent"
        userfeedback_expr = usersess_expr + "__userfeedback"

        # fields that are always fetched
        toplevel_fields = ["id", "name", "url"]
        stats_fields = [
            "n_users",
            "n_nonanon_users",
            "n_nonanon_logins",
            "n_feedback",
            "avg_feedback_score",
            "n_trackingsess",
            "most_recent_trackingsess",
            "avg_trackingsess_duration",
            "n_events",
            "most_recent_event",
        ]

        # fields specific for aggregation level
        if groupby == "app":
            group_fields = []
        elif groupby == "app_config":
            group_fields = ["applicationconfig__label", "applicationconfig"]
        elif groupby == "app_session":
            group_fields = [
                "applicationconfig__label",
                "applicationconfig",
                "applicationconfig__applicationsession__code",
                "applicationconfig__applicationsession__auth_mode",
                "applicationconfig__applicationsession",
            ]
        else:
            raise ValueError(f'invalid value for "groupby": {groupby}')

        # all fields that are fetched
        data_fields = toplevel_fields + group_fields + stats_fields
        # fields used for sorting
        order_fields = toplevel_fields + group_fields
        # fields that are fetched but that should not be displayed
        hidden_fields = set(toplevel_fields) | {"applicationconfig", "applicationconfig__applicationsession"}

        # fetch the data from the DB
        data_rows = (
            Application.objects.annotate(
                n_users=Count(usersess_expr, distinct=True),
                n_nonanon_users=Count(usersess_expr + "__user", distinct=True),
                n_nonanon_logins=Count(usersess_expr + "__user", distinct=False),
                n_feedback=Count(userfeedback_expr, distinct=True),
                avg_feedback_score=Avg(userfeedback_expr + "__score"),
                n_trackingsess=Count(trackingsess_expr, distinct=True),
                most_recent_trackingsess=Max(trackingsess_expr + "__start_time"),
                avg_trackingsess_duration=Avg(
                    F(trackingsess_expr + "__end_time") - F(trackingsess_expr + "__start_time")
                ),
                n_events=Count(trackingevent_expr, distinct=True),
                most_recent_event=Max(trackingevent_expr + "__time"),
            )
            .order_by(*order_fields)
            .values(*data_fields)
        )

        # format the data for display
        # `table_data` is a dict for grouped table data per app; maps tuple (app name, app url) to formatted data rows
        table_data = defaultdict(list)
        for row in data_rows:
            formatted_row = [
                column_formating.get(k, default_format)(row[k], row, groupby)
                for k in data_fields
                if k not in hidden_fields
            ]
            table_data[(row["name"], row["url"])].append(formatted_row)

        # set template variables
        context = {
            **self.each_context(request),
            "title": "Data manager",
            "subtitle": "Data view",
            "app_label": "datamanager",
            "configform": configform,
            "table_columns": [column_descriptions[k] for k in data_fields if k in column_descriptions],
            "table_data": table_data.items(),
        }

        request.current_app = self.name

        return TemplateResponse(request, "admin/dataview.html", context)

    def dataexport_filelist(self, request):
        """
        JSON endpoint for data export file table. Returns list of files along with their status as list of tuples with
        `(file_name, file_status)`, where `file_status` is a bool. For this value, true indicates that the file is ready
        to be downloaded, false indicates that the file is currently being generated.
        """
        # finished files are those from the data export directory
        finished = set(map(os.path.basename, glob(os.path.join(settings.DATA_EXPORT_DIR, "*.zip"))))

        # add the files that are currently being generated to form the set of all files
        all_files = sorted(finished | set(request.session["dataexport_awaiting_files"]))

        # generate response data as list of tuples `(file_name, file_status)`
        response_data = [(f, f in finished) for f in all_files]

        # remove already finished files from the set of files are being generated
        request.session["dataexport_awaiting_files"] = list(
            set(request.session["dataexport_awaiting_files"]) - finished
        )

        return JsonResponse(response_data, safe=False)

    def dataexport_download(self, request, file):
        """
        Data download view. Allows to download the exported `file`. Should be wrapped as `admin_view` for permission
        checking. Returns a `FileResponse`.
        """
        fpath_or_failresponse = _path_to_export_file(file)
        if isinstance(fpath_or_failresponse, HttpResponse):
            return fpath_or_failresponse

        return FileResponse(open(fpath_or_failresponse, "rb"), as_attachment=True)

    def dataexport_delete(self, request, file):
        """
        Data deletion view. Allows to delete the exported `file`. Returns an empty HTTP 200 response.
        """
        request.session["dataexport_awaiting_files"] = [
            f for f in request.session["dataexport_awaiting_files"] if f != file
        ]

        fpath_or_failresponse = _path_to_export_file(file)
        if isinstance(fpath_or_failresponse, HttpResponse):
            return fpath_or_failresponse

        # delete
        os.unlink(fpath_or_failresponse)

        return HttpResponse(status=200)

    def dataexport(self, request):
        """
        Data export view. Allows to generate data exports that can be generated. Lists all generated data exports.
        """

        def create_export(fname, app_sess):
            """
            Internal function to generate a data export file named `fname` for an application session `app_sess`.
            If `app_sess` is empty or None, we will generate a data export for all application sessions.
            """
            # create a temporary directory; all generated data will at first be placed in this directory; the final
            # data will then be moved to the export directory
            tmpdir = tempfile.mkdtemp()

            # map SQL query field names to CSV column names
            queries_and_fields = {
                "app_sessions": (
                    "SELECT {fields} FROM api_application a "
                    "LEFT JOIN api_applicationconfig ac ON a.id = ac.application_id "
                    "LEFT JOIN api_applicationsession asess ON ac.id = asess.config_id",
                    "WHERE asess.code = %s",
                    (
                        ("a.id", "app_id"),
                        ("a.name", "app_name"),
                        ("a.url", "app_url"),
                        ("ac.id", "app_config_id"),
                        ("ac.label", "app_config_label"),
                        ("asess.code", "app_sess_code"),
                        ("asess.auth_mode", "app_sess_auth_mode"),
                    ),
                ),
                "tracking_sessions": (
                    "SELECT {fields} FROM api_userapplicationsession ua "
                    "LEFT JOIN api_trackingsession t ON ua.id = t.user_app_session_id",
                    "WHERE ua.application_session_id = %s",
                    (
                        ("ua.application_session_id", "app_sess_code"),
                        ("ua.code", "user_app_sess_code"),
                        ("ua.user_id", "user_app_sess_user_id"),
                        ("t.id", "track_sess_id"),
                        ("t.start_time", "track_sess_start"),
                        ("t.end_time", "track_sess_end"),
                        ("t.device_info", "track_sess_device_info"),
                    ),
                ),
                "user_feedback": (
                    "SELECT {fields} FROM api_userfeedback fb "
                    "LEFT JOIN api_userapplicationsession ua ON fb.user_app_session_id = ua.id",
                    "WHERE ua.application_session_id = %s",
                    (
                        ("ua.application_session_id", "app_sess_code"),
                        ("ua.code", "user_app_sess_code"),
                        ("ua.user_id", "user_app_sess_user_id"),
                        ("fb.tracking_session_id", "track_sess_id"),
                        ("fb.created", "feedback_created"),
                        ("fb.content_section", "feedback_content_section"),
                        ("fb.score", "feedback_score"),
                        ("fb.text", "feedback_text"),
                    ),
                ),
                "tracking_events": (
                    "SELECT {fields} FROM api_trackingevent e "
                    "LEFT JOIN api_trackingsession t ON e.tracking_session_id = t.id "
                    "LEFT JOIN api_userapplicationsession ua ON t.user_app_session_id = ua.id",
                    "WHERE ua.application_session_id = %s",
                    (
                        ("t.id", "track_sess_id"),
                        ("e.time", "event_time"),
                        ("e.type", "event_type"),
                        ("e.value", "event_value"),
                    ),
                ),
            }

            def format_if_datetime(x):
                if isinstance(x, datetime):
                    return utc_to_default_tz(x).strftime("%Y-%m-%dT%H:%M:%S.%f%z")
                else:
                    return x

            # write CSVs for the data from the queries defined in `queries_and_fields`
            stored_csvs = []
            for csvname, (query_template, query_filter, query_fields) in queries_and_fields.items():
                query_select = ",".join(f"{sqlfield} AS {csvfield}" for sqlfield, csvfield in query_fields)
                query = query_template.format(fields=query_select)
                if app_sess:
                    query += " " + query_filter

                # write the output
                tmpfpath = os.path.join(tmpdir, csvname + ".csv")
                stored_csvs.append(tmpfpath)
                with open(tmpfpath, "w", newline="") as f:
                    csvwriter = csv.writer(f)
                    csvwriter.writerow(list(zip(*query_fields))[1])  # header

                    with db_conn.cursor() as cur:
                        cur.execute(query, [app_sess] if app_sess else [])

                        for dbrow in cur.fetchall():
                            csvwriter.writerow(map(format_if_datetime, dbrow))

            # add all CSVs to a ZIP file
            assert fname.endswith(".zip"), "file to be exported must end with .zip"
            zipfilepath = os.path.join(tmpdir, fname)
            with ZipFile(zipfilepath, "w", compression=ZIP_DEFLATED, compresslevel=9) as f:
                for csvfile in stored_csvs:
                    f.write(csvfile, arcname=os.path.basename(csvfile))
                f.write(settings.CODEBOOK_PATH, arcname=os.path.basename(settings.CODEBOOK_PATH))

            # move the final data to the export directory
            if not os.path.exists(settings.DATA_EXPORT_DIR):
                os.mkdir(settings.DATA_EXPORT_DIR, 0o755)
            shutil.move(zipfilepath, os.path.join(settings.DATA_EXPORT_DIR, fname))

        # get all application sessions
        app_sess_objs = ApplicationSession.objects.values(
            "config__application__name", "config__application__url", "config__label", "code", "auth_mode"
        ).order_by("config__application__name", "config__label", "auth_mode")

        # generate options to select an application session in the form
        app_sess_opts = [("", "– all –")] + [
            (
                sess["code"],
                f'{sess["config__application__name"]} '
                f'/ {sess["config__label"]} /  {sess["code"]} (auth. mode {sess["auth_mode"]})',
            )
            for sess in app_sess_objs
        ]

        # export config. form
        class ConfigForm(forms.Form):
            app_sess_select = forms.ChoiceField(label="Application session", choices=app_sess_opts, required=False)

        # prepare session data
        if "dataexport_awaiting_files" not in request.session:
            request.session["dataexport_awaiting_files"] = []

        if request.method == "POST":  # handle form submit; pass submitted data to form and generate data export
            configform = ConfigForm(request.POST)

            if configform.is_valid():
                request.session["dataexport_configform"] = configform.cleaned_data

                # get selected app session
                app_sess_select = request.session["dataexport_configform"]["app_sess_select"]

                # generate a file name
                fname = (
                    f'{datetime.now(ZoneInfo(settings.TIME_ZONE)).strftime("%Y-%m-%d_%H%M%S")}_'
                    f'{app_sess_select or "all"}.zip'
                )

                # add the file name to the list of files that are being generated
                request.session["dataexport_awaiting_files"].append(fname)

                # start a background thread that will generate the data export
                threading.Thread(target=create_export, args=[fname, app_sess_select], daemon=True).start()
        else:  # no form submit; only display exported data
            configform = ConfigForm(request.session.get("dataexport_configform", {}))

        # set template variables
        context = {
            **self.each_context(request),
            "title": "Data manager",
            "subtitle": "Data export",
            "app_label": "datamanager",
            "configform": configform,
        }

        request.current_app = self.name

        return TemplateResponse(request, "admin/dataexport.html", context)

    def trackingsession_replay(self, request, tracking_sess_id):
        """
        Tracking session replay view for a tracking session identified via `tracking_sess_id`.
        """
        tracking_sess = get_object_or_404(TrackingSession.objects.select_related(), pk=tracking_sess_id)

        # determine valid iframe origin: the scheme+host of the application session URL that will be embedded
        sess_url = tracking_sess.user_app_session.application_session.session_url()
        sess_url_parts = list(urlsplit(sess_url))
        allowed_iframe_origin = urlunsplit(sess_url_parts[:2] + [""] * 3)

        # set template variables
        context = {
            **self.each_context(request),
            "title": "Data manager",
            "subtitle": f"Tracking session replay for tracking session #{tracking_sess_id}",
            "app_label": "datamanager",
            "tracking_sess": tracking_sess,
            "app_config": tracking_sess.user_app_session.application_session.config.config,
            "allowed_iframe_origin": allowed_iframe_origin,
        }

        request.current_app = self.name

        return TemplateResponse(request, "admin/trackingsessions_replay.html", context)

    def trackingsession_replay_datachunk(self, request, tracking_sess_id, i):
        """
        Tracking session replay view for fetching the `i`th replay data chunk of the tracking session identified
        by `tracking_sess_id`. Returns a JSON response.
        """

        if i == 0:  # for the first chunk, also add the information about how many chunks there are
            n_chunks = len(TrackingEvent.objects.filter(tracking_session=tracking_sess_id, type="mouse"))
        else:
            n_chunks = None

        try:
            event = TrackingEvent.objects.filter(tracking_session=tracking_sess_id, type="mouse").order_by("time")[i]
        except IndexError:
            return JsonResponse({})

        # filter_frametypes = {'m', 'c', 's', 'i', 'o'}
        # filter_frametypes = {'m'}
        replaydata = event.value
        # startframe = 0
        # for f_index, f in enumerate(replaydata['frames']):
        #     if f[0] in filter_frametypes:
        #         startframe = f_index
        #         break
        #
        # replaydata['frames'] = replaydata['frames'][startframe:]
        filter_frametypes = {"m", "c", "s", "S", "i", "o"}
        replaydata["frames"] = [f for f in replaydata["frames"] if f[0] in filter_frametypes]
        replaydata["frames"] = sorted(replaydata["frames"], key=lambda f: f[-1])  # sort by last item in frame (time)

        return JsonResponse({"i": i, "replaydata": replaydata, "n_chunks": n_chunks})


admin_site = MultiLAAdminSite(name="multila_admin")

# register model admins
admin_site.register(Application, ApplicationAdmin)
admin_site.register(ApplicationConfig, ApplicationConfigAdmin)
admin_site.register(ApplicationSession, ApplicationSessionAdmin)
admin_site.register(ApplicationSessionGate, ApplicationSessionGateAdmin)
admin_site.register(UserFeedback, UserFeedbackAdmin)
admin_site.register(TrackingSession, TrackingSessionAdmin)
admin_site.register(TrackingEvent, TrackingEventAdmin)
admin_site.register(User, UserAdmin)
admin_site.register(Group, GroupAdmin)
