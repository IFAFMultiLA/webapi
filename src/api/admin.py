"""
Admin backend.

ModelAdmin classes to generate the django administration backend.
"""
import csv
import os.path
import shutil
import threading
import tempfile
from collections import defaultdict
from functools import partial
from glob import glob
from time import sleep
from datetime import datetime
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import admin
from django.contrib.auth.models import User, Group
from django.db.models import Count, Max, Avg, F
from django.db import connection as db_conn
from django.http import JsonResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.safestring import mark_safe
from django.conf import settings

from .models import Application, ApplicationConfig, ApplicationSession


# --- model admins ---


class ApplicationAdmin(admin.ModelAdmin):
    """
    Admin for Application model.
    """
    fields = ['name', 'url', 'updated', 'updated_by']
    readonly_fields = ['updated', 'updated_by']
    list_display = ['name', 'url', 'updated', 'updated_by']

    def save_model(self, request, obj, form, change):
        """
        Custom model save method to add current user to the `updated_by` field.
        """
        obj.updated_by = request.user
        return super().save_model(request, obj, form, change)


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


# --- custom admin site ---


class MultiLAAdminSite(admin.AdminSite):
    site_header = 'MultiLA Administration Interface'
    site_url = None

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('data/view/', self.admin_view(self.dataview), name='dataview'),
            path('data/export/', self.admin_view(self.dataexport), name='dataexport'),
            path('data/export_filelist/', self.admin_view(self.dataexport_filelist), name='dataexport_filelist'),
        ]
        return custom_urls + urls

    def get_app_list(self, request, app_label=None):
        app_list = super().get_app_list(request)

        datamanager_app = {
            'name': 'Data manager',
            'app_label': 'datamanager',
            'app_url': reverse('multila_admin:dataview'),
            'has_module_perms': request.user.is_superuser,
            'models': [
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
                }
            ]
        }

        app_list.append(datamanager_app)

        return app_list

    def dataview(self, request):
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
            return t.strftime('%Y-%m-%d %H:%M:%S')

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

        CONFIGFORM_GROUPBY_CHOICES = [
            ('app', 'Application'),
            ('app_config', 'Application config.'),
            ('app_session', 'Application session')
        ]

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

        COLUMN_FORMATING = {
            'avg_trackingsess_duration': format_timedelta,
            'most_recent_trackingsess': format_datetime,
            'most_recent_event': format_datetime,
            'applicationconfig__label': format_app_config,
            'applicationconfig__applicationsession__code': format_app_session
        }

        class ConfigForm(forms.Form):
            groupby = forms.ChoiceField(label='Group by', choices=CONFIGFORM_GROUPBY_CHOICES, required=False)

        if request.method == 'POST':
            configform = ConfigForm(request.POST)

            if configform.is_valid():
                request.session['dataview_configform'] = configform.cleaned_data
        else:
            configform = ConfigForm(request.session.get('dataview_configform', {}))

        viewconfig = request.session.get('dataview_configform', {})
        groupby = viewconfig.get('groupby', CONFIGFORM_GROUPBY_CHOICES[0][0])

        usersess_expr = 'applicationconfig__applicationsession__userapplicationsession'
        trackingsess_expr = usersess_expr + '__trackingsession'
        trackingevent_expr = trackingsess_expr + '__trackingevent'
        toplevel_fields = ['name', 'url']
        stats_fields = ['n_users', 'n_nonanon_users', 'n_nonanon_logins', 'n_trackingsess',
                        'most_recent_trackingsess', 'avg_trackingsess_duration', 'n_events', 'most_recent_event']

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

        data_fields = toplevel_fields + group_fields + stats_fields
        order_fields = toplevel_fields + group_fields
        hidden_fields = set(toplevel_fields) | {'applicationconfig', 'applicationconfig__applicationsession'}

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

        table_data = defaultdict(list)
        for row in data_rows:
            formatted_row = [COLUMN_FORMATING.get(k, default_format)(row[k], row)
                             for k in data_fields if k not in hidden_fields]
            table_data[(row['name'], row['url'])].append(formatted_row)

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
        finished = set(map(os.path.basename, glob(os.path.join(settings.DATA_EXPORT_DIR, '*.csv'))))

        all_files = sorted(finished | set(request.session['dataexport_awaiting_files']))
        response_data = [(f, f in finished) for f in all_files]

        request.session['dataexport_awaiting_files'] = \
            list(set(request.session['dataexport_awaiting_files']) - finished)

        return JsonResponse(response_data, safe=False)

    def dataexport(self, request):
        def create_export(dir, fname, app_sess):
            fpath = os.path.join(dir, fname)

            FIELDS = (
                ("a.id", "app_id"),
                ("a.name", "app_name"),
                ("a.url", "app_url"),
                ("ac.id", "app_config_id"),
                ("ac.label", "app_config_label"),
                ("asess.code", "app_sess_code"),
                ("asess.auth_mode", "app_sess_auth_mode"),
                ("au.code", "user_app_sess_code"),
                ("au.user_id", "user_app_sess_user_id"),
                ("t.id", "track_sess_id"),
                ("t.start_time", "track_sess_start"),
                ("t.end_time", "track_sess_end"),
                ("t.device_info", "track_sess_device_info"),
                ("e.time", "event_time"),
                ("e.type", "event_type"),
                ("e.value", "event_value"),
            )

            query_select = ','.join(f'{sqlfield} AS {csvfield}' for sqlfield, csvfield in FIELDS)

            query = f"""SELECT {query_select} FROM api_application a
                        LEFT JOIN api_applicationconfig ac on a.id = ac.application_id
                        LEFT JOIN api_applicationsession asess on ac.id = asess.config_id
                        LEFT JOIN api_userapplicationsession au on asess.code = au.application_session_id
                        LEFT JOIN api_trackingsession t on au.id = t.user_app_session_id
                        LEFT JOIN api_trackingevent e on t.id = e.tracking_session_id"""

            if app_sess:
                query += " WHERE asess.code = %s"

            with open(fpath, 'w', newline='') as f:
                csvwriter = csv.writer(f)
                csvwriter.writerow(list(zip(*FIELDS))[1])

                with db_conn.cursor() as cur:
                    cur.execute(query, [app_sess] if app_sess else [])

                    for dbrow in cur.fetchall():
                        csvwriter.writerow(dbrow)

            sleep(3)

            shutil.move(fpath, os.path.join(settings.DATA_EXPORT_DIR, fname))

        app_sess_objs = ApplicationSession.objects.values(
            'config__application__name', 'config__application__url', 'config__label', 'code', 'auth_mode'
        ).order_by('config__application__name', 'config__label', 'auth_mode')
        app_sess_opts = [('', '– all –')] + \
                        [(sess['code'], f'{sess["config__application__name"]} '
                                        f'/ {sess["config__label"]} /  {sess["code"]} (auth. mode {sess["auth_mode"]})')
                         for sess in app_sess_objs]

        class ConfigForm(forms.Form):
            app_sess_select = forms.ChoiceField(label='Application session', choices=app_sess_opts, required=False)

        if 'dataexport_awaiting_files' not in request.session:
            request.session['dataexport_awaiting_files'] = []

        if request.method == 'POST':
            configform = ConfigForm(request.POST)

            if configform.is_valid():
                request.session['dataexport_configform'] = configform.cleaned_data
                app_sess_select = request.session['dataexport_configform']['app_sess_select']
                fname = f'{datetime.now(ZoneInfo(settings.TIME_ZONE)).strftime("%Y-%m-%d_%H%M%S")}_' \
                        f'{app_sess_select or "all"}.csv'
                request.session['dataexport_awaiting_files'].append(fname)
                threading.Thread(target=create_export, args=[tempfile.mkdtemp(), fname, app_sess_select], daemon=True)\
                    .start()
        else:
            configform = ConfigForm(request.session.get('dataexport_configform', {}))

        request.current_app = self.name

        context = {
            **self.each_context(request),
            "title": "Data manager",
            "subtitle": "Data export",
            "app_label": "datamanager",
            "configform": configform
        }

        return TemplateResponse(request, "admin/dataexport.html", context)


admin_site = MultiLAAdminSite(name='multila_admin')

# register model admins
admin_site.register(Application, ApplicationAdmin)
admin_site.register(ApplicationConfig, ApplicationConfigAdmin)
admin_site.register(ApplicationSession, ApplicationSessionAdmin)
admin_site.register(User)
admin_site.register(Group)
