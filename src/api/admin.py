from django.contrib import admin

from .models import Application, ApplicationConfig


class ApplicationAdmin(admin.ModelAdmin):
    fields = ['name', 'url']
    readonly_fields = ['updated', 'updated_by']
    list_display = ['name', 'url', 'updated', 'updated_by']


class ApplicationConfigAdmin(admin.ModelAdmin):
    list_display = ['label', 'application_name']
    list_select_related = True

    @admin.display(ordering='application__name', description='Application')
    def application_name(self, obj):
        return obj.application.name
    application_name.allow_tags = True


admin.site.register(Application, ApplicationAdmin)
admin.site.register(ApplicationConfig, ApplicationConfigAdmin)
