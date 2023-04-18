"""multila URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path, include

from api.admin import admin_site

urlpatterns = [
    path('admin/', admin_site.urls),
    path('', include('api.urls'))
]

# custom error views (to return JSON instead of HTML)
handler400 = 'api.views.bad_request_failure'
handler403 = 'api.views.permission_denied_failure'
handler404 = 'api.views.not_found_failure'
handler500 = 'api.views.server_error_failure'


# if installed, include debug toolbar
try:
    import debug_toolbar
    urlpatterns.append(path('__debug__/', include(debug_toolbar.urls)))
except ModuleNotFoundError: pass
