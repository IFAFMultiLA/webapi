"""
Admin backend.

ModelAdmin classes to generate the django administration backend.

.. codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""
import csv
import os.path
import shutil
import threading
import tempfile
from collections import defaultdict
from glob import glob
from datetime import datetime
from zipfile import ZipFile, ZIP_DEFLATED
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, urlunsplit
import json

from django import forms
from django.contrib import admin
from django.contrib.auth.models import User, Group
from django.db.models import Count, Max, Avg, F
from django.db import connection as db_conn
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseNotFound, FileResponse, HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse, re_path
from django.utils.safestring import mark_safe
from django.shortcuts import get_object_or_404
from django.conf import settings

from .models import Application, ApplicationConfig, ApplicationSession, TrackingSession, TrackingEvent


DEFAULT_TZINFO = ZoneInfo(settings.TIME_ZONE)


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

    formatted_lines = json.dumps(value, indent=2).split('\n')
    if maxlines is not None and len(formatted_lines) > maxlines:
        formatted_lines = formatted_lines[:maxlines] + ['...']

    return '\n'.join(formatted_lines)


class JSONEncoderWithIdent(json.JSONEncoder):
    def __init__(self, *args, indent, **kwargs):
        super().__init__(*args, indent=2, **kwargs)


# --- model admins ---


class ApplicationAdmin(admin.ModelAdmin):
    """
    Admin for Application model.
    """
    fields = ['name', 'url', 'default_application_session', 'updated', 'updated_by']
    readonly_fields = ['updated', 'updated_by']
    list_display = ['name', 'url', 'default_application_session', 'updated', 'updated_by']

    def save_model(self, request, obj, form, change):
        """
        Custom model save method to add current user to the `updated_by` field.
        """
        obj.updated_by = request.user
        return super().save_model(request, obj, form, change)

    def get_fields(self, request, obj=None):
        fields = self.fields
        if obj:
            # on update return all fields
            return fields
        else:
            # on create, don't show "default application session" field, as we can't possibly have created any
            # application session for this application, yet
            return [f for f in fields if f != 'default_application_session']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('default_application_session__config')

    def get_form(self, request, obj=None, change=False, **kwargs):
        form = super().get_form(request, obj=obj, change=change, **kwargs)

        if obj:
            # on update, set up the "default application session" field to fetch all related application sessions
            # as options for selection
            form.base_fields['default_application_session'].required = False
            form.base_fields['default_application_session'].queryset = \
                ApplicationSession.objects.filter(config__application=obj)

        return form


class ApplicationConfigAdmin(admin.ModelAdmin):
    """
    Admin for ApplicationConfig model.
    """
    fields = ['application', 'label', 'config', 'updated', 'updated_by']
    readonly_fields = ['updated', 'updated_by']
    list_display = ['label', 'application_name', 'updated', 'updated_by']
    list_display_links = ['label', 'application_name']
    list_select_related = True   # for application.name

    @admin.display(ordering='application__name', description='Application')
    def application_name(self, obj):
        """
        Custom display field for name of the application for this configuration.
        """
        return obj.application.name

    def save_model(self, request, obj, form, change):
        """
        Custom model save method to add current user to the `updated_by` field.
        """
        obj.updated_by = request.user
        return super().save_model(request, obj, form, change)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """
        Adjust form fields for specific model fields.
        """
        if db_field.name == 'config':
            # pretty formatting for configuration JSON field
            kwargs['encoder'] = JSONEncoderWithIdent
        return super().formfield_for_dbfield(db_field, request, **kwargs)


class ApplicationSessionAdmin(admin.ModelAdmin):
    """
    Admin for ApplicationSession model.
    """
    fields = ['code', 'session_url', 'config', 'auth_mode', 'updated', 'updated_by']
    readonly_fields = ['code', 'session_url', 'updated', 'updated_by']
    list_display = ['code', 'config_label', 'session_url', 'auth_mode', 'updated', 'updated_by']
    list_select_related = True  # for config and config.application

    @admin.display(ordering='config__application__name', description='Application configuration')
    def config_label(self, obj):
        """Custom display field combining the application name and the configuration label."""
        return f'{obj.config.application.name} / {obj.config.label}'

    @admin.display(ordering=None, description='URL')
    def session_url(self, obj):
        """Custom display field for URL pointing to application with session code attached."""
        sess_url = obj.session_url()
        return mark_safe(f'<a href="{sess_url}" target="_blank">{sess_url}</a>')

    def save_model(self, request, obj, form, change):
        """
        Custom model save method to add current user to the `updated_by` field and generate a session code for newly
        created sessions.
        """
        obj.updated_by = request.user

        if not change:
            obj.generate_code()
        return super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Adjust form field for specific foreign key model fields.
        """
        if db_field.name == "config":
            # queryset for application configuration choices
            kwargs['queryset'] = ApplicationConfig.objects.select_related('application')\
                .order_by('application__name', 'label')

        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class TrackingSessionAdmin(admin.ModelAdmin):
    """
    Model admin for TrackingSession model.

    This model admin is used as "read-only" admin for displaying a list of tracking sessions via the "changelist"
    action.
    """
    list_display = ['app_config_sess', 'session_url', 'start_time', 'end_time', 'n_events', 'options']
    list_select_related = True
    list_filter = ['user_app_session__application_session__config__application__name', 'start_time', 'end_time']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(n_events=Count('trackingevent'))

    @admin.display(ordering='user_app_session__application_session__config__application__name',
                   description='Application / Config / Session')
    def app_config_sess(self, obj):
        """Custom display field combining the application name, configuration label and application session."""
        app_sess = obj.user_app_session.application_session
        app_url = reverse('multila_admin:api_application_change', args=[app_sess.config.application_id])
        config_url = reverse('multila_admin:api_applicationconfig_change', args=[app_sess.config_id])
        sess_url = reverse('multila_admin:api_applicationsession_change', args=[app_sess.code])

        return mark_safe(f'<a href="{app_url}">{app_sess.config.application.name}</a> / '
                         f'<a href="{config_url}">{app_sess.config.label}</a> / '
                         f'<a href="{sess_url}">{app_sess.code}</a>')

    @admin.display(ordering=None, description='URL')
    def session_url(self, obj):
        """Custom display field for URL pointing to application with session code attached."""
        sess_url = obj.user_app_session.application_session.session_url()
        return mark_safe(f'<a href="{sess_url}" target="_blank">{sess_url}</a>')

    @admin.display(ordering='n_events', description='Num. events')
    def n_events(self, obj):
        """Custom display field to show the number of tracked events per tracking session."""
        return obj.n_events

    @admin.display(ordering=None, description='Options')
    def options(self, obj):
        """Event viewer and replay buttons."""
        events_view_url = reverse('multila_admin:api_trackingevent_changelist') + f'?tracking_sess_id={obj.pk}'
        replay_url = reverse('multila_admin:trackingsessions_replay', args=[obj.pk])
        if obj.n_events > 0:
            return mark_safe(f'<a href="{events_view_url}" style="font-weight:bold;font-size:1.5em">&#8505;</a>&nbsp;'
                             f'<a href="{replay_url}" style="font-weight:bold;font-size:1.5em">&#8634;</a>')
        else:
            return '-'


class TrackingEventAdmin(admin.ModelAdmin):
    """
    Model admin for TrackingEvent model.

    This model admin is used as "read-only" admin for displaying a list of tracking events of a tracking session via the
    "changelist" action.
    """
    list_display = ['time', 'type', 'value_formatted']
    list_filter = ['time', 'type']
    fields = ['tracking_session', ('time', 'type'), 'value_formatted_rofield']
    readonly_fields = ['value_formatted_rofield']
    change_list_template = "admin/trackingevent_change_list.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        """
        Custom queryset for filtering data for a specific tracking session given by `tracking_sess_id` URL parameter.
        """

        qs = super().get_queryset(request)

        tracking_sess_id = request.session.get('tracking_sess_id')

        if tracking_sess_id is None:
            return tracking_sess_id
        else:
            return qs.filter(tracking_session=tracking_sess_id)

    @admin.display(ordering=None, description='Value (JSON)')
    def value_formatted(self, obj):
        return mark_safe(f"<pre>{format_value_as_json(obj.value, maxlines=5)}</pre>")

    @admin.display(description='Value (JSON)')
    def value_formatted_rofield(self, obj):
        return mark_safe(f"<pre>{format_value_as_json(obj.value)}</pre>")

    def changelist_view(self, request, extra_context=None):
        tracking_sess_id = None

        try:
            # need to get and remove the tracking_sess_id parameter (if given), otherwise the super() call will produce
            # an error
            request.GET._mutable = True
            param = request.GET.pop('tracking_sess_id')
            if isinstance(param, list) and len(param) == 1:
                tracking_sess_id = int(param[0])
        except (TypeError, KeyError):
            pass

        if tracking_sess_id is None:
            tracking_sess_id_from_session = request.session.get('tracking_sess_id')

            if tracking_sess_id_from_session is None:
                return super().changelist_view(request, extra_context=extra_context)
            else:
                tracking_sess_id = tracking_sess_id_from_session

        request.session['tracking_sess_id'] = tracking_sess_id

        tracking_sess = get_object_or_404(TrackingSession.objects.select_related(), pk=tracking_sess_id)
        extra_context = extra_context or {}
        extra_context.update({
            "title": "Data manager",
            "subtitle": f"Tracking events for tracking session #{tracking_sess_id}",
            "app_label": "datamanager",
            "tracking_sess": tracking_sess
        })

        return super().changelist_view(request, extra_context=extra_context)


# --- custom admin site ---


def _path_to_export_file(file):
    """
    Helper function to get absolute path to exportable `file`. If `file` does not exist at data export directory or
    `file` string is malformed, return a HTTP error response.
    """
    if not file or file.startswith('.') or file.count('.') != 1:
        return HttpResponseForbidden()

    fpath = os.path.join(settings.DATA_EXPORT_DIR, file)

    if not os.path.exists(fpath):
        return HttpResponseNotFound()

    return fpath


class MultiLAAdminSite(admin.AdminSite):
    """
    Custom administration site.
    """
    site_header = 'MultiLA Administration Interface'
    site_url = None   # disable "view on site" link since there is no real frontend

    def get_urls(self):
        """
        Expand admin URLs by custom views.
        """
        urls = super().get_urls()
        custom_urls = [
            path('data/view/',
                 self.admin_view(self.dataview),
                 name='dataview'),
            path('data/export/',
                 self.admin_view(self.dataexport),
                 name='dataexport'),
            path('data/export_filelist/',
                 self.admin_view(self.dataexport_filelist),
                 name='dataexport_filelist'),
            re_path(r'^data/export_download/(?P<file>[\w._-]+)$',
                    self.admin_view(self.dataexport_download),
                    name='dataexport_download'),
            re_path(r'^data/export_delete/(?P<file>[\w._-]+)$',
                    self.admin_view(self.dataexport_delete),
                    name='dataexport_delete'),
            path('data/trackingsession/replay/<int:tracking_sess_id>',
                 self.admin_view(self.trackingsession_replay),
                 name='trackingsessions_replay'),
            path('data/trackingsession/replay/<int:tracking_sess_id>/chunk/<int:i>',
                 self.admin_view(self.trackingsession_replay_datachunk),
                 name='trackingsession_replay_datachunk'),
        ]
        return custom_urls + urls

    def get_app_list(self, request, app_label=None):
        """
        Expand app list by custom views for a "data manager".
        """

        # first, remove the TrackingSession admin from the default admin menu, because we will list it under the
        # custom "datamanager" app
        app_list = super().get_app_list(request)
        for app in app_list:
            if app['app_label'] == 'api':
                app['models'] = [m for m in app['models']
                                 if m['object_name'] not in {'TrackingSession', 'TrackingEvent'}]

        # add a custom data manager app
        datamanager_app = {
            'name': 'Data manager',
            'app_label': 'datamanager',
            'app_url': reverse('multila_admin:dataview'),
            'has_module_perms': request.user.is_superuser,
            'models': [   # "models" actually refer only to custom views – there are no specific datamanager models
                {
                    'model': None,
                    'object_name': 'View',
                    'name': 'View',
                    'perms': {'add': False, 'change': False, 'delete': False, 'view': True},
                    'admin_url': reverse('multila_admin:dataview'),
                    'add_url': None
                },
                {
                    'model': None,
                    'object_name': 'Export',
                    'name': 'Export',
                    'perms': {'add': False, 'change': False, 'delete': False, 'view': True},
                    'admin_url': reverse('multila_admin:dataexport'),
                    'add_url': None
                },
                {
                    'model': TrackingSession,
                    'object_name': 'TrackingSession',
                    'name': 'Tracking sessions',
                    'perms': {'add': False, 'change': False, 'delete': False, 'view': True},
                    'admin_url': reverse('multila_admin:api_trackingsession_changelist'),
                    'add_url': None
                }
            ]
        }

        app_list.append(datamanager_app)

        return app_list

    def dataview(self, request):
        """
        Custom view for "dataview", i.e. overview of collected data.
        """

        # data formatting functions for the table; see COLUMN_FORMATING below
        def default_format(v, _=None):
            return '-' if v is None else v

        def format_timedelta(t, _):
            if t is None:
                return default_format(t)
            return f'{int(t.total_seconds() // 60)}min ' \
                   f'{round((t.total_seconds() / 60 - t.total_seconds() // 60) * 60)}s'

        def format_datetime(t, _):
            if t is None:
                return default_format(t)
            return utc_to_default_tz(t).strftime('%Y-%m-%d %H:%M:%S')

        def format_app_config(v, row):
            if v is None:
                return default_format(v)
            app_config_url = reverse("multila_admin:api_applicationconfig_change", args=[row["applicationconfig"]])
            return mark_safe(f'<a href={app_config_url}>{v}</a>')

        def format_app_session(v, row):
            if v is None:
                return default_format(v)
            app_config_url = reverse("multila_admin:api_applicationsession_change",
                                     args=[row["applicationconfig__applicationsession"]])
            return mark_safe(f'<a href={app_config_url}>{v}</a>')

        # aggregation level choices for form
        CONFIGFORM_GROUPBY_CHOICES = [
            ('app', 'Application'),
            ('app_config', 'Application config.'),
            ('app_session', 'Application session')
        ]

        # map field names to "human readable" column names
        COLUMN_DESCRIPTIONS = {
            'applicationconfig__label': 'Application config.',
            'applicationconfig__applicationsession__code': 'App. session code',
            'applicationconfig__applicationsession__auth_mode': 'Auth. mode',
            'n_users': 'Total num. users',
            'n_nonanon_users': 'Registered users',
            'n_nonanon_logins': 'Logins of registered users',
            'n_trackingsess': 'Num. tracking sessions',
            'most_recent_trackingsess': 'Most recently started tracking session',
            'avg_trackingsess_duration': 'Avg. tracking session duration',
            'n_events': 'Num. recorded tracking events',
            'most_recent_event': 'Most recently recorded event'
        }

        # map field names to custom formating functions
        COLUMN_FORMATING = {
            'avg_trackingsess_duration': format_timedelta,
            'most_recent_trackingsess': format_datetime,
            'most_recent_event': format_datetime,
            'applicationconfig__label': format_app_config,
            'applicationconfig__applicationsession__code': format_app_session
        }

        # form to select the aggregation level
        class ConfigForm(forms.Form):
            groupby = forms.ChoiceField(label='Group by', choices=CONFIGFORM_GROUPBY_CHOICES, required=False)

        # handle request
        if request.method == 'POST':   # form was submitted: populate with submitted data
            configform = ConfigForm(request.POST)

            if configform.is_valid():  # store submitted data in session
                request.session['dataview_configform'] = configform.cleaned_data
        else:   # form was not submitted: populate with default values stored in sesssion
            configform = ConfigForm(request.session.get('dataview_configform', {}))

        # get aggregation level "groupby" stored in session
        viewconfig = request.session.get('dataview_configform', {})
        groupby = viewconfig.get('groupby', CONFIGFORM_GROUPBY_CHOICES[0][0])

        # table expressions
        usersess_expr = 'applicationconfig__applicationsession__userapplicationsession'
        trackingsess_expr = usersess_expr + '__trackingsession'
        trackingevent_expr = trackingsess_expr + '__trackingevent'

        # fields that are always fetched
        toplevel_fields = ['name', 'url']
        stats_fields = ['n_users', 'n_nonanon_users', 'n_nonanon_logins', 'n_trackingsess',
                        'most_recent_trackingsess', 'avg_trackingsess_duration', 'n_events', 'most_recent_event']

        # fields specific for aggregation level
        if groupby == 'app':
            group_fields = []
        elif groupby == 'app_config':
            group_fields = ['applicationconfig__label', 'applicationconfig']
        elif groupby == 'app_session':
            group_fields = ['applicationconfig__label', 'applicationconfig',
                            'applicationconfig__applicationsession__code',
                            'applicationconfig__applicationsession__auth_mode',
                            'applicationconfig__applicationsession']
        else:
            raise ValueError(f'invalid value for "groupby": {groupby}')

        # all fields that are fetched
        data_fields = toplevel_fields + group_fields + stats_fields
        # fields used for sorting
        order_fields = toplevel_fields + group_fields
        # fields that are fetched but that should not be displayed
        hidden_fields = set(toplevel_fields) | {'applicationconfig', 'applicationconfig__applicationsession'}

        # fetch the data from the DB
        data_rows = Application.objects\
            .annotate(n_users=Count(usersess_expr, distinct=True),
                      n_nonanon_users=Count(usersess_expr + '__user', distinct=True),
                      n_nonanon_logins=Count(usersess_expr + '__user', distinct=False),
                      n_trackingsess=Count(trackingsess_expr, distinct=True),
                      most_recent_trackingsess=Max(trackingsess_expr + '__start_time'),
                      avg_trackingsess_duration=Avg(F(trackingsess_expr + '__end_time')
                                                    - F(trackingsess_expr + '__start_time')),
                      n_events=Count(trackingevent_expr, distinct=True),
                      most_recent_event=Max(trackingevent_expr + '__time'))\
            .order_by(*order_fields).values(*data_fields)

        # format the data for display
        # `table_data` is a dict for grouped table data per app; maps tuple (app name, app url) to formatted data rows
        table_data = defaultdict(list)
        for row in data_rows:
            formatted_row = [COLUMN_FORMATING.get(k, default_format)(row[k], row)
                             for k in data_fields if k not in hidden_fields]
            table_data[(row['name'], row['url'])].append(formatted_row)

        # set template variables
        context = {
            **self.each_context(request),
            "title": "Data manager",
            "subtitle": "Data view",
            "app_label": "datamanager",
            "configform": configform,
            "table_columns": [COLUMN_DESCRIPTIONS[k] for k in data_fields if k in COLUMN_DESCRIPTIONS],
            "table_data": table_data.items()
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
        finished = set(map(os.path.basename, glob(os.path.join(settings.DATA_EXPORT_DIR, '*.zip'))))

        # add the files that are currently being generated to form the set of all files
        all_files = sorted(finished | set(request.session['dataexport_awaiting_files']))

        # generate response data as list of tuples `(file_name, file_status)`
        response_data = [(f, f in finished) for f in all_files]

        # remove already finished files from the set of files are being generated
        request.session['dataexport_awaiting_files'] = \
            list(set(request.session['dataexport_awaiting_files']) - finished)

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
        request.session['dataexport_awaiting_files'] = [f for f in request.session['dataexport_awaiting_files']
                                                        if f != file]

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
            QUERIES_AND_FIELDS = {
                "app_sessions": (
                    "SELECT {fields} FROM api_application a "
                        "LEFT JOIN api_applicationconfig ac on a.id = ac.application_id "
                        "LEFT JOIN api_applicationsession asess on ac.id = asess.config_id",
                    "WHERE asess.code = %s",
                    (
                        ("a.id", "app_id"),
                        ("a.name", "app_name"),
                        ("a.url", "app_url"),
                        ("ac.id", "app_config_id"),
                        ("ac.label", "app_config_label"),
                        ("asess.code", "app_sess_code"),
                        ("asess.auth_mode", "app_sess_auth_mode"),
                    )
                ),
                "tracking_sessions": (
                    "SELECT {fields} FROM api_userapplicationsession ua "
                        "LEFT JOIN api_trackingsession t on ua.id = t.user_app_session_id",
                    "WHERE ua.application_session_id = %s",
                    (
                        ("ua.application_session_id", "app_sess_code"),
                        ("ua.code", "user_app_sess_code"),
                        ("ua.user_id", "user_app_sess_user_id"),
                        ("t.id", "track_sess_id"),
                        ("t.start_time", "track_sess_start"),
                        ("t.end_time", "track_sess_end"),
                        ("t.device_info", "track_sess_device_info"),
                    )
                ),
                "tracking_events": (
                    "SELECT {fields} FROM api_trackingevent e "
                        "LEFT JOIN api_trackingsession t on e.tracking_session_id = t.id "
                        "LEFT JOIN api_userapplicationsession ua on t.user_app_session_id = ua.id",
                    "WHERE ua.application_session_id = %s",
                    (
                        ("t.id", "track_sess_id"),
                        ("e.time", "event_time"),
                        ("e.type", "event_type"),
                        ("e.value", "event_value"),
                    )
                )
            }

            def format_if_datetime(x):
                if isinstance(x, datetime):
                    return utc_to_default_tz(x).strftime('%Y-%m-%dT%H:%M:%S.%f')
                else:
                    return x

            # write CSVs for the data from the queries defined in `QUERIES_AND_FIELDS`
            stored_csvs = []
            for csvname, (query_template, query_filter, query_fields) in QUERIES_AND_FIELDS.items():
                query_select = ','.join(f'{sqlfield} AS {csvfield}' for sqlfield, csvfield in query_fields)
                query = query_template.format(fields=query_select)
                if app_sess:
                    query += " " + query_filter

                # write the output
                tmpfpath = os.path.join(tmpdir, csvname + ".csv")
                stored_csvs.append(tmpfpath)
                with open(tmpfpath, 'w', newline='') as f:
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
            'config__application__name', 'config__application__url', 'config__label', 'code', 'auth_mode'
        ).order_by('config__application__name', 'config__label', 'auth_mode')

        # generate options to select an application session in the form
        app_sess_opts = [('', '– all –')] + \
                        [(sess['code'], f'{sess["config__application__name"]} '
                                        f'/ {sess["config__label"]} /  {sess["code"]} (auth. mode {sess["auth_mode"]})')
                         for sess in app_sess_objs]

        # export config. form
        class ConfigForm(forms.Form):
            app_sess_select = forms.ChoiceField(label='Application session', choices=app_sess_opts, required=False)

        # prepare session data
        if 'dataexport_awaiting_files' not in request.session:
            request.session['dataexport_awaiting_files'] = []

        if request.method == 'POST':   # handle form submit; pass submitted data to form and generate data export
            configform = ConfigForm(request.POST)

            if configform.is_valid():
                request.session['dataexport_configform'] = configform.cleaned_data

                # get selected app session
                app_sess_select = request.session['dataexport_configform']['app_sess_select']

                # generate a file name
                fname = f'{datetime.now(ZoneInfo(settings.TIME_ZONE)).strftime("%Y-%m-%d_%H%M%S")}_' \
                        f'{app_sess_select or "all"}.zip'

                # add the file name to the list of files that are being generated
                request.session['dataexport_awaiting_files'].append(fname)

                # start a background thread that will generate the data export
                threading.Thread(target=create_export, args=[fname, app_sess_select], daemon=True)\
                    .start()
        else:   # no form submit; only display exported data
            configform = ConfigForm(request.session.get('dataexport_configform', {}))

        # set template variables
        context = {
            **self.each_context(request),
            "title": "Data manager",
            "subtitle": "Data export",
            "app_label": "datamanager",
            "configform": configform
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
        allowed_iframe_origin = urlunsplit(sess_url_parts[:2] + [''] * 3)

        # set template variables
        context = {
            **self.each_context(request),
            "title": "Data manager",
            "subtitle": f"Tracking session replay for tracking session #{tracking_sess_id}",
            "app_label": "datamanager",
            "tracking_sess": tracking_sess,
            "app_config": tracking_sess.user_app_session.application_session.config.config,
            "allowed_iframe_origin": allowed_iframe_origin
        }

        request.current_app = self.name

        return TemplateResponse(request, "admin/trackingsessions_replay.html", context)

    def trackingsession_replay_datachunk(self, request, tracking_sess_id, i):
        """
        Tracking session replay view for fetching the `i`th replay data chunk of the tracking session identified
        by `tracking_sess_id`. Returns a JSON response.
        """

        if i == 0:   # for the first chunk, also add the information about how many chunks there are
            n_chunks = len(TrackingEvent.objects.filter(tracking_session=tracking_sess_id, type="mouse"))
        else:
            n_chunks = None

        try:
            event = TrackingEvent.objects.filter(tracking_session=tracking_sess_id, type="mouse").order_by("time")[i]
        except IndexError:
            return JsonResponse({})

        #filter_frametypes = {'m', 'c', 's', 'i', 'o'}
        # filter_frametypes = {'m'}
        replaydata = event.value
        # startframe = 0
        # for f_index, f in enumerate(replaydata['frames']):
        #     if f[0] in filter_frametypes:
        #         startframe = f_index
        #         break
        #
        # replaydata['frames'] = replaydata['frames'][startframe:]
        filter_frametypes = {'m', 'c', 's', 'i', 'o'}
        replaydata['frames'] = [f for f in replaydata['frames'] if f[0] in filter_frametypes]

        return JsonResponse({"i": i, "replaydata": replaydata, "n_chunks": n_chunks})


admin_site = MultiLAAdminSite(name='multila_admin')

# register model admins
admin_site.register(Application, ApplicationAdmin)
admin_site.register(ApplicationConfig, ApplicationConfigAdmin)
admin_site.register(ApplicationSession, ApplicationSessionAdmin)
admin_site.register(TrackingSession, TrackingSessionAdmin)
admin_site.register(TrackingEvent, TrackingEventAdmin)
admin_site.register(User)
admin_site.register(Group)
