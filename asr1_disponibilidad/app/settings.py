"""
Django settings mínimos para el experimento ASR-1.
En producción estos valores vienen de variables de entorno / AWS Secrets Manager.
"""
from decouple import config

SECRET_KEY = config("DJANGO_SECRET_KEY", default="dev-only-insecure-key-change-in-prod")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME", default="finops"),
        "USER": config("DB_USER", default="finops_user"),
        "PASSWORD": config("DB_PASSWORD", default="changeme"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
        # Opciones clave para Alta Disponibilidad en RDS Multi-AZ
        "OPTIONS": {
            "connect_timeout": 5,
            "sslmode": "require",        # TLS obligatorio hacia RDS
        },
        "CONN_MAX_AGE": 60,
    }
}

REDIS_URL = config("REDIS_URL", default="redis://localhost:6379/0")
FINOPS_INTERNAL_URL = config("FINOPS_INTERNAL_URL", default="http://localhost:8080/health/")

ROOT_URLCONF = "asr1_disponibilidad.app.urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
