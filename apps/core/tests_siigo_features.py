import io
import json
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from apps.contabilidad.models import (
    AsientoContable,
    AsientoLinea,
    ComprobanteEgreso,
    GastoOperativo,
    PeriodoContableCerrado,
    PlanCuenta,
)
from apps.empresas.models import Empresa, Modulo, Plan
from apps.facturacion.models import Factura, ReciboCaja
from apps.inventario.models import Bodega, CompraProveedor, Producto, StockBodega
from apps.nomina.models import Empleado, Nomina, NominaEmpleado
from apps.pos.models import Caja, CajaTurno
from apps.terceros.models import Tercero
from apps.usuarios.models import EmpresaUsuario, User


class CompatibilidadSiigoTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(nombre="Full Siigo", precio_mensual=199000, limite_facturas_mes=0, limite_usuarios=0)
        for cod, nom in (
            ("facturacion", "Facturación"),
            ("pos", "POS"),
            ("inventario", "Inventario"),
            ("contabilidad", "Contabilidad"),
            ("nomina", "Nómina"),
            ("terceros", "Terceros"),
        ):
            m, _ = Modulo.objects.get_or_create(codigo=cod, defaults={"nombre": nom})
            self.plan.modulos.add(m)

        self.empresa = Empresa.objects.create(
            razon_social="Distribuidora SiigoPro SAS",
            nit="901666777",
            dv="1",
            email="contabilidad@siigopro.co",
            plan=self.plan,
            resolucion_dian="1876400999",
            dian_software_id="SW-SIIGO-1",
            dian_pin="99999",
            dian_test_set_id="TS-999",
            rango_desde=1,
            rango_hasta=100,
            consecutivo_actual=60,
        )
        self.empresa.sincronizar_modulos_con_plan(self.plan)

        self.admin = User.objects.create_user(
            username="admin_sp@siigopro.co", email="admin_sp@siigopro.co", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=self.admin, empresa=self.empresa, rol="propietario", activo=True)

        self.prod = Producto.objects.create(
            empresa=self.empresa,
            codigo="CAFE-1",
            nombre="Café Especial",
            precio_venta=Decimal("20000.00"),
            costo_unitario=Decimal("12000.00"),
            tarifa_iva=Decimal("0.00"),
            maneja_inventario=True,
            stock_actual=Decimal("20.00"),
        )
        self.cliente = Tercero.objects.create(
            empresa=self.empresa, numero_documento="102030", razon_social="Cliente Crédito",
            email="cli@siigopro.co", es_cliente=True,
        )
        self.prov = Tercero.objects.create(
            empresa=self.empresa, numero_documento="800900", razon_social="Proveedor Mayorista",
            es_cliente=False, es_proveedor=True,
        )
        self.cta_arr = PlanCuenta.objects.create(
            empresa=self.empresa, codigo="512010", nombre="Arrendamientos", naturaleza="DEBITO"
        )
        self.cta_caja = PlanCuenta.objects.create(
            empresa=self.empresa, codigo="110505", nombre="Caja General", naturaleza="DEBITO"
        )
        self.cta_vta = PlanCuenta.objects.create(
            empresa=self.empresa, codigo="413505", nombre="Comercio", naturaleza="CREDITO"
        )

    def test_costo_ventas_automatico_egresos_gastos_cierres_certificados_exogena_xlsx(self):
        client = Client()
        client.force_login(self.admin)

        # 1. Factura a crédito -> genera venta Y costo de ventas (613505 vs 143505) + alerta resolución DIAN (100 - 61 = 39 <= 50)
        client.post(reverse("facturacion:factura_crear"), {
            "tercero": str(self.cliente.id),
            "tipo_documento": "FACTURA",
            "metodo_pago": "CREDITO",
            "item_producto[]": [str(self.prod.id)],
            "item_cantidad[]": ["2"],
            "item_precio[]": ["20000"],
        }, follow=True)
        fac = Factura.objects.get(empresa=self.empresa, tipo_documento="FACTURA")
        asiento_vta = AsientoContable.objects.get(empresa=self.empresa, referencia_origen=fac.numero_completo)
        self.assertTrue(asiento_vta.esta_cuadrado)
        self.assertTrue(asiento_vta.lineas.filter(cuenta__codigo="613505", debito=Decimal("24000.00")).exists())
        self.assertTrue(asiento_vta.lineas.filter(cuenta__codigo="143505", credito=Decimal("24000.00")).exists())

        # 2. Compra a crédito crea CxP y ComprobanteEgreso la abona y cuadra
        client.post(reverse("inventario:compra_crear"), {
            "proveedor": str(self.prov.id),
            "metodo_pago": "CREDITO",
            "es_documento_soporte": "on",
            "item_producto[]": [str(self.prod.id)],
            "item_cantidad[]": ["1"],
            "item_factor[]": ["1"],
            "item_costo[]": ["12000"],
        }, follow=True)
        compra = CompraProveedor.objects.get(empresa=self.empresa)
        self.assertEqual(compra.saldo_pendiente, Decimal("12000.00"))
        client.post(reverse("contabilidad:egreso_crear"), {
            "compra": str(compra.id),
            "fecha": date.today().isoformat(),
            "valor": "7000",
            "metodo_pago": "TRANSFERENCIA",
        }, follow=True)
        compra.refresh_from_db()
        self.assertEqual(compra.saldo_pendiente, Decimal("5000.00"))
        self.assertTrue(ComprobanteEgreso.objects.filter(empresa=self.empresa, valor=Decimal("7000.00")).exists())

        # Transmitir DSE vía view
        r_dse = client.post(reverse("inventario:compra_transmitir_dse", args=[compra.id]), follow=True)
        self.assertEqual(r_dse.status_code, 200)
        compra.refresh_from_db()
        self.assertEqual(compra.estado_dian, "ENVIADA")

        # 3. Gasto operativo + Certificado de Retención PDF + Exógena XLSX + Estados Financieros XLSX
        client.post(reverse("contabilidad:gasto_crear"), {
            "tercero": str(self.prov.id),
            "cuenta_gasto": str(self.cta_arr.id),
            "fecha": date.today().isoformat(),
            "concepto": "Arriendo Bodega Mes",
            "subtotal": "1000000",
            "iva": "190000",
            "retencion_fuente": "35000",
            "retencion_ica": "9660",
            "metodo_pago": "CONTADO",
        }, follow=True)
        self.assertTrue(GastoOperativo.objects.filter(empresa=self.empresa, total_pagar=Decimal("1145340.00")).exists())

        r_cert = client.get(reverse("contabilidad:certificados_retencion"), {
            "tercero": str(self.prov.id), "ano": str(date.today().year), "formato": "pdf",
        })
        self.assertEqual(r_cert.status_code, 200)
        self.assertEqual(r_cert["Content-Type"], "application/pdf")

        r_exo = client.get(reverse("contabilidad:exogena_resumen"), {"ano": str(date.today().year), "export": "xlsx"})
        self.assertEqual(r_exo.status_code, 200)
        self.assertIn("spreadsheetml", r_exo["Content-Type"])

        r_est = client.get(reverse("contabilidad:estados_financieros"), {"export": "xlsx"})
        self.assertEqual(r_est.status_code, 200)
        self.assertIn("spreadsheetml", r_est["Content-Type"])

        # 4. Cierre de período mensual bloquea nuevos asientos en ese mes y Cierre Anual traslada resultados a 360505
        client.post(reverse("contabilidad:cierres_lista"), {
            "accion": "toggle_mes", "ano": str(date.today().year), "mes": str(date.today().month),
        }, follow=True)
        self.assertTrue(PeriodoContableCerrado.objects.filter(empresa=self.empresa, cerrado=True).exists())
        antes_asientos = AsientoContable.objects.filter(empresa=self.empresa).count()
        client.post(reverse("contabilidad:asiento_crear"), {
            "numero_comprobante": "BLQ-1",
            "fecha": date.today().isoformat(),
            "tipo": "GENERAL",
            "concepto": "Bloqueado",
            "cuenta[]": [str(self.cta_caja.id), str(self.cta_vta.id)],
            "tercero[]": ["", ""],
            "descripcion[]": ["D", "C"],
            "debito[]": ["1000", "0"],
            "credito[]": ["0", "1000"],
        }, follow=True)
        self.assertEqual(AsientoContable.objects.filter(empresa=self.empresa).count(), antes_asientos)

        client.post(reverse("contabilidad:cierres_lista"), {
            "accion": "cierre_anual", "ano": str(date.today().year),
        }, follow=True)
        cierre_a = AsientoContable.objects.get(empresa=self.empresa, referencia_origen=f"CIERRE-{date.today().year}")
        self.assertTrue(cierre_a.esta_cuadrado)

    def test_nota_debito_nc_parcial_radian_attached_portal_y_cartera_edades(self):
        client = Client()
        client.force_login(self.admin)

        fac = Factura.objects.create(
            empresa=self.empresa, tipo_documento="FACTURA", prefijo="FE", consecutivo=501,
            tercero=self.cliente, fecha_emision=date.today() - timedelta(days=45),
            fecha_vencimiento=date.today() - timedelta(days=35),
            estado="EMITIDA", metodo_pago="CREDITO", subtotal=Decimal("100000.00"),
            total=Decimal("100000.00"), saldo_pendiente=Decimal("100000.00"),
        )
        fac.generar_cufe()

        # Nota Crédito Parcial (20.000) reduce saldo sin anular factura
        client.post(reverse("facturacion:factura_emitir_nota_credito", args=[fac.id]), {
            "monto_parcial": "20000", "motivo": "Descuento pronto pago",
        }, follow=True)
        fac.refresh_from_db()
        self.assertEqual(fac.estado, "EMITIDA")
        self.assertEqual(fac.saldo_pendiente, Decimal("80000.00"))

        # Nota Débito (10.000) aumenta saldo a 90.000 y genera XML 92
        client.post(reverse("facturacion:factura_emitir_nota_debito", args=[fac.id]), {
            "valor": "10000", "concepto": "Flete adicional",
        }, follow=True)
        fac.refresh_from_db()
        self.assertEqual(fac.saldo_pendiente, Decimal("90000.00"))
        nd = Factura.objects.get(empresa=self.empresa, tipo_documento="NOTA_DEBITO")
        r_nd_xml = client.get(reverse("facturacion:factura_xml", args=[nd.id]))
        self.assertIn(b"DebitNote", r_nd_xml.content)

        # AttachedDocument XML + Evento RADIAN 030 + Cartera por Edades + Portal Público
        r_att = client.get(reverse("facturacion:factura_attached_xml", args=[fac.id]))
        self.assertIn(b"AttachedDocument", r_att.content)

        client.post(reverse("facturacion:factura_registrar_evento_radian", args=[fac.id]), {"codigo": "030"}, follow=True)
        fac.refresh_from_db()
        self.assertTrue(fac.radian_estado.startswith("030"))

        r_ed = client.get(reverse("facturacion:cartera_edades"))
        self.assertEqual(r_ed.status_code, 200)
        self.assertContains(r_ed, "31 - 60")

        r_det = client.get(reverse("facturacion:factura_detalle", args=[fac.id]))
        portal_url = r_det.context["portal_url"]
        anon = Client()
        r_pub = anon.get(portal_url)
        self.assertEqual(r_pub.status_code, 200)
        self.assertContains(r_pub, fac.numero_completo)

    def test_importadores_bodegas_nomina_novedades_pila_pos_mixto_y_backup(self):
        client = Client()
        client.force_login(self.admin)

        # Importar Terceros CSV
        csv_ter = SimpleUploadedFile(
            "terceros.csv",
            b"numero_documento,razon_social,email,es_cliente,es_proveedor\n777888,Importado SAS,imp@siigopro.co,1,0\n",
            content_type="text/csv",
        )
        client.post(reverse("terceros:importar"), {"archivo": csv_ter}, follow=True)
        self.assertTrue(Tercero.objects.filter(empresa=self.empresa, numero_documento="777888").exists())

        # Importar Productos CSV
        csv_prod = SimpleUploadedFile(
            "productos.csv",
            b"codigo,nombre,categoria,precio_venta,costo_unitario,stock_actual\nIMP-1,Taza Ceramica,Hogar,15000,8000,12\n",
            content_type="text/csv",
        )
        client.post(reverse("inventario:productos_importar"), {"archivo": csv_prod}, follow=True)
        self.assertTrue(Producto.objects.filter(empresa=self.empresa, codigo="IMP-1").exists())

        # Importar PUC CSV
        csv_puc = SimpleUploadedFile(
            "puc.csv",
            b"codigo,nombre,naturaleza\n519530,Utiles y Papeleria,DEBITO\n",
            content_type="text/csv",
        )
        client.post(reverse("contabilidad:puc_importar"), {"archivo": csv_puc}, follow=True)
        self.assertTrue(PlanCuenta.objects.filter(empresa=self.empresa, codigo="519530").exists())

        # Multi-bodega y traslado
        client.post(reverse("inventario:bodegas_lista"), {"codigo": "B1", "nombre": "Bodega Norte"}, follow=True)
        client.post(reverse("inventario:bodegas_lista"), {"codigo": "B2", "nombre": "Bodega Sur"}, follow=True)
        b1 = Bodega.objects.get(empresa=self.empresa, codigo="B1")
        b2 = Bodega.objects.get(empresa=self.empresa, codigo="B2")
        client.post(reverse("inventario:bodega_traslado"), {
            "bodega_origen": str(b1.id), "bodega_destino": str(b2.id), "producto": str(self.prod.id), "cantidad": "5",
        }, follow=True)
        self.assertEqual(StockBodega.objects.get(bodega=b2, producto=self.prod).cantidad, Decimal("5.00"))

        # Nómina con novedades, Exoneración Art. 114-1, PILA y Transmisión DIAN
        emp = Empleado.objects.create(
            empresa=self.empresa, numero_documento="1099", nombres="Ana", apellidos="Pérez",
            cargo="Analista", salario_base=Decimal("2000000.00"), fecha_ingreso=date(2025, 1, 10),
        )
        client.post(reverse("nomina:nomina_liquidar"), {
            "periodo_mes": "11", "periodo_ano": "2026",
            f"dias_{emp.id}": "30", f"extras_{emp.id}": "100000", f"bono_{emp.id}": "50000", f"otras_ded_{emp.id}": "20000",
        }, follow=True)
        nom = Nomina.objects.get(empresa=self.empresa, periodo_mes=11, periodo_ano=2026)
        det = NominaEmpleado.objects.get(nomina=nom, empleado=emp)
        self.assertEqual(det.horas_extras, Decimal("100000.00"))
        self.assertEqual(det.salud_empleador_8_5, Decimal("0.00"))  # Exonerado Art. 114-1 (< 10 SMMLV)
        r_pila = client.get(reverse("nomina:nomina_exportar_pila", args=[nom.id]))
        self.assertEqual(r_pila.status_code, 200)
        self.assertIn(b"1099", r_pila.content)
        client.post(reverse("nomina:nomina_transmitir_lote", args=[nom.id]), follow=True)
        nom.refresh_from_db()
        self.assertEqual(nom.estado, "EMITIDA_DIAN")

        # POS pago mixto (Efectivo + Nequi)
        caja = Caja.objects.create(empresa=self.empresa, nombre="Caja Mixta", codigo="CMX")
        turno = CajaTurno.objects.create(empresa=self.empresa, caja=caja, usuario=self.admin, monto_inicial=Decimal("0.00"))
        r_mix = client.post(
            reverse("pos:pos_procesar_venta"),
            data=json.dumps({
                "tercero_id": self.cliente.id,
                "metodo_pago": "EFECTIVO",
                "tipo_documento": "POS_ELECTRONICO",
                "items": [{"producto_id": self.prod.id, "cantidad": 1, "precio": 20000}],
                "pagos_mixtos": [
                    {"metodo": "EFECTIVO", "monto": 12000},
                    {"metodo": "NEQUI", "monto": 8000},
                ],
            }),
            content_type="application/json",
        )
        self.assertEqual(r_mix.status_code, 200)
        turno.refresh_from_db()
        self.assertEqual(turno.monto_ventas_efectivo, Decimal("12000.00"))
        self.assertEqual(turno.monto_ventas_otros, Decimal("8000.00"))

        # Backup comprimido .json.gz
        import tempfile
        with tempfile.TemporaryDirectory() as tmpd:
            out_bk = StringIO()
            call_command("respaldar_bd", destino=tmpd, stdout=out_bk)
            self.assertIn("Respaldo generado: faccol_backup_", out_bk.getvalue())
