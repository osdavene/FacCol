import os
from pathlib import Path
from dotenv import load_dotenv
from django.core.management.utils import get_random_secret_key

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Cargar variables de entorno desde .env si existe
load_dotenv(BASE_DIR / '.env')

DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 't')

SECRET_KEY = os.getenv('SECRET_KEY', '').strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = get_random_secret_key()
    else:
        raise RuntimeError("SECRET_KEY debe configurarse en .env cuando DEBUG=False.")

SETTINGS_ENCRYPTION_KEY = os.getenv('SETTINGS_ENCRYPTION_KEY', '').strip()

ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost,testserver,faccol.com,www.faccol.com').split(',') if h.strip()]
if DEBUG and 'testserver' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('testserver')

# CSRF Trusted Origins (necesario para producción y CloudPanel)
CSRF_TRUSTED_ORIGINS = [
    'https://faccol.com',
    'https://www.faccol.com',
    'https://faccol.duckdns.org',
    'https://faccol.servigest.online',
    'https://servigest.online',
    'http://127.0.0.1:8000',
    'http://127.0.0.1:8002',
    'http://localhost:8000',
]

for host in ALLOWED_HOSTS:
    if host not in ('127.0.0.1', 'localhost', 'testserver') and not host.startswith('.'):
        origin = f'https://{host}'
        if origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(origin)

# Application definition
INSTALLED_APPS = [
    'whitenoise.runserver_nostatic',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    # Módulos FacCol
    'apps.core',
    'apps.usuarios',
    'apps.empresas',
    'apps.terceros',
    'apps.inventario',
    'apps.facturacion',
    'apps.pos',
    'apps.contabilidad',
    'apps.nomina',
    'apps.backoffice',
    'apps.ia',
]

AUTH_USER_MODEL = 'usuarios.User'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.core.middleware.TenantMiddleware',
]

ROOT_URLCONF = 'faccol_core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.core.context_processors.global_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'faccol_core.wsgi.application'

# Database Configuration (PostgreSQL / MySQL)
DB_ENGINE = os.getenv('DB_ENGINE', 'django.db.backends.postgresql')
DB_NAME = os.getenv('DB_NAME', 'faccol')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_HOST = os.getenv('DB_HOST', '127.0.0.1')
DB_PORT = os.getenv('DB_PORT', '5432')

DATABASES = {
    'default': {
        'ENGINE': DB_ENGINE,
        'NAME': DB_NAME,
        'USER': DB_USER,
        'PASSWORD': DB_PASSWORD,
        'HOST': DB_HOST,
        'PORT': DB_PORT,
    }
}

if 'mysql' in DB_ENGINE:
    DATABASES['default']['OPTIONS'] = {
        'charset': 'utf8mb4',
        'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization (Colombia)
LANGUAGE_CODE = 'es-co'
TIME_ZONE = 'America/Bogota'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Media files (Logos, Documentos, Fotos)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Redirecciones de Autenticación
LOGIN_URL = 'usuarios:login'
LOGIN_REDIRECT_URL = 'core:dashboard'
LOGOUT_REDIRECT_URL = 'usuarios:login'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Email Settings (Resend API / Respaldo SMTP - prioridad .env, con respaldo Laravel/Django)
APP_URL = os.getenv('APP_URL', 'https://faccol.servigest.online').strip().rstrip('/')
MAIL_MAILER = (os.getenv('MAIL_MAILER') or 'smtp').strip().lower()
MAIL_ENCRYPTION = (
    os.getenv('MAIL_ENCRYPTION')
    or ('tls' if os.getenv('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 't') else '')
).strip().lower()
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = (os.getenv('MAIL_HOST') or os.getenv('EMAIL_HOST') or 'smtp.gmail.com').strip()
EMAIL_PORT = int(os.getenv('MAIL_PORT') or os.getenv('EMAIL_PORT') or 587)
EMAIL_USE_TLS = MAIL_ENCRYPTION in ('tls', 'true', '1')
EMAIL_USE_SSL = MAIL_ENCRYPTION == 'ssl'
EMAIL_HOST_USER = (os.getenv('MAIL_USERNAME') or os.getenv('EMAIL_HOST_USER') or '').strip()
EMAIL_HOST_PASSWORD = (os.getenv('MAIL_PASSWORD') or os.getenv('EMAIL_HOST_PASSWORD') or '').strip()
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '').strip()

MAIL_FROM_ADDRESS = (os.getenv('MAIL_FROM_ADDRESS') or os.getenv('DEFAULT_FROM_EMAIL') or 'notificaciones@servigest.online').strip()
MAIL_FROM_NAME = (os.getenv('MAIL_FROM_NAME') or 'FacCol — ServiGest').strip()
MAIL_ADMIN_RECIPIENT = (os.getenv('MAIL_ADMIN_RECIPIENT') or 'servigestonline@gmail.com').strip()
DEFAULT_FROM_EMAIL = MAIL_FROM_ADDRESS
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Seguridad adicional en producción
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
X_FRAME_OPTIONS = 'DENY'
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'False').lower() in ('true', '1', 't')

# API Keys & Pasarelas
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
WOMPI_PUBLIC_KEY = os.getenv('WOMPI_PUBLIC_KEY', '')
WOMPI_PRIVATE_KEY = os.getenv('WOMPI_PRIVATE_KEY', '')
WOMPI_EVENTS_SECRET = os.getenv('WOMPI_EVENTS_SECRET', '')
WOMPI_SANDBOX = os.getenv('WOMPI_SANDBOX', 'True').lower() in ('true', '1', 't')

# Observabilidad opcional (Sentry)
SENTRY_DSN = os.getenv('SENTRY_DSN', '').strip()
if SENTRY_DSN and not DEBUG:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration()],
            traces_sample_rate=0.1,
            send_default_pii=False,
        )
    except Exception:
        pass
