"""
Configuración de Gunicorn para producción en AWS / CloudPanel (faccol.com)
Optimizada dinámicamente según RAM disponible y lista para escalar a >= 2 GB RAM.
"""
import multiprocessing
import os


def _mem_total_mb() -> int:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 2048


# Dirección de escucha (puerto 8002 para FacCol)
bind = "127.0.0.1:8002"

_env_workers = os.getenv("GUNICORN_WORKERS", "").strip()
if _env_workers.isdigit() and int(_env_workers) > 0:
    workers = int(_env_workers)
elif _mem_total_mb() < 1536:
    # Instancias de ~1 GB RAM: menos workers + threads evita swapping y OOM
    workers = 2
else:
    workers = multiprocessing.cpu_count() * 2 + 1

worker_class = "gthread"
threads = 4
timeout = 120
keepalive = 5

# Logs
accesslog = "/home/faccol/logs/gunicorn_access.log"
errorlog = "/home/faccol/logs/gunicorn_error.log"
loglevel = "info"

# Proceso
daemon = False
max_requests = 800
max_requests_jitter = 80
reload = False
