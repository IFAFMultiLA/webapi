"""
Django settings for multila project.

**DEVELOPMENT SETTINGS. DO NOT USE IN PRODUCTION!**

Production settings are in `settings_prod.py`.

Generated by 'django-admin startproject' using Django 4.1.7.

For more information on this file, see
https://docs.djangoproject.com/en/4.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/4.1/ref/settings/
"""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-76tpm66^c=!&)qg5^&%o!9&fo+4wj7ksopfz_@_qty@=1ex$kt"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []
CORS_ALLOWED_ORIGINS = [
    "http://localhost:8001",
    "http://localhost:8002",
    "http://localhost:8003",
    "http://localhost:8004",
    "http://localhost:8005",
]
CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:8001",
    "http://127.0.0.1:8002",
    "http://127.0.0.1:8003",
    "http://127.0.0.1:8004",
    "http://127.0.0.1:8005",
]
CORS_ALLOW_CREDENTIALS = True

# Application definition

INSTALLED_APPS = [
    "api.apps.ApiConfig",
    "django.contrib.admin.apps.SimpleAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "debug_toolbar",
    "corsheaders",
    "rest_framework",
]

MIDDLEWARE = [
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if DEBUG:
    MIDDLEWARE.append("api.utils.DisableCSRF")


ROOT_URLCONF = "multila.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "multila.wsgi.application"

CSRF_FAILURE_VIEW = "api.views.csrf_failure"

# Database
# https://docs.djangoproject.com/en/4.1/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB") or "multila",
        "USER": os.environ.get("POSTGRES_USER") or "admin",
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD") or "admin",
        "HOST": os.environ.get("POSTGRES_HOST") or "localhost",
        "PORT": int(os.environ.get("POSTGRES_PORT") or "5432"),
    }
}

# Password validation
# https://docs.djangoproject.com/en/4.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Europe/Berlin"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.1/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR.parent / "static_files"
DATA_EXPORT_DIR = BASE_DIR.parent / "data" / "export"
CODEBOOK_PATH = BASE_DIR.parent / "data" / "codebook.pdf"
APPS_DEPLOYMENT = {  # set to None to disable app upload feature
    "upload_path": BASE_DIR.parent / "apps_deployed",  # must be writable
    "log_path": BASE_DIR.parent / "apps_deployed" / "log",  # directory where Shiny writes logs; optional; can be None
    "base_url": "http://localhost:8001",  # base URL for all deployed apps inside `upload_path`
    # below: action to perform when removing a deployed app; if "delete", remove the whole app directory;
    # if "remove.txt" write a "remove.txt" file in the app directory; otherwise do nothing
    "remove_mode": "keep",
    # below: path to a "trigger file" that is updated (via "touch") whenever there was a change to any deployed
    # app in order to trigger dependency installation, app removal, etc. via an external program; optional
    "update_trigger_file": None,
}

CHATBOT_API = {  # set to None to disable chatbot API feature
    "key": os.environ.get("OPENAI_API_KEY"),
    "available_models": ["gpt-3.5-turbo", "gpt-4o-mini", "gpt-4o"],
    "content_section_identifier_pattern": r"mainContentElem-\d+$",
    "system_role_templates": {  #  per language
        "en": "You are a teacher in data science and statistics. Consider the following learning material enclosed "
        'by "---" marks. Before each content section in the document, there is a unique identifier for that '
        'section denoted as "mainContentElem-#". "#" is a placeholder for a number.'
        "\n\n---\n\n$doc_text\n\n---\n\nNow give a short answer to the following question and, if possible, refer to "
        "the learning material. If you are referring to the learning material, end your answer with a new paragraph "
        'containing only "mainContentElem-#" and replace "#" with the respective section number.',
        "de": "Du bist Lehrkraft im Bereich Data Science und Statistik. Berücksichtige das folgende "
        'Lehrmaterial, das durch "---"-Markierungen eingeschlossen ist. Vor jedem Inhaltsabschnitt im Dokument '
        'gibt es eine eindeutige Kennung für diesen Abschnitt, die mit "mainContentElem-#" angegeben ist. "#" '
        "ist ein Platzhalter für eine Zahl.\n\n---\n\n$doc_text\n\n---\n\nGib nun eine kurze Antwort auf "
        "die folgende Frage und beziehe dich, wenn möglich, auf das Lehrmaterial. Wenn du dich auf das "
        "Lehrmaterial beziehst, beende deine Antwort mit einem neuen Absatz, der ausschließlich den Text "
        '"mainContentElem-#" enthält und ersetze "#" durch die entsprechende Abschnittsnummer.',
    },
    "user_role_templates": {  #  per language
        "en": "$question",
        "de": "$question",
    },
}

# Default primary key field type
# https://docs.djangoproject.com/en/4.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if DEBUG:
    import socket  # only if you haven't already imported this

    hostname, _, ips = socket.gethostbyname_ex(socket.gethostname())
    INTERNAL_IPS = [ip[: ip.rfind(".")] + ".1" for ip in ips] + ["127.0.0.1", "10.0.2.2"]
