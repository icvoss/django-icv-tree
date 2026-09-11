"""
Django settings for icv-tree tests.

Minimal configuration — MIGRATION_MODULES set to None so syncdb creates
tables directly without running migrations.
"""

import os

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
    },
    # Second alias for PathIndex router-guard regression tests
    # (icvoss/django-icv-tree#37). Not used by any other test.
    "other": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}

# When POSTGRES_HOST is set (the CI Postgres leg, icvoss/django-icv-tree#40),
# switch the default alias to PostgreSQL so _rebuild_cte's recursive-CTE
# fast path (tests/test_rebuild_cte.py) runs against a real backend. The
# "other" alias stays on sqlite, since it exists only for the PathIndex
# router-guard regression tests above and does not exercise the CTE path.
if os.environ.get("POSTGRES_HOST"):
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ["POSTGRES_HOST"],
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "NAME": os.environ.get("POSTGRES_DB", "tree_test"),
        "USER": os.environ.get("POSTGRES_USER", "icv_test"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "icv_test_password"),
        # tests/test_operations_pathindex.py runs databases=["other"] tests
        # alongside a "default" alias pointed at Postgres; without an
        # explicit empty TEST["DEPENDENCIES"] on both aliases, Django's test
        # runner raises ImproperlyConfigured: Circular dependency in
        # TEST[DEPENDENCIES] when serialising which alias depends on which.
        "TEST": {"DEPENDENCIES": []},
    }
    DATABASES["other"]["TEST"] = {"DEPENDENCIES": []}

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
# The Postgres CI leg (icvoss/django-icv-tree#40) sets ICV_TREE_ENABLE_CTE=1
# so tests/test_rebuild_cte.py exercises the real _rebuild_cte() path rather
# than the default BFS rebuild.
ICV_TREE_ENABLE_CTE = os.environ.get("ICV_TREE_ENABLE_CTE") == "1"
ICV_TREE_REBUILD_BATCH_SIZE = 1000
ICV_TREE_CHECK_ON_SAVE = False
