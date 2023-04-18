"""
Admin backend.

ModelAdmin classes to generate the django administration backend.
"""
from collections import defaultdict

from django import forms
from django.contrib import admin
from django.db.models import Count, Max, Avg, F
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.safestring import mark_safe

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
            path('dataview/', self.admin_view(self.dataview), name='dataview'),
        ]
        return custom_urls + urls

    def get_app_list(self, request, app_label=None):
        app_list = super().get_app_list(request)

        dataview_app = {
            'name': 'Data view',
            'app_label': 'dataview',
            'app_url': reverse('multila_admin:dataview'),
            'has_module_perms': request.user.is_superuser,
            'models': []
        }

        app_list.append(dataview_app)

        return app_list

    def dataview(self, request):
        def default_format(v):
            return '-' if v is None else v

        def format_timedelta(t):
            if t is None:
                return default_format(t)
            return f'{int(t.total_seconds() // 60)}min {round((t.total_seconds() / 60 - t.total_seconds() // 60) * 60)}s'

        def format_datetime(t):
            if t is None:
                return default_format(t)
            return t.strftime('%Y-%m-%d %H:%M:%S')

        CONFIGFORM_GROUPBY_CHOICES = [
            ('app', 'Application'),
            ('app_config', 'Application config.'),
            ('app_session', 'Application session')
        ]

        COLUMN_DESCRIPTIONS = {
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
            'most_recent_event': format_datetime
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

        if groupby == 'app':
            usersess_expr = 'applicationconfig__applicationsession__userapplicationsession'
            trackingsess_expr = usersess_expr + '__trackingsession'
            trackingevent_expr = trackingsess_expr + '__trackingevent'

            data_fields = ['name', 'url', 'n_users', 'n_nonanon_users', 'n_nonanon_logins', 'n_trackingsess',
                           'most_recent_trackingsess', 'avg_trackingsess_duration', 'n_events', 'most_recent_event']
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
                .order_by('name').values(*data_fields)
        elif groupby == 'app_config':
            # TODO
            data_fields = []
            data_rows = []
        elif groupby == 'app_session':
            # TODO
            data_fields = []
            data_rows = []
        else:
            raise ValueError(f'invalid value for "groupby": {groupby}')

        table_data = defaultdict(list)
        for row in data_rows:
            formatted_row = [COLUMN_FORMATING.get(k, default_format)(row[k])
                             for k in data_fields if k not in {'name', 'url'}]
            table_data[(row['name'], row['url'])].append(formatted_row)

        context = {
            **self.each_context(request),
            "title": self.index_title,
            "subtitle": "Data view",
            "app_label": 'dataview',
            "configform": configform,
            "table_columns": [COLUMN_DESCRIPTIONS[k] for k in data_fields if k in COLUMN_DESCRIPTIONS],
            "table_data": table_data.items()
        }

        request.current_app = self.name

        return TemplateResponse(request, "admin/dataview.html", context)


admin_site = MultiLAAdminSite(name='multila_admin')

# register model admins
admin_site.register(Application, ApplicationAdmin)
admin_site.register(ApplicationConfig, ApplicationConfigAdmin)
admin_site.register(ApplicationSession, ApplicationSessionAdmin)
