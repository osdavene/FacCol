"""
Configuración de Gunicorn para producción en AWS / CloudPanel (faccol.com)
"""
import multiprocessing

# Dirección de escucha (puerto 8000 o socket unix según reverse proxy Nginx)
bind = "127.0.0.1:8000"

# Trabajadores basados en núcleos de CPU
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
threads = 2
timeout = 120
keepalive = 5

# Logs
accesslog = "/home/faccol/htdocs/faccol.com/logs/gunicorn_access.log"
errorlog = "/home/faccol/htdocs/faccol.com/logs/gunicorn_error.log"
loglevel = "info"

# Proceso
daemon = False
max_requests = 1000
max_requests_jitter = 50
reload = False
