# FacCol — Sistema ERP & Facturación Electrónica Colombia 🇨🇴 (Python Web Edition)

Sistema Integral de Facturación Electrónica, Punto de Venta (POS), Inventario, Contabilidad (PUC), Nómina Electrónica y Copilot de Inteligencia Artificial para Colombia. Arquitectura SaaS multi-empresa con aislamiento por tenant y panel Backoffice.

**Stack Tecnológico:**
- **Backend:** Python 3.12 · Django 5.1 · Gunicorn · WhiteNoise
- **Base de Datos:** PostgreSQL
- **Frontend:** Tailwind CSS · Alpine.js · FontAwesome 6
- **Documentos & Fiscal:** ReportLab (PDF) · QRCode · Generación CUFE DIAN
- **Inteligencia Artificial:** Google Gemini AI SDK (`google-genai`) & Motor analítico nativo
- **Despliegue:** AWS EC2 · CloudPanel · Nginx · GitHub Actions CI/CD

---

## 🚀 Módulos del Sistema

| Módulo | Descripción |
|---|---|
| **Facturación Electrónica** | Facturas de venta con CUFE, código QR DIAN, cotizaciones, remisiones y notas de crédito |
| **Punto de Venta (POS)** | Terminal de mostrador táctil con soporte para lectores de código de barras, turnos y arqueos de caja |
| **Recibos de Caja** | Control de recaudos, cobros y abonos parciales aplicados a facturas |
| **Terceros (Clientes / Proveedores)** | Personas naturales y jurídicas con régimen tributario colombiano, responsabilidades fiscales y retenciones |
| **Inventario & Kardex** | Catálogo de productos, existencias en tiempo real, alertas de stock mínimo, categorías y unidades de medida |
| **Contabilidad & PUC** | Plan Único de Cuentas (PUC) de Colombia y libro diario con asientos contables automáticos |
| **Nómina Electrónica** | Empleados, liquidación mensual con SMMLV, auxilio de transporte, deducciones (salud, pensión) y aportes patronales |
| **Servicios Técnicos & Taller** | Órdenes de servicio técnico, recepción de equipos, evidencias fotográficas y tablero Kanban |
| **FacCol AI Copilot** | Asistente conversacional inteligente para análisis financiero, alertas de stock y consultas tributarias DIAN |
| **Backoffice Superadmin** | Panel de administración global de inquilinos (tenants), planes comerciales y monitoreo del sistema |

---

## 💻 Instalación y Configuración Local

### Requisitos
- Python 3.12+
- PostgreSQL 15+
- Git

### Pasos de Instalación

1. **Clonar el repositorio:**
   ```bash
   git clone https://github.com/osdavene/FacCol.git
   cd FacCol
   ```

2. **Crear y activar el entorno virtual:**
   ```bash
   python -m venv venv
   # En Windows:
   .\venv\Scripts\activate
   # En Linux / Mac:
   source venv/bin/activate
   ```

3. **Instalar dependencias:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configurar el archivo `.env`:**
   Copiar `.env.example` a `.env` y ajustar credenciales de PostgreSQL:
   ```env
   DB_ENGINE=django.db.backends.postgresql
   DB_NAME=faccol
   DB_USER=postgres
   DB_PASSWORD=tu_password
   DB_HOST=127.0.0.1
   DB_PORT=5432
   ```

5. **Ejecutar migraciones:**
   ```bash
   python manage.py migrate
   ```

6. **Cargar catálogos y datos iniciales (Seeders):**
   ```bash
   # 1. Catálogos base (módulos y planes)
   python manage.py seed_base

   # 2. División política de Colombia (34 departamentos y 1.082 municipios DANE)
   python manage.py seed_colombia

   # 3. Datos de prueba y superadmin inicial
   python manage.py seed_demo
   ```

7. **Recopilar archivos estáticos:**
   ```bash
   python manage.py collectstatic --noinput
   ```

8. **Iniciar el servidor de desarrollo:**
   ```bash
   python manage.py runserver
   ```
   Abrir en el navegador: [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## 🔑 Credenciales de Acceso Demo

- **URL de Acceso:** [http://127.0.0.1:8000/auth/login/](http://127.0.0.1:8000/auth/login/)
- **Usuario:** `admin@faccol.com`
- **Contraseña:** `admin123`
- **Empresa Demo Activa:** `FacCol Soluciones Empresariales S.A.S.` (NIT `901458789-1`)

---

## 🌐 Despliegue en AWS CloudPanel (`faccol.com`)

El proyecto incluye integración continua mediante GitHub Actions en `.github/workflows/deploy.yml`.

### Configuración en CloudPanel
1. En CloudPanel crear un sitio de tipo **Python Site** o **Reverse Proxy** apuntando a `http://127.0.0.1:8000`.
2. Habilitar el servicio systemd copiando `faccol.service` a `/etc/systemd/system/faccol.service`:
   ```bash
   sudo cp faccol.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable faccol
   sudo systemctl start faccol
   ```
3. Cada `git push origin main` desencadenará el despliegue automático con 0 tiempo de inactividad.
