from django.core.management.base import BaseCommand
from apps.empresas.models import Modulo, Plan


class Command(BaseCommand):
    help = "Carga modulos y planes base del sistema FacCol"

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("Cargando modulos del sistema..."))

        modulos_data = [
            {
                "codigo": "facturacion",
                "nombre": "Facturación Electrónica",
                "descripcion": "Emisión de facturas con QR y CUFE, cotizaciones, remisiones y notas crédito",
                "icono": "fa-file-invoice-dollar",
                "ruta": "facturacion:facturas_lista",
                "orden": 1,
            },
            {
                "codigo": "pos",
                "nombre": "Punto de Venta (POS)",
                "descripcion": "Terminal de caja rápida para mostrador y arqueos de turno",
                "icono": "fa-cash-register",
                "ruta": "pos:pos_terminal",
                "orden": 2,
            },
            {
                "codigo": "terceros",
                "nombre": "Clientes & Proveedores",
                "descripcion": "Gestión de terceros, regímenes tributarios y retenciones DIAN",
                "icono": "fa-users",
                "ruta": "terceros:lista",
                "orden": 3,
            },
            {
                "codigo": "inventario",
                "nombre": "Inventario & Productos",
                "descripcion": "Control de existencias, kardex de movimientos y categorías",
                "icono": "fa-boxes-stacked",
                "ruta": "inventario:productos_lista",
                "orden": 4,
            },
            {
                "codigo": "contabilidad",
                "nombre": "Contabilidad & PUC",
                "descripcion": "Plan único de cuentas de Colombia y asientos contables automáticos",
                "icono": "fa-book-bookmark",
                "ruta": "contabilidad:asientos_lista",
                "orden": 5,
            },
            {
                "codigo": "nomina",
                "nombre": "Nómina Electrónica",
                "descripcion": "Gestión de empleados, seguridad social y liquidaciones mensuales",
                "icono": "fa-id-card-clip",
                "ruta": "nomina:nominas_lista",
                "orden": 6,
            },
            {
                "codigo": "servicios",
                "nombre": "Servicios Técnicos",
                "descripcion": "Control de reparaciones, tablero Kanban y evidencias de servicio",
                "icono": "fa-screwdriver-wrench",
                "ruta": "servicios:kanban",
                "orden": 7,
            },
            {
                "codigo": "ia",
                "nombre": "FacCol AI Copilot",
                "descripcion": "Asistente inteligente para análisis de ventas, consultas y lectura de documentos",
                "icono": "fa-wand-magic-sparkles",
                "ruta": "ia:asistente",
                "orden": 8,
            },
        ]

        for m in modulos_data:
            Modulo.objects.update_or_create(
                codigo=m["codigo"],
                defaults=m
            )

        self.stdout.write(self.style.WARNING("Cargando planes comerciales..."))

        planes_data = [
            {
                "nombre": "Plan Básico / Emprendedor",
                "descripcion": "Ideal para personas naturales y pequeños negocios.",
                "precio_mensual": 49000,
                "limite_facturas_mes": 100,
                "limite_usuarios": 2,
                "duracion_meses": 1,
            },
            {
                "nombre": "Plan Profesional",
                "descripcion": "Facturación completa, POS, inventario y contabilidad básica.",
                "precio_mensual": 99000,
                "limite_facturas_mes": 1000,
                "limite_usuarios": 6,
                "duracion_meses": 1,
            },
            {
                "nombre": "Plan Empresarial Ilimitado",
                "descripcion": "Todos los módulos incluidos, nómina ilimitada y soporte prioritario.",
                "precio_mensual": 189000,
                "limite_facturas_mes": 100000,
                "limite_usuarios": 30,
                "duracion_meses": 1,
            },
        ]

        for p in planes_data:
            Plan.objects.update_or_create(
                nombre=p["nombre"],
                defaults=p
            )

        self.stdout.write(self.style.SUCCESS(
            f"Exitoso: {Modulo.objects.count()} modulos y {Plan.objects.count()} planes configurados."
        ))
