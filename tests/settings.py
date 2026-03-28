"""
Django settings for icv-tree tests.

Minimal configuration — MIGRATION_MODULES set to None so syncdb creates
tables directly without running migrations.
"""

SECRET_KEY = "icv-tree-test-secret-key"  # noqa: S105

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "icv_tree",
    "tree_testapp",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

MIGRATION_MODULES = {
    "icv_tree": None,
    "tree_testapp": None,
    "contenttypes": None,
    "auth": None,
    "admin": None,
    "sessions": None,
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

USE_TZ = True
TIME_ZONE = "UTC"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

ROOT_URLCONF = "django.urls"

# icv-tree settings
ICV_TREE_PATH_SEPARATOR = "/"
ICV_TREE_STEP_LENGTH = 4
ICV_TREE_MAX_PATH_LENGTH = 255
ICV_TREE_ENABLE_CTE = False
ICV_TREE_REBUILD_BATCH_SIZE = 1000
ICV_TREE_CHECK_ON_SAVE = False
