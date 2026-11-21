import calendar
import hashlib
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

from apps.contabilidad.models import AsientoContable, AsientoLinea, PlanCuenta
from apps.contabilidad.services import (
    registrar_asiento_compra,
    registrar_asiento_nomina,
    registrar_asiento_nota_credito,
    registrar_asiento_recibo,
    registrar_asiento_venta,
)
from apps.core.mail_service import enviar_correo_sistema
from apps.empresas.models import Departamento, Empresa, EmpresaModulo, Modulo, Municipio, Plan
from apps.facturacion.models import Factura, FacturaItem, ReciboCaja
from apps.inventario.models import (
    Categoria,
    CompraItem,
    CompraProveedor,
    MovimientoInventario,
    Producto,
    UnidadMedida,
)
from apps.nomina.models import Empleado, Nomina, NominaEmpleado
from apps.pos.models import Caja, CajaTurno
from apps.servicios.models import OrdenServicio, ServicioEquipo
from apps.terceros.models import Tercero
from apps.usuarios.models import EmpresaUsuario, User


class Command(BaseCommand):
    help = "Inyecta una empresa integral de pruebas con datos reales en todos los módulos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--notificar-superadmin",
            action="store_true",
            help="Envía el usuario y clave temporal generados por correo cifrado al SuperAdmin sin imprimir claves en logs.",
        )
        parser.add_argument(
            "--mostrar-claves",
            action="store_true",
            help="Muestra las credenciales generadas por consola (usar solo en entorno local).",
        )
        parser.add_argument(
            "--correo-destino",
            type=str,
            default="",
            help="Correo explícito al cual enviar las credenciales de acceso generadas.",
        )

    def handle(self, *args, **options):
        nit_demo = "901777999"
        if Empresa.objects.filter(nit=nit_demo).exists():
            self.stdout.write(self.style.SUCCESS(f"La empresa de pruebas (NIT {nit_demo}) ya existe. Nada que inyectar."))
            return

        hoy = date.today()
        clave_propietario = get_random_string(12)
        clave_equipo = get_random_string(12)

        with transaction.atomic():
            plan_pro = (
                Plan.objects.filter(nombre__icontains="Profesional").first()
                or Plan.objects.filter(activo=True).order_by("-precio_mensual").first()
            )
            dep = Departamento.objects.filter(nombre__icontains="BOGOT").first() or Departamento.objects.first()
            mun = Municipio.objects.filter(departamento=dep).first() if dep else Municipio.objects.first()

            empresa = Empresa.objects.create(
                razon_social="Comercializadora El Progreso SAS",
                nombre_comercial="El Progreso Tech & Market",
                nit=nit_demo,
                dv="4",
                tipo_persona="JURIDICA",
                regimen_tributario="RESPONSABLE_IVA",
                perfil_operativo="GENERAL",
                direccion="Av. Carrera 15 # 82 - 40 Local 102",
                departamento=dep,
                municipio=mun,
                telefono="3105554321",
                email="contacto@elprogreso.co",
                prefijo_factura="FE",
                prefijo_pos_electronico="POS",
                resolucion_dian="18764000998877",
                fecha_resolucion=hoy - timedelta(days=30),
                rango_desde=1,
                rango_hasta=50000,
                consecutivo_actual=1,
                consecutivo_pos_actual=1,
                modo_dian="PRUEBAS",
                dian_software_id="SW-PROGRESO-2026",
                dian_pin="99887",
                dian_test_set_id="TESTSET-PROGRESO-01",
                plan=plan_pro,
                activa=True,
            )

            for mod in Modulo.objects.all():
                EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=mod, defaults={"activo": True})

            # 1. Usuarios del equipo de la empresa
            cuentas_equipo = [
                ("pruebas.empresa@faccol.online", "Carlos", "Mendoza", "propietario", clave_propietario),
                ("vendedor.pruebas@faccol.online", "Sofía", "Rojas", "vendedor", clave_equipo),
                ("contador.pruebas@faccol.online", "Andrés", "Gutiérrez", "contador", clave_equipo),
                ("tecnico.pruebas@faccol.online", "Julián", "Pérez", "operador", clave_equipo),
            ]
            usuarios_creados = {}
            for email_u, nom, ape, rol, pwd in cuentas_equipo:
                u, created = User.objects.get_or_create(
                    username=email_u,
                    defaults={
                        "email": email_u,
                        "first_name": nom,
                        "last_name": ape,
                        "is_superadmin": False,
                        "is_staff": False,
                        "is_superuser": False,
                        "activo": True,
                    },
                )
                if created:
                    u.set_password(pwd)
                    u.save()
                EmpresaUsuario.objects.create(user=u, empresa=empresa, rol=rol, activo=True)
                usuarios_creados[rol] = u

            propietario = usuarios_creados["propietario"]

            # 2. Plan de Cuentas base adicional (Patrimonio y Gastos iniciales)
            cta_banco, _ = PlanCuenta.objects.get_or_create(
                empresa=empresa, codigo="111005",
                defaults={"nombre": "Bancos Nacionales", "nivel": 6, "naturaleza": "DEBITO", "permite_movimiento": True},
            )
            cta_cap, _ = PlanCuenta.objects.get_or_create(
                empresa=empresa, codigo="311505",
                defaults={"nombre": "Aportes Sociales", "nivel": 6, "naturaleza": "CREDITO", "permite_movimiento": True},
            )
            asiento_apertura = AsientoContable.objects.create(
                empresa=empresa,
                numero_comprobante="AP-001",
                fecha=hoy - timedelta(days=25),
                tipo="GENERAL",
                concepto="Capital inicial de trabajo Comercializadora El Progreso SAS",
                referencia_origen="ACTA-001",
            )
            AsientoLinea.objects.create(
                asiento=asiento_apertura, cuenta=cta_banco, descripcion="Aporte inicial en bancos",
                debito=Decimal("25000000.00"), credito=Decimal("0.00"),
            )
            AsientoLinea.objects.create(
                asiento=asiento_apertura, cuenta=cta_cap, descripcion="Capital suscrito y pagado",
                debito=Decimal("0.00"), credito=Decimal("25000000.00"),
            )

            # 3. Terceros (Clientes y Proveedores)
            cf = Tercero.objects.create(
                empresa=empresa, tipo_persona="NATURAL", tipo_documento="CC", numero_documento="222222222222",
                razon_social="Consumidor Final", es_cliente=True, regimen="NO_RESPONSABLE_IVA",
            )
            cli_fiado_1 = Tercero.objects.create(
                empresa=empresa, tipo_persona="NATURAL", tipo_documento="CC", numero_documento="79888111",
                razon_social="Doña Martha Rodríguez", telefono="3112223344", email="martha@vecino.co",
                direccion="Calle 80 # 14-10", es_cliente=True, regimen="NO_RESPONSABLE_IVA",
            )
            cli_fiado_2 = Tercero.objects.create(
                empresa=empresa, tipo_persona="NATURAL", tipo_documento="CC", numero_documento="52444777",
                razon_social="Luis Alberto Torres", telefono="3209998877", email="luis@vecino.co",
                direccion="Cra 16 # 81-22", es_cliente=True, regimen="NO_RESPONSABLE_IVA",
            )
            cli_corp_1 = Tercero.objects.create(
                empresa=empresa, tipo_persona="JURIDICA", tipo_documento="NIT", numero_documento="900555111", dv="3",
                razon_social="Inversiones Andinas Ltda", telefono="6017778899", email="compras@andinas.co",
                direccion="Calle 93 # 11-30", es_cliente=True, regimen="RESPONSABLE_IVA",
                aplica_retefuente=True, tarifa_retefuente=Decimal("2.50"),
                aplica_reteica=True, tarifa_reteica=Decimal("9.66"),
            )
            cli_corp_2 = Tercero.objects.create(
                empresa=empresa, tipo_persona="JURIDICA", tipo_documento="NIT", numero_documento="800112233", dv="7",
                razon_social="Constructora Horizonte SAS", telefono="6014445566", email="tesoreria@horizonte.co",
                direccion="Autopista Norte # 104-12", es_cliente=True, regimen="GRAN_CONTRIBUYENTE",
                aplica_retefuente=True, tarifa_retefuente=Decimal("2.50"),
            )
            prov_1 = Tercero.objects.create(
                empresa=empresa, tipo_persona="JURIDICA", tipo_documento="NIT", numero_documento="900888222", dv="5",
                razon_social="Distribuidora Nacional de Bebidas SAS", telefono="6013214567",
                email="cartera@disbebidas.co", es_cliente=False, es_proveedor=True, regimen="RESPONSABLE_IVA",
            )
            prov_2 = Tercero.objects.create(
                empresa=empresa, tipo_persona="JURIDICA", tipo_documento="NIT", numero_documento="901222444", dv="1",
                razon_social="Mayorista Tech Importadores SAS", telefono="6019876543",
                email="ventas@techimport.co", es_cliente=False, es_proveedor=True, regimen="RESPONSABLE_IVA",
            )
            prov_rural = Tercero.objects.create(
                empresa=empresa, tipo_persona="NATURAL", tipo_documento="CC", numero_documento="19333444",
                razon_social="Finca Lácteos Don José (No Obligado)", telefono="3156667788",
                email="donjose@lacteos.co", es_cliente=False, es_proveedor=True, regimen="NO_RESPONSABLE_IVA",
            )

            # 4. Categorías, Unidades y Productos
            cat_beb = Categoria.objects.create(empresa=empresa, nombre="Bebidas y Refrescos")
            cat_aba = Categoria.objects.create(empresa=empresa, nombre="Abarrotes y Despensa")
            cat_tec = Categoria.objects.create(empresa=empresa, nombre="Tecnología y Cómputo")
            cat_rep = Categoria.objects.create(empresa=empresa, nombre="Repuestos y Accesorios")

            un_und = UnidadMedida.objects.create(empresa=empresa, codigo="94", nombre="Unidad")
            un_lb = UnidadMedida.objects.create(empresa=empresa, codigo="LBR", nombre="Libra")

            productos_spec = [
                ("GAS-400", "7702004001011", "Gaseosa Cola 400ml", cat_beb, un_und, Decimal("3500"), Decimal("2100"), Decimal("19"), Decimal("10"), True, True, False, Decimal("12"), Decimal("120")),
                ("JUG-500", "7702004002025", "Jugo Naranja Caja 500ml", cat_beb, un_und, Decimal("4200"), Decimal("2600"), Decimal("19"), Decimal("10"), True, True, False, Decimal("12"), Decimal("72")),
                ("AGU-600", "7702004003039", "Agua Manantial 600ml", cat_beb, un_und, Decimal("2200"), Decimal("1100"), Decimal("0"), Decimal("0"), True, True, False, Decimal("24"), Decimal("96")),
                ("ARR-500", "7701001001112", "Arroz Blanco Premium 500g", cat_aba, un_und, Decimal("2800"), Decimal("1950"), Decimal("0"), Decimal("0"), True, True, False, Decimal("25"), Decimal("150")),
                ("QUE-LB", "7701002002224", "Queso Campesino Fresco (Lb)", cat_aba, un_lb, Decimal("9500"), Decimal("6800"), Decimal("0"), Decimal("0"), True, True, True, Decimal("1"), Decimal("35")),
                ("ACE-900", "7701003003336", "Aceite Vegetal 900ml", cat_aba, un_und, Decimal("11500"), Decimal("8700"), Decimal("19"), Decimal("0"), True, False, False, Decimal("12"), Decimal("48")),
                ("POR-14", "7705001001001", "Portátil Empresarial 14\" 16GB/512GB", cat_tec, un_und, Decimal("2450000"), Decimal("1850000"), Decimal("19"), Decimal("0"), False, False, False, Decimal("1"), Decimal("10")),
                ("IMP-POS", "7705001002008", "Impresora Térmica USB/LAN 80mm", cat_tec, un_und, Decimal("420000"), Decimal("295000"), Decimal("19"), Decimal("0"), False, False, False, Decimal("1"), Decimal("14")),
                ("LEC-COD", "7705001003005", "Lector Código de Barras Láser", cat_tec, un_und, Decimal("185000"), Decimal("115000"), Decimal("19"), Decimal("0"), False, True, False, Decimal("1"), Decimal("18")),
                ("SSD-512", "7705002004002", "Disco SSD NVMe 512GB Repuesto", cat_rep, un_und, Decimal("195000"), Decimal("135000"), Decimal("19"), Decimal("0"), False, False, False, Decimal("1"), Decimal("22")),
            ]
            prods = {}
            for cod, bar, nom, cat, un, pv, cu, iva, sal, inc, fav, frac, fconv, stk in productos_spec:
                p = Producto.objects.create(
                    empresa=empresa, codigo=cod, codigo_barras=bar, nombre=nom,
                    categoria=cat, unidad_medida=un, precio_venta=pv, costo_unitario=cu,
                    tarifa_iva=iva, impuesto_saludable_pct=sal, precio_incluye_impuestos=inc,
                    es_favorito_pos=fav, permite_fraccion=frac, factor_conversion=fconv,
                    stock_actual=stk, stock_minimo=Decimal("10.00"), maneja_inventario=True,
                )
                prods[cod] = p
                MovimientoInventario.objects.create(
                    empresa=empresa, producto=p, tipo="ENTRADA", cantidad=stk,
                    costo_unitario=cu, saldo_anterior=Decimal("0.00"), saldo_nuevo=stk,
                    motivo="Saldo inicial de inventario (Empresa de Pruebas)",
                    referencia_documento="INV-INI",
                )

            # 5. Compras a Proveedores & Documento Soporte Electrónico (DSE)
            compra_dse = CompraProveedor.objects.create(
                empresa=empresa, consecutivo=1, proveedor=prov_rural,
                numero_factura_proveedor="REC-JOSE-01", fecha=hoy - timedelta(days=10),
                metodo_pago="CONTADO", es_documento_soporte=True,
                observaciones="Compra directa de queso artesanal (No obligado a facturar)",
            )
            ci_1 = CompraItem.objects.create(
                compra=compra_dse, producto=prods["QUE-LB"],
                cantidad_empaque=Decimal("20"), unidades_por_empaque=Decimal("1"),
                costo_unitario_empaque=Decimal("6800"),
            )
            compra_dse.total = ci_1.subtotal
            compra_dse.cuds = hashlib.sha384(f"DSE1{hoy}{compra_dse.total}{empresa.nit}".encode()).hexdigest()
            compra_dse.estado_dian = "ENVIADA"
            compra_dse.save()
            prods["QUE-LB"].stock_actual += ci_1.unidades_totales
            prods["QUE-LB"].save()
            MovimientoInventario.objects.create(
                empresa=empresa, producto=prods["QUE-LB"], tipo="ENTRADA",
                cantidad=ci_1.unidades_totales, costo_unitario=Decimal("6800"),
                saldo_anterior=Decimal("35"), saldo_nuevo=prods["QUE-LB"].stock_actual,
                motivo="Compra DSE1", referencia_documento="DSE1",
            )
            registrar_asiento_compra(compra_dse)

            compra_pacas = CompraProveedor.objects.create(
                empresa=empresa, consecutivo=2, proveedor=prov_1,
                numero_factura_proveedor="FV-45902", fecha=hoy - timedelta(days=6),
                metodo_pago="CREDITO", es_documento_soporte=False,
                observaciones="Compra de 5 pacas de gaseosa (5 x 12 unidades)",
            )
            ci_2 = CompraItem.objects.create(
                compra=compra_pacas, producto=prods["GAS-400"],
                cantidad_empaque=Decimal("5"), unidades_por_empaque=Decimal("12"),
                costo_unitario_empaque=Decimal("25200"),
            )
            compra_pacas.total = ci_2.subtotal
            compra_pacas.save()
            prods["GAS-400"].stock_actual += ci_2.unidades_totales
            prods["GAS-400"].save()
            MovimientoInventario.objects.create(
                empresa=empresa, producto=prods["GAS-400"], tipo="ENTRADA",
                cantidad=ci_2.unidades_totales, costo_unitario=Decimal("2100"),
                saldo_anterior=Decimal("120"), saldo_nuevo=prods["GAS-400"].stock_actual,
                motivo="Compra OC2 (5 pacas x 12)", referencia_documento="OC2",
            )
            registrar_asiento_compra(compra_pacas)

            # Ajuste manual de merma en inventario
            prods["JUG-500"].stock_actual -= Decimal("2")
            prods["JUG-500"].save()
            MovimientoInventario.objects.create(
                empresa=empresa, producto=prods["JUG-500"], tipo="SALIDA",
                cantidad=Decimal("2"), costo_unitario=Decimal("2600"),
                saldo_anterior=Decimal("72"), saldo_nuevo=Decimal("70"),
                motivo="Ajuste por avería en bodega", referencia_documento="AJUSTE-01",
            )

            # 6. Cajas y Turnos POS
            caja1 = Caja.objects.create(empresa=empresa, nombre="Caja Principal 1", codigo="CAJA-01")
            caja2 = Caja.objects.create(empresa=empresa, nombre="Caja Express 2", codigo="CAJA-02")
            CajaTurno.objects.create(
                empresa=empresa, caja=caja1, usuario=usuarios_creados["vendedor"],
                fecha_cierre=timezone.now() - timedelta(days=1),
                monto_inicial=Decimal("100000"), monto_ventas_efectivo=Decimal("185000"),
                monto_ventas_otros=Decimal("45000"), monto_total_esperado=Decimal("285000"),
                monto_cierre_real=Decimal("285000"), diferencia=Decimal("0"),
                estado="CERRADA", observaciones="Arqueo anterior cuadrado sin novedades",
            )
            CajaTurno.objects.create(
                empresa=empresa, caja=caja2, usuario=propietario,
                monto_inicial=Decimal("150000"), monto_ventas_efectivo=Decimal("21000"),
                monto_total_esperado=Decimal("171000"), estado="ABIERTA",
            )

            # 7. Facturas Electrónicas, DEE POS, Fiados, Cotizaciones, Remisiones y Nota Crédito
            def crear_doc(tipo, pref, cons, cliente, estado_doc, metodo, es_fiado, items_list, dias_atras=0, ret=Decimal("0")):
                f = Factura.objects.create(
                    empresa=empresa, tipo_documento=tipo, prefijo=pref, consecutivo=cons,
                    tercero=cliente, fecha_emision=hoy - timedelta(days=dias_atras),
                    fecha_vencimiento=hoy + timedelta(days=30 - dias_atras),
                    estado=estado_doc, metodo_pago=metodo, es_fiado=es_fiado,
                    retenciones=ret,
                )
                for prod_obj, cant, precio_u, iva_pct, otro_pct in items_list:
                    FacturaItem.objects.create(
                        factura=f, producto=prod_obj, descripcion=prod_obj.nombre,
                        cantidad=Decimal(str(cant)), precio_unitario=Decimal(str(precio_u)),
                        porcentaje_iva=Decimal(str(iva_pct)), porcentaje_otro_impuesto=Decimal(str(otro_pct)),
                    )
                    if tipo in ("FACTURA", "POS_ELECTRONICO", "REMISION") and prod_obj.maneja_inventario:
                        s_ant = prod_obj.stock_actual
                        prod_obj.stock_actual = max(Decimal("0"), s_ant - Decimal(str(cant)))
                        prod_obj.save(update_fields=["stock_actual"])
                        MovimientoInventario.objects.create(
                            empresa=empresa, producto=prod_obj, tipo="SALIDA",
                            cantidad=Decimal(str(cant)), costo_unitario=prod_obj.costo_unitario,
                            saldo_anterior=s_ant, saldo_nuevo=prod_obj.stock_actual,
                            motivo=f"Venta {f.numero_completo}", referencia_documento=f.numero_completo,
                        )
                f.recalcular_totales()
                if tipo in ("FACTURA", "POS_ELECTRONICO"):
                    f.generar_cufe()
                    f.estado_dian = "ENVIADA" if cons % 2 == 1 else "PENDIENTE"
                    f.dian_track_id = f"DIAN-HAB-{f.cufe[:16].upper()}" if f.estado_dian == "ENVIADA" else ""
                    f.save(update_fields=["estado_dian", "dian_track_id"])
                    registrar_asiento_venta(f)
                else:
                    f.estado_dian = "NO_APLICA"
                    f.saldo_pendiente = Decimal("0.00")
                    f.save(update_fields=["estado_dian", "saldo_pendiente"])
                return f

            # Factura 1: Venta B2B corporativa a crédito con ReteFuente 2.5% + abono parcial
            fac1 = crear_doc(
                "FACTURA", "FE", 1, cli_corp_1, "EMITIDA", "CREDITO", False,
                [(prods["POR-14"], 2, 2450000, 19, 0)], dias_atras=8,
                ret=Decimal("122500.00"),
            )
            rc1 = ReciboCaja.objects.create(
                empresa=empresa, consecutivo=1, factura=fac1, tercero=cli_corp_1,
                fecha=hoy - timedelta(days=3), valor=Decimal("2500000.00"),
                metodo_pago="TRANSFERENCIA", referencia_pago="BAN-99881",
                observaciones="Abono inicial 50% contrato",
            )
            fac1.recalcular_totales()
            registrar_asiento_recibo(rc1)

            # Factura 2: Venta de contado pagada totalmente
            fac2 = crear_doc(
                "FACTURA", "FE", 2, cli_corp_2, "PAGADA", "CONTADO", False,
                [(prods["IMP-POS"], 2, 420000, 19, 0), (prods["LEC-COD"], 2, 185000, 19, 0)], dias_atras=4,
            )
            ReciboCaja.objects.create(
                empresa=empresa, consecutivo=2, factura=fac2, tercero=cli_corp_2,
                fecha=hoy - timedelta(days=4), valor=fac2.total,
                metodo_pago="NEQUI", referencia_pago="NEQ-44112",
                observaciones="Pago inmediato Factura FE2",
            )
            fac2.recalcular_totales()

            # Factura 3 + Nota Crédito NC1 (anulación de prueba con devolución de stock)
            fac3 = crear_doc(
                "FACTURA", "FE", 3, cli_corp_2, "EMITIDA", "CREDITO", False,
                [(prods["SSD-512"], 1, 195000, 19, 0)], dias_atras=5,
            )
            nc1 = Factura.objects.create(
                empresa=empresa, tipo_documento="NOTA_CREDITO", prefijo="NC", consecutivo=1,
                tercero=cli_corp_2, fecha_emision=hoy - timedelta(days=2), fecha_vencimiento=hoy,
                estado="EMITIDA", metodo_pago="CREDITO", observaciones="Devolución por cambio de referencia",
                factura_referencia=fac3,
            )
            FacturaItem.objects.create(
                factura=nc1, producto=prods["SSD-512"], descripcion=prods["SSD-512"].nombre,
                cantidad=Decimal("1"), precio_unitario=Decimal("195000"), porcentaje_iva=Decimal("19"),
            )
            prods["SSD-512"].stock_actual += Decimal("1")
            prods["SSD-512"].save()
            nc1.recalcular_totales()
            nc1.saldo_pendiente = Decimal("0.00")
            nc1.save(update_fields=["saldo_pendiente"])
            nc1.generar_cufe()
            fac3.estado = "ANULADA"
            fac3.saldo_pendiente = Decimal("0.00")
            fac3.save(update_fields=["estado", "saldo_pendiente"])
            registrar_asiento_nota_credito(nc1, factura_origen=fac3)

            # Tiquetes POS y Fiados (Libreta de Fiados)
            crear_doc(
                "POS_ELECTRONICO", "POS", 1, cf, "PAGADA", "CONTADO", False,
                [(prods["GAS-400"], 3, 2713.18, 19, 10), (prods["ARR-500"], 2, 2800, 0, 0)], dias_atras=1,
            )
            crear_doc(
                "POS_ELECTRONICO", "POS", 2, cli_fiado_1, "EMITIDA", "CREDITO", True,
                [(prods["QUE-LB"], 2, 9500, 0, 0), (prods["ACE-900"], 1, 9663.87, 19, 0)], dias_atras=3,
            )
            crear_doc(
                "POS_ELECTRONICO", "POS", 3, cli_fiado_2, "EMITIDA", "CREDITO", True,
                [(prods["JUG-500"], 4, 3255.81, 19, 10), (prods["ARR-500"], 3, 2800, 0, 0)], dias_atras=2,
            )

            # Cotizaciones y Remisiones listas para conversión
            crear_doc(
                "COTIZACION", "COT", 1, cli_corp_1, "EMITIDA", "CREDITO", False,
                [(prods["POR-14"], 1, 2450000, 19, 0), (prods["IMP-POS"], 1, 420000, 19, 0)], dias_atras=1,
            )
            crear_doc(
                "COTIZACION", "COT", 2, cli_corp_2, "EMITIDA", "CONTADO", False,
                [(prods["LEC-COD"], 5, 185000, 19, 0)], dias_atras=0,
            )
            crear_doc(
                "REMISION", "REM", 1, cli_corp_1, "EMITIDA", "CREDITO", False,
                [(prods["AGU-600"], 10, 2200, 0, 0)], dias_atras=1,
            )

            empresa.consecutivo_actual = 4
            empresa.consecutivo_pos_actual = 4
            empresa.save(update_fields=["consecutivo_actual", "consecutivo_pos_actual"])

            # 8. Servicios y Taller Kanban (4 equipos y 5 órdenes)
            eq1 = ServicioEquipo.objects.create(
                empresa=empresa, cliente=cli_fiado_1, tipo_equipo="Laptop", marca="HP", modelo="Pavilion 15",
                serial="HP-998811", accesorios="Cargador original",
            )
            eq2 = ServicioEquipo.objects.create(
                empresa=empresa, cliente=cli_corp_1, tipo_equipo="Impresora POS", marca="Epson", modelo="TM-T20III",
                serial="EP-554422", accesorios="Cable de poder",
            )
            eq3 = ServicioEquipo.objects.create(
                empresa=empresa, cliente=cli_fiado_2, tipo_equipo="Smartphone", marca="Samsung", modelo="Galaxy A54",
                serial="IMEI-35998877", accesorios="Funda transparente",
            )
            eq4 = ServicioEquipo.objects.create(
                empresa=empresa, cliente=cli_corp_2, tipo_equipo="PC Escritorio", marca="Dell", modelo="OptiPlex 7090",
                serial="DL-774411", accesorios="Solo torre",
            )

            tecnico = usuarios_creados["operador"]
            OrdenServicio.objects.create(
                empresa=empresa, consecutivo=1, cliente=cli_fiado_1, equipo=eq1, tecnico_asignado=tecnico,
                falla_reportada="Lentitud extrema y recalentamiento", diagnostico_tecnico="Pasta térmica seca y disco mecánico lento",
                solucion_aplicada="Cambio a SSD y limpieza interna", costo_repuestos=Decimal("195000"),
                costo_mano_obra=Decimal("90000"), estado="LISTO", prioridad="ALTA",
            )
            OrdenServicio.objects.create(
                empresa=empresa, consecutivo=2, cliente=cli_corp_1, equipo=eq2, tecnico_asignado=tecnico,
                falla_reportada="No corta el papel térmico automáticamente", diagnostico_tecnico="Cuchilla atascada con etiqueta",
                costo_repuestos=Decimal("45000"), costo_mano_obra=Decimal("60000"), estado="EN_REPARACION", prioridad="NORMAL",
            )
            OrdenServicio.objects.create(
                empresa=empresa, consecutivo=3, cliente=cli_fiado_2, equipo=eq3, tecnico_asignado=tecnico,
                falla_reportada="Puerto USB-C con falso contacto al cargar",
                costo_repuestos=Decimal("55000"), costo_mano_obra=Decimal("65000"), estado="PRESUPUESTADO", prioridad="NORMAL",
            )
            OrdenServicio.objects.create(
                empresa=empresa, consecutivo=4, cliente=cli_corp_2, equipo=eq4, tecnico_asignado=tecnico,
                falla_reportada="No da video al encender", estado="RECIBIDO", prioridad="BAJA",
            )

            # 9. Empleados y Nómina Electrónica liquidada del mes actual
            emp1 = Empleado.objects.create(
                empresa=empresa, tipo_documento="CC", numero_documento="1020304050",
                nombres="Camila", apellidos="Castillo", email="camila@elprogreso.co",
                cargo="Cajera Punto de Venta", salario_base=Decimal("1423500.00"),
                tiene_auxilio_transporte=True, fecha_ingreso=hoy - timedelta(days=200),
                eps="Sura EPS", fondo_pension="Porvenir", caja_compensacion="Compensar",
            )
            emp2 = Empleado.objects.create(
                empresa=empresa, tipo_documento="CC", numero_documento="1030405060",
                nombres="Mateo", apellidos="Herrera", email="mateo@elprogreso.co",
                cargo="Técnico de Soporte", salario_base=Decimal("2400000.00"),
                tiene_auxilio_transporte=True, fecha_ingreso=hoy - timedelta(days=150),
                eps="Sanitas EPS", fondo_pension="Protección", caja_compensacion="Colsubsidio",
            )
            emp3 = Empleado.objects.create(
                empresa=empresa, tipo_documento="CC", numero_documento="79500600",
                nombres="Valeria", apellidos="Morales", email="valeria@elprogreso.co",
                cargo="Directora Financiera", salario_base=Decimal("6200000.00"),
                tiene_auxilio_transporte=False, fecha_ingreso=hoy - timedelta(days=320),
                eps="Sura EPS", fondo_pension="Colpensiones", caja_compensacion="Compensar",
            )

            smmlv = Decimal("1423500.00")
            aux_legal = Decimal("202050.00")
            ultimo_dia = calendar.monthrange(hoy.year, hoy.month)[1]
            nomina = Nomina.objects.create(
                empresa=empresa, periodo_mes=hoy.month, periodo_ano=hoy.year,
                fecha_inicio=date(hoy.year, hoy.month, 1), fecha_fin=date(hoy.year, hoy.month, ultimo_dia),
                estado="LIQUIDADA", smmlv=smmlv, auxilio_transporte_legal=aux_legal,
            )
            tot_dev = Decimal("0.00")
            tot_ded = Decimal("0.00")
            tot_net = Decimal("0.00")
            for tr in (emp1, emp2, emp3):
                sal = tr.salario_base
                aux = aux_legal if (tr.tiene_auxilio_transporte and sal <= smmlv * 2) else Decimal("0.00")
                dev = sal + aux
                s4 = (sal * Decimal("0.04")).quantize(Decimal("0.01"))
                p4 = (sal * Decimal("0.04")).quantize(Decimal("0.01"))
                fsp = (sal * Decimal("0.01")).quantize(Decimal("0.01")) if sal >= smmlv * 4 else Decimal("0.00")
                ded = s4 + p4 + fsp
                neto = dev - ded
                NominaEmpleado.objects.create(
                    nomina=nomina, empleado=tr, dias_trabajados=30,
                    sueldo_basico_devengado=sal, auxilio_transporte=aux, total_devengado=dev,
                    salud_empleado_4=s4, pension_empleado_4=p4, otras_deducciones=fsp,
                    total_deducciones=ded, neto_a_pagar=neto,
                    salud_empleador_8_5=(sal * Decimal("0.085")).quantize(Decimal("0.01")),
                    pension_empleador_12=(sal * Decimal("0.12")).quantize(Decimal("0.01")),
                    arl_empleador=(sal * Decimal("0.00522")).quantize(Decimal("0.01")),
                    caja_compensacion_4=(sal * Decimal("0.04")).quantize(Decimal("0.01")),
                    provision_prima=(dev * Decimal("0.0833")).quantize(Decimal("0.01")),
                    provision_cesantias=(dev * Decimal("0.0833")).quantize(Decimal("0.01")),
                    provision_intereses=(dev * Decimal("0.0833") * Decimal("0.12")).quantize(Decimal("0.01")),
                    provision_vacaciones=(sal * Decimal("0.0417")).quantize(Decimal("0.01")),
                )
                tot_dev += dev
                tot_ded += ded
                tot_net += neto
            nomina.total_devengado = tot_dev
            nomina.total_deducciones = tot_ded
            nomina.total_neto = tot_net
            nomina.save()
            registrar_asiento_nomina(nomina)

        self.stdout.write(self.style.SUCCESS(
            f"Empresa '{empresa.razon_social}' (NIT {empresa.nit_completo}) inyectada con datos completos en todos los módulos."
        ))

        if options.get("notificar_superadmin") or options.get("correo_destino"):
            destinos = []
            if options.get("correo_destino"):
                destinos.append(options["correo_destino"].strip())
            else:
                destinos.extend(
                    list(User.objects.filter(is_superadmin=True).exclude(email="").values_list("email", flat=True))
                )
            destinos = list(dict.fromkeys(destinos))
            if destinos:
                html = (
                    f"<h2>Empresa de Pruebas Creada en FacCol</h2>"
                    f"<p><b>Empresa:</b> {empresa.razon_social} (NIT {empresa.nit_completo})</p>"
                    f"<p><b>Propietario (acceso completo):</b><br>"
                    f"Usuario / Correo: <code>{propietario.email}</code><br>"
                    f"Contraseña temporal: <code>{clave_propietario}</code></p>"
                    f"<p><b>Usuarios adicionales del equipo</b> (clave compartida <code>{clave_equipo}</code>):</p>"
                    f"<ul>"
                    f"<li>Vendedor: <code>vendedor.pruebas@faccol.online</code></li>"
                    f"<li>Contador: <code>contador.pruebas@faccol.online</code></li>"
                    f"<li>Técnico / Operador: <code>tecnico.pruebas@faccol.online</code></li>"
                    f"</ul>"
                    f"<p>Incluye productos, compras/DSE, facturas, DEE POS, fiados, cotizaciones, remisiones, nota crédito, asientos PUC, taller Kanban y nómina liquidada.</p>"
                )
                for dest in destinos:
                    try:
                        enviar_correo_sistema(
                            dest,
                            "Credenciales Empresa de Pruebas — Comercializadora El Progreso SAS",
                            html,
                            fail_silently=False,
                        )
                        self.stdout.write(self.style.SUCCESS(f"Credenciales enviadas por correo cifrado a {dest}."))
                    except Exception as exc:
                        self.stdout.write(self.style.WARNING(f"No se pudo enviar correo a {dest}: {exc}"))

        if options.get("mostrar_claves"):
            self.stdout.write(
                f"CREDENCIALES_PROPIETARIO: {propietario.email} / {clave_propietario} | "
                f"EQUIPO_CLAVE: {clave_equipo}"
            )
