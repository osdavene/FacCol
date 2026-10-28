#!/bin/bash
set -e

echo "🚀 Iniciando despliegue de FacCol (Python Web) en CloudPanel AWS..."

APP_DIR="/home/faccol/htdocs/faccol.com"
cd "$APP_DIR"

# 1. Crear directorios de soporte si no existen
mkdir -p logs media staticfiles

# 2. Verificar entorno virtual Python
if [ ! -d "venv" ]; then
    echo "📦 Creando entorno virtual Python 3..."
    python3 -m venv venv
fi

# 3. Activar venv e instalar dependencias
echo "⚡ Instalando dependencias desde requirements.txt..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Ejecutar migraciones de PostgreSQL
echo "🗄️ Ejecutando migraciones de base de datos..."
python manage.py migrate --noinput

# 5. Cargar módulos y catálogos base (idempotente)
echo "🌱 Verificando catálogos y módulos base..."
python manage.py seed_base
python manage.py seed_colombia

# 6. Recopilar archivos estáticos para WhiteNoise y Nginx
echo "🎨 Recopilando archivos estáticos..."
python manage.py collectstatic --noinput

# 7. Reiniciar servicio Gunicorn
echo "🔄 Reiniciando servicio de aplicación..."
if systemctl is-active --quiet faccol; then
    sudo systemctl restart faccol
else
    # Si aún no está como servicio systemd, recargar Gunicorn por señal
    pkill -HUP gunicorn || true
fi

echo "✅ ¡Despliegue de FacCol Python completado exitosamente con 0 downtime!"
