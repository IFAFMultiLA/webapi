from django.contrib import admin
from django.utils.safestring import mark_safe

from .models import Application, ApplicationConfig, ApplicationSession


class ApplicationAdmin(admin.ModelAdmin):
    fields = ['name', 'url', 'updated', 'updated_by']
    readonly_fields = ['updated', 'updated_by']
    list_display = ['name', 'url', 'updated', 'updated_by']

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        return super().save_model(request, obj, form, change)


class ApplicationConfigAdmin(admin.ModelAdmin):
    fields = ['application', 'label', 'config', 'updated', 'updated_by']
    readonly_fields = ['updated', 'updated_by']
    list_display = ['label', 'application_name', 'updated', 'updated_by']
    list_display_links = ['label', 'application_name']
    list_select_related = True

    @admin.display(ordering='application__name', description='Application')
    def application_name(self, obj):
        return obj.application.name

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        return super().save_model(request, obj, form, change)


class ApplicationSessionAdmin(admin.ModelAdmin):
    fields = ['code', 'session_url', 'config', 'auth_mode', 'updated', 'updated_by']
    readonly_fields = ['code', 'session_url', 'updated', 'updated_by']
    list_display = ['code', 'config_label', 'session_url', 'auth_mode', 'updated', 'updated_by']
    list_select_related = True

    @admin.display(ordering='config__application__name', description='Application configuration')
    def config_label(self, obj):
        return f'{obj.config.application.name} / {obj.config.label}'

    @admin.display(ordering=None, description='URL')
    def session_url(self, obj):
        baseurl = obj.config.application.url
        if not baseurl.endswith('/'):
            baseurl += '/'
        sess_url = f'{baseurl}?sess={obj.code}'
        return mark_safe(f'<a href="{sess_url}" target="_blank">{sess_url}</a>')

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user

        if not change:
            obj.generate_code()
        return super().save_model(request, obj, form, change)


admin.site.register(Application, ApplicationAdmin)
admin.site.register(ApplicationConfig, ApplicationConfigAdmin)
admin.site.register(ApplicationSession, ApplicationSessionAdmin)
