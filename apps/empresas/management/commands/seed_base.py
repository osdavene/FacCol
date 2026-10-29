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
                "nombre": "FacCol IA",
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
                "descripcion": "Ideal para personas naturales y pequeños negocios con facturación electrónica DIAN.",
                "precio_mensual": 49000,
                "limite_facturas_mes": 100,
                "limite_usuarios": 2,
                "duracion_meses": 1,
                "destacado": False,
                "color_tema": "blue",
                "caracteristicas": "Hasta 100 facturas electrónicas al mes\n1 usuario administrador y 1 vendedor\nFacturación electrónica DIAN con QR y CUFE\nGestión de clientes y catálogo de productos\nEnvío automático de PDF por correo electrónico",
                "modulos_codigos": ["facturacion", "terceros", "inventario"],
            },
            {
                "nombre": "Plan Profesional",
                "descripcion": "Facturación completa, POS, inventario, órdenes de servicio y FacCol IA integrada.",
                "precio_mensual": 99000,
                "limite_facturas_mes": 1000,
                "limite_usuarios": 6,
                "duracion_meses": 1,
                "destacado": True,
                "color_tema": "amber",
                "caracteristicas": "Hasta 1.000 facturas y ventas POS al mes\nHasta 5 usuarios con control de roles\nPunto de Venta (POS) y arqueo de turnos de caja\nKardex de movimientos y control de inventario\nÓrdenes de servicio y panel de taller técnico\nFacCol IA incluida",
                "modulos_codigos": ["facturacion", "pos", "terceros", "inventario", "servicios", "ia"],
            },
            {
                "nombre": "Plan Empresarial Ilimitado",
                "descripcion": "Todos los módulos incluidos, contabilidad PUC, nómina electrónica DIAN y soporte 24/7.",
                "precio_mensual": 189000,
                "limite_facturas_mes": 100000,
                "limite_usuarios": 30,
                "duracion_meses": 1,
                "destacado": False,
                "color_tema": "purple",
                "caracteristicas": "Facturación electrónica y tickets POS ILIMITADOS\nUsuarios y terminales ilimitadas\nNómina Electrónica DIAN para empleados\nContabilidad completa con PUC y Libro Diario\nKardex multi-bodega y trazabilidad total\nSoporte técnico prioritario 24/7 y respaldos continuos",
                "modulos_codigos": ["facturacion", "pos", "terceros", "inventario", "contabilidad", "nomina", "servicios", "ia"],
            },
        ]

        for p in planes_data:
            mod_codigos = p.pop("modulos_codigos", [])
            plan_obj, _ = Plan.objects.update_or_create(
                nombre=p["nombre"],
                defaults=p
            )
            if mod_codigos:
                mods = Modulo.objects.filter(codigo__in=mod_codigos)
                plan_obj.modulos.set(mods)

        self.stdout.write(self.style.SUCCESS(
            f"Exitoso: {Modulo.objects.count()} modulos y {Plan.objects.count()} planes configurados."
        ))
