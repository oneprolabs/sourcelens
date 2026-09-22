"""Minimal Django settings for core integration tests."""

SECRET_KEY = "test-secret"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "agentcore_metering.adapters.django",
    "agentcore_notifier.adapters.django",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

ROOT_URLCONF = "tests.admin_bulk_urls"
USE_TZ = True

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": (
        "core.settings.renders.CustomJSONRenderer",
    ),
}
