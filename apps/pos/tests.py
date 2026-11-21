import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from apps.usuarios.models import User, EmpresaUsuario
from apps.empresas.models import Empresa, Modulo, Plan, EmpresaModulo
from apps.terceros.models import Tercero
from apps.inventario.models import Producto
from apps.pos.models import Caja, CajaTurno
from apps.facturacion.models import Factura
from apps.facturacion.dian_service import generar_xml_ubl


class TiendaBarrioPosYDianTests(TestCase):
    def setUp(self):
        self.mod_pos = Modulo.objects.create(codigo="pos", nombre="POS", activo_por_defecto=True)
        self.mod_fac = Modulo.objects.create(codigo="facturacion", nombre="Facturación", activo_por_defecto=True)
        self.mod_cont = Modulo.objects.create(codigo="contabilidad", nombre="Contabilidad", activo_por_defecto=False)
        self.plan_tienda = Plan.objects.create(
            nombre="Plan Tienda Express", precio_mensual=29900, limite_facturas_mes=2, limite_usuarios=2
        )
        self.plan_tienda.modulos.add(self.mod_pos, self.mod_fac)

        self.tienda = Empresa.objects.create(
            razon_social="Tienda La Prosperidad",
            nit="900444555",
            regimen_tributario="NO_RESPONSABLE_IVA",
            perfil_operativo="TIENDA",
            prefijo_pos_electronico="POS",
            plan=self.plan_tienda,
        )
        EmpresaModulo.objects.create(empresa=self.tienda, modulo=self.mod_pos, activo=True)
        EmpresaModulo.objects.create(empresa=self.tienda, modulo=self.mod_fac, activo=True)

        self.cajero = User.objects.create_user(
            username="tendero@tienda.co", email="tendero@tienda.co", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=self.cajero, empresa=self.tienda, rol="propietario", activo=True)

        self.caja = Caja.objects.create(empresa=self.tienda, nombre="Caja Mostrador", codigo="C1")
        self.turno = CajaTurno.objects.create(empresa=self.tienda, caja=self.caja, usuario=self.cajero, monto_inicial=50000)

        self.gaseosa = Producto.objects.create(
            empresa=self.tienda, codigo="GAS-01", codigo_barras="7701234567890",
            nombre="Gaseosa 400ml", precio_venta=Decimal("3300.00"),
            tarifa_iva=Decimal("19.00"), impuesto_saludable_pct=Decimal("10.00"),
            precio_incluye_impuestos=True, es_favorito_pos=True, stock_actual=Decimal("24.00"),
        )
        self.queso = Producto.objects.create(
            empresa=self.tienda, codigo="QUE-01", nombre="Queso Campesino (lb)",
            precio_venta=Decimal("8000.00"), tarifa_iva=Decimal("0.00"),
            precio_incluye_impuestos=True, permite_fraccion=True, stock_actual=Decimal("10.00"),
        )
        self.vecino = Tercero.objects.create(
            empresa=self.tienda, numero_documento="79123456", razon_social="Don Carlos Vecino",
            telefono="3001112233", es_cliente=True,
        )

    def test_pos_crea_consumidor_final_emite_dee_pos_xml_y_tirilla(self):
        client = Client()
        client.force_login(self.cajero)

        # 1. Al abrir terminal POS se autocrea Consumidor Final y no aparece Contabilidad en menú
        resp_pos = client.get(reverse("pos:pos_terminal"))
        self.assertEqual(resp_pos.status_code, 200)
        cf = Tercero.objects.get(empresa=self.tienda, numero_documento="222222222222")
        self.assertNotContains(resp_pos, "Libro Diario (Asientos)")

        # Contabilidad bloqueada por plan
        resp_cont = client.get(reverse("contabilidad:asientos_lista"))
        self.assertEqual(resp_cont.status_code, 302)

        # 2. Venta menudeo fraccionado + impuesto saludable (No Responsable de IVA -> IVA 0%, IBUA 10% incluido)
        resp_venta = client.post(
            reverse("pos:pos_procesar_venta"),
            data=json.dumps({
                "tercero_id": cf.id,
                "tipo_documento": "POS_ELECTRONICO",
                "metodo_pago": "NEQUI",
                "items": [
                    {"producto_id": self.gaseosa.id, "cantidad": 1, "precio": 3300},
                    {"producto_id": self.queso.id, "cantidad": 0.5, "precio": 8000},
                ],
            }),
            content_type="application/json",
        )
        self.assertEqual(resp_venta.status_code, 200)
        payload = resp_venta.json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["numero"].startswith("POS"))

        fac = Factura.objects.get(id=payload["factura_id"])
        self.assertEqual(fac.tipo_documento, "POS_ELECTRONICO")
        self.assertEqual(fac.iva, Decimal("0.00"))
        self.assertEqual(fac.impuestos_adicionales, Decimal("300.00"))  # 3000 base + 10% IBUA = 3300
        self.assertEqual(fac.total, Decimal("7300.00"))  # 3300 + 4000
        self.assertEqual(len(fac.cufe), 96)

        xml_str = generar_xml_ubl(fac)
        self.assertIn("<cbc:InvoiceTypeCode>20</cbc:InvoiceTypeCode>", xml_str)

        # 3. Tirilla HTML y PDF térmico 58mm
        t_html = client.get(reverse("facturacion:factura_tirilla", args=[fac.id]) + "?ancho=58")
        self.assertEqual(t_html.status_code, 200)
        self.assertContains(t_html, "DOC. EQUIVALENTE")
        t_pdf = client.get(reverse("facturacion:factura_tirilla", args=[fac.id]) + "?format=pdf&ancho=58")
        self.assertEqual(t_pdf.status_code, 200)
        self.assertEqual(t_pdf["Content-Type"], "application/pdf")

    def test_fiado_vecino_y_limite_mensual_plan(self):
        client = Client()
        client.force_login(self.cajero)
        client.get(reverse("pos:pos_terminal"))
        cf = Tercero.objects.get(empresa=self.tienda, numero_documento="222222222222")

        # No permite fiado a Consumidor Final
        r_bad = client.post(
            reverse("pos:pos_procesar_venta"),
            data=json.dumps({
                "tercero_id": cf.id,
                "metodo_pago": "FIADO",
                "items": [{"producto_id": self.queso.id, "cantidad": 1, "precio": 8000}],
            }),
            content_type="application/json",
        )
        self.assertEqual(r_bad.status_code, 400)

        # Permite fiado al vecino Don Carlos
        r_ok = client.post(
            reverse("pos:pos_procesar_venta"),
            data=json.dumps({
                "tercero_id": self.vecino.id,
                "metodo_pago": "FIADO",
                "items": [{"producto_id": self.queso.id, "cantidad": 1, "precio": 8000}],
            }),
            content_type="application/json",
        )
        self.assertEqual(r_ok.status_code, 200)
        fac = Factura.objects.get(id=r_ok.json()["factura_id"])
        self.assertTrue(fac.es_fiado)
        self.assertEqual(fac.saldo_pendiente, Decimal("8000.00"))

        r_fiados = client.get(reverse("facturacion:fiados_lista"))
        self.assertEqual(r_fiados.status_code, 200)
        self.assertContains(r_fiados, "Don Carlos Vecino")

        # Segundo documento permitido (límite = 2), tercero bloqueado (403)
        r2 = client.post(
            reverse("pos:pos_procesar_venta"),
            data=json.dumps({
                "tercero_id": cf.id,
                "metodo_pago": "EFECTIVO",
                "items": [{"producto_id": self.queso.id, "cantidad": 1, "precio": 8000}],
            }),
            content_type="application/json",
        )
        self.assertEqual(r2.status_code, 200)

        r3 = client.post(
            reverse("pos:pos_procesar_venta"),
            data=json.dumps({
                "tercero_id": cf.id,
                "metodo_pago": "EFECTIVO",
                "items": [{"producto_id": self.queso.id, "cantidad": 1, "precio": 8000}],
            }),
            content_type="application/json",
        )
        self.assertEqual(r3.status_code, 403)

    def test_pos_bloquea_dee_pos_mayor_a_5_uvt_y_dashboard_incluye_pos(self):
        client = Client()
        client.force_login(self.cajero)

        # Venta POS válida ($8,000 fiado + contado) debe reflejarse en métricas del Dashboard
        r_venta = client.post(
            reverse("pos:pos_procesar_venta"),
            data=json.dumps({
                "tercero_id": self.vecino.id,
                "tipo_documento": "POS_ELECTRONICO",
                "metodo_pago": "FIADO",
                "items": [{"producto_id": self.queso.id, "cantidad": 1, "precio": 8000}],
            }),
            content_type="application/json",
        )
        self.assertEqual(r_venta.status_code, 200)
        r_dash = client.get(reverse("core:dashboard"))
        self.assertEqual(r_dash.status_code, 200)
        self.assertEqual(r_dash.context["ventas_mes"], Decimal("8000.00"))
        self.assertEqual(r_dash.context["cartera_pendiente"], Decimal("8000.00"))

        # Venta POS_ELECTRONICO superior a 5 UVT (~$250,000 COP) debe exigir Factura Electrónica
        r_tope = client.post(
            reverse("pos:pos_procesar_venta"),
            data=json.dumps({
                "tercero_id": self.vecino.id,
                "tipo_documento": "POS_ELECTRONICO",
                "metodo_pago": "EFECTIVO",
                "items": [{"producto_id": self.queso.id, "cantidad": 40, "precio": 8000}],
            }),
            content_type="application/json",
        )
        self.assertEqual(r_tope.status_code, 400)
        self.assertIn("5 UVT", r_tope.json()["error"])
