from decimal import Decimal
from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.usuarios.models import User, EmpresaUsuario
from apps.empresas.models import Empresa, Modulo, Plan, Departamento, Municipio, EmpresaModulo
from apps.terceros.models import Tercero
from apps.inventario.models import Categoria, UnidadMedida, Producto, MovimientoInventario
from apps.facturacion.models import Factura, FacturaItem, ReciboCaja
from apps.contabilidad.models import PlanCuenta


class Command(BaseCommand):
    help = "Carga usuario superadmin, empresa demo y datos iniciales para pruebas"

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("Creando entorno demo inicial..."))

        with transaction.atomic():
            # 1. Superadmin
            admin_user, created_admin = User.objects.get_or_create(
                username="admin@faccol.com",
                defaults={
                    "email": "admin@faccol.com",
                    "first_name": "Administrador",
                    "last_name": "Plataforma",
                    "is_staff": True,
                    "is_superuser": True,
                    "is_superadmin": True,
                    "tema": "oscuro"
                }
            )
            if created_admin:
                admin_user.set_password("admin123")
                admin_user.save()

            # 2. Empresa Demo
            dep_bogota = Departamento.objects.filter(nombre__icontains="BOGOTA").first()
            mun_bogota = Municipio.objects.filter(nombre__icontains="BOGOTA").first()
            plan_pro = Plan.objects.filter(nombre__icontains="Profesional").first()

            empresa, created_emp = Empresa.objects.get_or_create(
                nit="901458789",
                defaults={
                    "razon_social": "FacCol Soluciones Empresariales S.A.S.",
                    "nombre_comercial": "FacCol Tech",
                    "dv": "1",
                    "tipo_persona": "JURIDICA",
                    "regimen_tributario": "RESPONSABLE_IVA",
                    "direccion": "Carrera 7 # 71 - 52 Torre B, Piso 12",
                    "departamento": dep_bogota,
                    "municipio": mun_bogota,
                    "telefono": "601 345 6789",
                    "email": "contacto@faccol.com",
                    "prefijo_factura": "FE",
                    "resolucion_dian": "18764000001234",
                    "fecha_resolucion": date.today() - timedelta(days=60),
                    "rango_desde": 1,
                    "rango_hasta": 10000,
                    "consecutivo_actual": 1001,
                    "plan": plan_pro,
                }
            )

            # 3. Vincular admin como propietario
            EmpresaUsuario.objects.get_or_create(
                user=admin_user,
                empresa=empresa,
                defaults={"rol": "propietario", "activo": True}
            )

            # 4. Habilitar todos los módulos
            for mod in Modulo.objects.all():
                EmpresaModulo.objects.get_or_create(
                    empresa=empresa,
                    modulo=mod,
                    defaults={"activo": True}
                )

            # 5. Cuentas Contables PUC base para la empresa
            cuentas_puc = [
                ("110505", "Caja General", 6, "DEBITO", True),
                ("111005", "Bancos Nacionales", 6, "DEBITO", True),
                ("130505", "Clientes Nacionales", 6, "DEBITO", True),
                ("143505", "Mercancías No Fabricadas por la Empresa", 6, "DEBITO", True),
                ("220505", "Proveedores Nacionales", 6, "CREDITO", True),
                ("233595", "Costos y Gastos por Pagar - Otros", 6, "CREDITO", True),
                ("236540", "Retención en la Fuente 2.5% (Compras)", 6, "CREDITO", True),
                ("240801", "Impuesto sobre las Ventas por Pagar (IVA Generado 19%)", 6, "CREDITO", True),
                ("240802", "Impuesto sobre las Ventas Descontable (IVA en Compras)", 6, "DEBITO", True),
                ("413501", "Comercio al por Mayor y Menor - Ventas", 6, "CREDITO", True),
                ("510506", "Sueldos y Salarios Personal Administrativo", 6, "DEBITO", True),
                ("613501", "Costo de Ventas - Mercancías", 6, "DEBITO", True),
            ]
            for cod, nom, niv, nat, mov in cuentas_puc:
                PlanCuenta.objects.get_or_create(
                    empresa=empresa,
                    codigo=cod,
                    defaults={
                        "nombre": nom,
                        "nivel": niv,
                        "naturaleza": nat,
                        "permite_movimiento": mov,
                    }
                )

            # 6. Unidades de Medida
            und_unidad, _ = UnidadMedida.objects.get_or_create(
                empresa=empresa, codigo="UND",
                defaults={"nombre": "Unidad", "activa": True}
            )
            und_srv, _ = UnidadMedida.objects.get_or_create(
                empresa=empresa, codigo="SRV",
                defaults={"nombre": "Servicio / Hora", "activa": True}
            )

            # 7. Categorías
            cat_equipos, _ = Categoria.objects.get_or_create(
                empresa=empresa, nombre="Equipos y Computadores",
                defaults={"descripcion": "Equipos portátiles, servidores y accesorios"}
            )
            cat_servicios, _ = Categoria.objects.get_or_create(
                empresa=empresa, nombre="Servicios Técnicos",
                defaults={"descripcion": "Consultoría y soporte informático"}
            )

            # 8. Productos
            prod1, _ = Producto.objects.get_or_create(
                empresa=empresa, codigo="PROD-001",
                defaults={
                    "nombre": "Laptop ThinkPad L14 Gen 4 Core i7 16GB",
                    "descripcion": "Portátil corporativo con pantalla Full HD y disco SSD 512GB",
                    "categoria": cat_equipos,
                    "unidad_medida": und_unidad,
                    "precio_venta": Decimal("3850000.00"),
                    "costo_unitario": Decimal("2950000.00"),
                    "tarifa_iva": Decimal("19.00"),
                    "stock_actual": Decimal("15.00"),
                    "stock_minimo": Decimal("3.00"),
                    "stock_maximo": Decimal("50.00"),
                    "maneja_inventario": True,
                }
            )
            prod2, _ = Producto.objects.get_or_create(
                empresa=empresa, codigo="SRV-001",
                defaults={
                    "nombre": "Mantenimiento Preventivo y Limpieza de Equipos",
                    "descripcion": "Servicio técnico especializado para hardware y software",
                    "categoria": cat_servicios,
                    "unidad_medida": und_srv,
                    "precio_venta": Decimal("150000.00"),
                    "costo_unitario": Decimal("40000.00"),
                    "tarifa_iva": Decimal("19.00"),
                    "stock_actual": Decimal("0.00"),
                    "maneja_inventario": False,
                }
            )

            # 9. Clientes / Terceros
            cliente1, _ = Tercero.objects.get_or_create(
                empresa=empresa,
                numero_documento="900876543",
                defaults={
                    "es_cliente": True,
                    "tipo_persona": "JURIDICA",
                    "tipo_documento": "NIT",
                    "dv": "4",
                    "razon_social": "Inversiones Andinas de Colombia S.A.S.",
                    "nombre_comercial": "Andinas Group",
                    "direccion": "Calle 100 # 19A - 45",
                    "departamento": dep_bogota,
                    "municipio": mun_bogota,
                    "telefono": "601 456 7890",
                    "email": "contabilidad@andinascolombia.com",
                    "regimen": "RESPONSABLE_IVA",
                }
            )

            cliente2, _ = Tercero.objects.get_or_create(
                empresa=empresa,
                numero_documento="1020304050",
                defaults={
                    "es_cliente": True,
                    "tipo_persona": "NATURAL",
                    "tipo_documento": "CC",
                    "razon_social": "Carlos Arturo Rodriguez Martinez",
                    "direccion": "Carrera 15 # 85 - 20 Apto 402",
                    "departamento": dep_bogota,
                    "municipio": mun_bogota,
                    "telefono": "310 987 6543",
                    "email": "carlos.rodriguez@gmail.com",
                    "regimen": "NO_RESPONSABLE_IVA",
                }
            )

            # 10. Factura de Prueba
            factura, created_fac = Factura.objects.get_or_create(
                empresa=empresa,
                tipo_documento="FACTURA",
                prefijo="FE",
                consecutivo=1001,
                defaults={
                    "tercero": cliente1,
                    "fecha_emision": date.today(),
                    "fecha_vencimiento": date.today() + timedelta(days=30),
                    "estado": "EMITIDA",
                    "metodo_pago": "CONTADO",
                    "observaciones": "Factura electrónica demostrativa generada por FacCol.",
                }
            )

            if created_fac:
                FacturaItem.objects.create(
                    factura=factura,
                    producto=prod1,
                    descripcion=prod1.nombre,
                    cantidad=Decimal("2.00"),
                    precio_unitario=prod1.precio_venta,
                    porcentaje_descuento=Decimal("5.00"),
                    porcentaje_iva=Decimal("19.00"),
                )
                FacturaItem.objects.create(
                    factura=factura,
                    producto=prod2,
                    descripcion=prod2.nombre,
                    cantidad=Decimal("1.00"),
                    precio_unitario=prod2.precio_venta,
                    porcentaje_descuento=Decimal("0.00"),
                    porcentaje_iva=Decimal("19.00"),
                )
                factura.recalcular_totales()
                factura.generar_cufe()

                # Recibo de caja parcial
                ReciboCaja.objects.create(
                    empresa=empresa,
                    consecutivo=1,
                    factura=factura,
                    tercero=cliente1,
                    fecha=date.today(),
                    valor=Decimal("2000000.00"),
                    metodo_pago="TRANSFERENCIA",
                    referencia_pago="BANC-9874521",
                    observaciones="Anticipo del 25% por transferencia Bancolombia",
                )
                factura.recalcular_totales()

        self.stdout.write(self.style.SUCCESS(
            "Entorno Demo cargado con éxito!\n"
            "Credenciales Superadmin:\n"
            "  Usuario: admin@faccol.com\n"
            "  Password: admin123\n"
            "Empresa: FacCol Soluciones Empresariales S.A.S. (NIT 901458789-1)"
        ))
