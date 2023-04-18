"""
Admin backend.

ModelAdmin classes to generate the django administration backend.
"""


from django.contrib import admin
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
        context = {
            **self.each_context(request),
            "title": self.index_title,
            "subtitle": "Data view",
            "app_label": 'dataview',
        }

        request.current_app = self.name

        return TemplateResponse(request, "admin/dataview.html", context)


admin_site = MultiLAAdminSite(name='multila_admin')

# register model admins
admin_site.register(Application, ApplicationAdmin)
admin_site.register(ApplicationConfig, ApplicationConfigAdmin)
admin_site.register(ApplicationSession, ApplicationSessionAdmin)
