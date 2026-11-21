import hashlib
import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from apps.usuarios.models import User, EmpresaUsuario
from apps.empresas.models import Empresa, Plan, Modulo
from apps.terceros.models import Tercero
from apps.inventario.models import Producto, MovimientoInventario
from apps.facturacion.models import Factura, FacturaItem, ReciboCaja, TransaccionWompi
from apps.facturacion.wompi_service import get_wompi_runtime_config, get_platform_wompi_config
from apps.contabilidad.models import AsientoContable
from apps.pos.models import Caja, CajaTurno
from apps.core.platform_settings import encrypt_secret, set_platform_setting


class FacturacionEmisionTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(razon_social="Comercial SAS", nit="900123123", prefijo_factura="FE")
        self.vendedor = User.objects.create_user(
            username="vend@faccol.test", email="vend@faccol.test", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=self.vendedor, empresa=self.empresa, rol="vendedor", activo=True)
        self.cliente = Tercero.objects.create(
            empresa=self.empresa, numero_documento="222222222222", razon_social="Consumidor Final", es_cliente=True
        )
        self.prod = Producto.objects.create(
            empresa=self.empresa, codigo="ARR-01", nombre="Arroz 500g",
            precio_venta=Decimal("2500.00"), tarifa_iva=Decimal("0.00"),
            stock_actual=Decimal("20.00"), maneja_inventario=True
        )

    def test_emitir_factura_descuenta_stock_y_genera_pdf(self):
        client = Client()
        client.force_login(self.vendedor)

        resp = client.post(reverse("facturacion:factura_crear"), {
            "tercero": str(self.cliente.id),
            "tipo_documento": "FACTURA",
            "metodo_pago": "CONTADO",
            "item_producto[]": [str(self.prod.id)],
            "item_cantidad[]": ["3"],
            "item_precio[]": ["2500"],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Factura.objects.count(), 1)
        factura = Factura.objects.first()
        self.assertEqual(factura.total, Decimal("7500.00"))
        self.assertTrue(bool(factura.cufe))

        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock_actual, Decimal("17.00"))
        self.assertEqual(MovimientoInventario.objects.count(), 1)

        pdf_resp = client.get(reverse("facturacion:factura_pdf", args=[factura.id]))
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertEqual(pdf_resp["Content-Type"], "application/pdf")

    def test_wompi_checkout_y_webhook_idempotente(self):
        # Cada empresa inquilina configura SUS PROPIAS llaves Wompi
        self.empresa.wompi_public_key = "pub_test_empresa_12345"
        self.empresa.wompi_integrity_secret = encrypt_secret("emp_integ_secret")
        self.empresa.wompi_events_secret = encrypt_secret("emp_events_secret")
        self.empresa.save()

        factura = Factura.objects.create(
            empresa=self.empresa, tipo_documento="FACTURA", prefijo="FE", consecutivo=99,
            tercero=self.cliente, fecha_emision="2026-11-20", fecha_vencimiento="2026-11-20",
            estado="EMITIDA", metodo_pago="CREDITO",
        )
        FacturaItem.objects.create(
            factura=factura, producto=self.prod, descripcion="Arroz bulto",
            cantidad=Decimal("1"), precio_unitario=Decimal("50000.00"), porcentaje_iva=Decimal("0.00"),
        )
        factura.recalcular_totales()
        client = Client()
        client.force_login(self.vendedor)

        r_init = client.post(reverse("facturacion:wompi_iniciar", args=[factura.id]))
        self.assertEqual(r_init.status_code, 302)
        self.assertIn("https://checkout.wompi.co/p/", r_init["Location"])
        tx = TransaccionWompi.objects.get(factura=factura)
        self.assertEqual(tx.amount_in_cents, 5000000)

        # Firma inválida debe retornar 401
        payload = {
            "event": "transaction.updated",
            "environment": "test",
            "timestamp": 1732140000,
            "data": {
                "transaction": {
                    "id": "wompi-tx-1001",
                    "reference": tx.reference,
                    "status": "APPROVED",
                    "amount_in_cents": 5000000,
                    "currency": "COP",
                    "payment_method_type": "NEQUI",
                }
            },
            "signature": {
                "properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
                "checksum": "invalid_checksum",
            },
        }
        r_bad = client.post(reverse("facturacion:wompi_webhook"), data=json.dumps(payload), content_type="application/json")
        self.assertEqual(r_bad.status_code, 401)

        # Firma válida SHA-256 usando el events_secret PROPIO de la empresa
        raw = "wompi-tx-1001APPROVED50000001732140000emp_events_secret"
        payload["signature"]["checksum"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        r_ok = client.post(reverse("facturacion:wompi_webhook"), data=json.dumps(payload), content_type="application/json")
        self.assertEqual(r_ok.status_code, 200)

        factura.refresh_from_db()
        self.assertEqual(factura.estado, "PAGADA")
        self.assertEqual(factura.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(ReciboCaja.objects.filter(factura=factura, metodo_pago="WOMPI").count(), 1)

        # Reenvío duplicado del webhook no debe duplicar ReciboCaja
        client.post(reverse("facturacion:wompi_webhook"), data=json.dumps(payload), content_type="application/json")
        self.assertEqual(ReciboCaja.objects.filter(factura=factura).count(), 1)

    def test_cotizacion_convertir_a_factura_y_nota_credito_con_reversion(self):
        client = Client()
        client.force_login(self.vendedor)

        # 1. Crear Cotización: no descuenta stock ni genera asiento ni cartera
        resp_cot = client.post(reverse("facturacion:factura_crear"), {
            "tercero": str(self.cliente.id),
            "tipo_documento": "COTIZACION",
            "metodo_pago": "CREDITO",
            "item_producto[]": [str(self.prod.id)],
            "item_cantidad[]": ["4"],
            "item_precio[]": ["2500"],
        }, follow=True)
        self.assertEqual(resp_cot.status_code, 200)
        cot = Factura.objects.get(tipo_documento="COTIZACION")
        self.assertEqual(cot.numero_completo, "COT1")
        self.assertEqual(cot.total, Decimal("10000.00"))
        self.assertEqual(cot.saldo_pendiente, Decimal("0.00"))
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock_actual, Decimal("20.00"))
        self.assertEqual(AsientoContable.objects.filter(empresa=self.empresa).count(), 0)

        # 2. Convertir Cotización a Factura: descuenta stock, genera CUFE y asiento contable cuadrado
        resp_conv = client.post(reverse("facturacion:cotizacion_convertir_factura", args=[cot.id]), follow=True)
        self.assertEqual(resp_conv.status_code, 200)
        cot.refresh_from_db()
        fac = cot.factura_referencia
        self.assertIsNotNone(fac)
        self.assertEqual(fac.tipo_documento, "FACTURA")
        self.assertEqual(fac.total, Decimal("10000.00"))
        self.assertTrue(bool(fac.cufe))

        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock_actual, Decimal("16.00"))
        asiento_vta = AsientoContable.objects.get(empresa=self.empresa, tipo="VENTA")
        self.assertTrue(asiento_vta.esta_cuadrado)

        # 3. Emitir Nota Crédito: anula factura, limpia saldo, reversa inventario y crea asiento
        resp_nc = client.post(reverse("facturacion:factura_emitir_nota_credito", args=[fac.id]), {
            "motivo": "Devolución total prueba",
        }, follow=True)
        self.assertEqual(resp_nc.status_code, 200)

        fac.refresh_from_db()
        self.assertEqual(fac.estado, "ANULADA")
        self.assertEqual(fac.saldo_pendiente, Decimal("0.00"))

        nc = Factura.objects.get(tipo_documento="NOTA_CREDITO", factura_referencia=fac)
        self.assertEqual(nc.numero_completo, "NC1")
        self.assertTrue(bool(nc.cufe))

        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock_actual, Decimal("20.00"))
        self.assertTrue(MovimientoInventario.objects.filter(producto=self.prod, tipo="ENTRADA").exists())

        asiento_nc = AsientoContable.objects.get(empresa=self.empresa, numero_comprobante="NC-1")
        self.assertTrue(asiento_nc.esta_cuadrado)

        # XML UBL de la Nota Crédito debe usar raíz CreditNote y código 91 DIAN 2.1
        xml_resp = client.get(reverse("facturacion:factura_xml", args=[nc.id]))
        self.assertEqual(xml_resp.status_code, 200)
        self.assertIn(b"<cbc:CreditNoteTypeCode>91</cbc:CreditNoteTypeCode>", xml_resp.content)
        self.assertIn(b"<cac:DiscrepancyResponse>", xml_resp.content)

    def test_recibo_caja_y_pos_generan_asiento_contable(self):
        caja = Caja.objects.create(empresa=self.empresa, nombre="Caja Principal")
        CajaTurno.objects.create(empresa=self.empresa, caja=caja, usuario=self.vendedor, monto_inicial=Decimal("0.00"))

        client = Client()
        client.force_login(self.vendedor)
        r_pos = client.post(
            reverse("pos:pos_procesar_venta"),
            data=json.dumps({
                "tercero_id": self.cliente.id,
                "metodo_pago": "EFECTIVO",
                "tipo_documento": "POS_ELECTRONICO",
                "items": [{"producto_id": self.prod.id, "cantidad": 2, "precio": 2500}],
            }),
            content_type="application/json",
        )
        self.assertEqual(r_pos.status_code, 200)
        asientos_pos = AsientoContable.objects.filter(empresa=self.empresa, tipo="VENTA")
        self.assertEqual(asientos_pos.count(), 1)
        self.assertTrue(asientos_pos.first().esta_cuadrado)

        # Venta a crédito y cobro mediante ReciboCaja debe generar asiento VENTA + INGRESO
        fac_cred = Factura.objects.create(
            empresa=self.empresa, tipo_documento="FACTURA", prefijo="FE", consecutivo=50,
            tercero=self.cliente, fecha_emision="2026-11-20", fecha_vencimiento="2026-11-30",
            estado="EMITIDA", metodo_pago="CREDITO",
        )
        FacturaItem.objects.create(
            factura=fac_cred, producto=self.prod, descripcion="Arroz 500g",
            cantidad=Decimal("2"), precio_unitario=Decimal("2500.00"), porcentaje_iva=Decimal("0.00"),
        )
        fac_cred.recalcular_totales()

        r_rc = client.post(reverse("facturacion:recibo_crear"), {
            "factura": str(fac_cred.id),
            "valor": "5000",
            "metodo_pago": "NEQUI",
        }, follow=True)
        self.assertEqual(r_rc.status_code, 200)
        asiento_ing = AsientoContable.objects.get(empresa=self.empresa, tipo="INGRESO")
        self.assertTrue(asiento_ing.esta_cuadrado)

    def test_wompi_empresa_aislado_y_suscripcion_landing_plataforma(self):
        # Plataforma SI tiene Wompi activo, pero self.empresa NO tiene Wompi propio configurado
        set_platform_setting("wompi_active_flag", "1")
        set_platform_setting("wompi_public_key", "pub_test_plataforma")
        set_platform_setting("wompi_integrity_secret", "plat_integ_secret", is_secret=True)
        set_platform_setting("wompi_events_secret", "plat_events_secret", is_secret=True)

        self.assertTrue(get_platform_wompi_config()["enabled"])
        self.assertFalse(get_wompi_runtime_config(self.empresa)["enabled"])

        # Una factura de self.empresa NO debe poder iniciar Wompi usando el Wompi de la plataforma
        fac = Factura.objects.create(
            empresa=self.empresa, tipo_documento="FACTURA", prefijo="FE", consecutivo=88,
            tercero=self.cliente, fecha_emision="2026-11-21", fecha_vencimiento="2026-11-21",
            estado="EMITIDA", metodo_pago="CREDITO",
        )
        FacturaItem.objects.create(
            factura=fac, producto=self.prod, descripcion="Arroz",
            cantidad=Decimal("1"), precio_unitario=Decimal("2500.00"), porcentaje_iva=Decimal("0.00"),
        )
        fac.recalcular_totales()

        client = Client()
        client.force_login(self.vendedor)
        r_block = client.post(reverse("facturacion:wompi_iniciar", args=[fac.id]))
        self.assertEqual(r_block.status_code, 302)
        self.assertNotIn("checkout.wompi.co", r_block["Location"])
        self.assertEqual(TransaccionWompi.objects.filter(factura=fac).count(), 0)

        # Visitante anónimo en página principal ve planes y paga suscripción al Wompi de la Plataforma
        mod_fac = Modulo.objects.create(codigo="facturacion", nombre="Facturación")
        plan = Plan.objects.create(nombre="Plan Tienda Test", precio_mensual=Decimal("59900.00"), activo=True)
        plan.modulos.add(mod_fac)

        anon = Client()
        r_land = anon.get(reverse("core:dashboard"))
        self.assertEqual(r_land.status_code, 200)
        self.assertContains(r_land, "Plan Tienda Test")

        r_sub = anon.post(reverse("core:suscripcion_wompi_iniciar"), {
            "plan_id": str(plan.id),
            "razon_social": "Minimercado Nuevo SAS",
            "nit": "901888777",
            "email": "dueno@minimercado.co",
        })
        self.assertEqual(r_sub.status_code, 302)
        self.assertIn("https://checkout.wompi.co/p/", r_sub["Location"])

        tx_plan = TransaccionWompi.objects.get(plan=plan, empresa__isnull=True)
        payload = {
            "event": "transaction.updated",
            "environment": "test",
            "timestamp": 1732150000,
            "data": {
                "transaction": {
                    "id": "wompi-plan-777",
                    "reference": tx_plan.reference,
                    "status": "APPROVED",
                    "amount_in_cents": 5990000,
                    "currency": "COP",
                    "payment_method_type": "PSE",
                }
            },
            "signature": {
                "properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
                "checksum": hashlib.sha256(b"wompi-plan-777APPROVED59900001732150000plat_events_secret").hexdigest(),
            },
        }
        r_wh = anon.post(reverse("facturacion:wompi_webhook"), data=json.dumps(payload), content_type="application/json")
        self.assertEqual(r_wh.status_code, 200)
        tx_plan.refresh_from_db()
        self.assertEqual(tx_plan.status, "APPROVED")
        self.assertIsNotNone(tx_plan.empresa_creada)
        self.assertEqual(tx_plan.empresa_creada.plan_id, plan.id)
        self.assertTrue(User.objects.filter(email="dueno@minimercado.co").exists())

    def test_cola_dian_y_configuracion_empresa(self):
        admin_emp = User.objects.create_user(
            username="admin_cfg@faccol.test", email="admin_cfg@faccol.test", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=admin_emp, empresa=self.empresa, rol="admin", activo=True)

        client = Client()
        client.force_login(admin_emp)

        # Emite factura sin credenciales DIAN -> queda en CONTINGENCIA
        client.post(reverse("facturacion:factura_crear"), {
            "tercero": str(self.cliente.id),
            "tipo_documento": "FACTURA",
            "metodo_pago": "CONTADO",
            "item_producto[]": [str(self.prod.id)],
            "item_cantidad[]": ["1"],
            "item_precio[]": ["2500"],
        }, follow=True)
        fac = Factura.objects.get(empresa=self.empresa, tipo_documento="FACTURA")
        self.assertEqual(fac.estado_dian, "CONTINGENCIA")

        # El propio admin de empresa configura sus credenciales DIAN y su propio Wompi
        r_cfg = client.post(reverse("core:configuracion_empresa"), {
            "seccion": "dian",
            "perfil_operativo": "TIENDA",
            "regimen_tributario": "NO_RESPONSABLE_IVA",
            "prefijo_factura": "FE",
            "prefijo_pos_electronico": "POS",
            "resolucion_dian": "18764000112233",
            "modo_dian": "PRUEBAS",
            "dian_software_id": "SW-UUID-1234",
            "dian_pin": "12345",
            "dian_test_set_id": "TEST-SET-999",
        }, follow=True)
        self.assertEqual(r_cfg.status_code, 200)

        r_lote = client.post(reverse("facturacion:dian_procesar_lote"), follow=True)
        self.assertEqual(r_lote.status_code, 200)
        fac.refresh_from_db()
        self.assertEqual(fac.estado_dian, "ENVIADA")
        self.assertTrue(fac.dian_track_id.startswith("DIAN-HAB-"))

    def test_retenciones_automaticas_tercero_y_asiento_contable(self):
        cliente_ret = Tercero.objects.create(
            empresa=self.empresa, numero_documento="901777888", razon_social="Cliente Retenedor SAS",
            es_cliente=True, aplica_retefuente=True, tarifa_retefuente=Decimal("2.50"),
        )
        client = Client()
        client.force_login(self.vendedor)

        r = client.post(reverse("facturacion:factura_crear"), {
            "tercero": str(cliente_ret.id),
            "tipo_documento": "FACTURA",
            "metodo_pago": "CREDITO",
            "item_producto[]": [str(self.prod.id)],
            "item_cantidad[]": ["10"],
            "item_precio[]": ["10000"],
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        fac = Factura.objects.get(empresa=self.empresa, tercero=cliente_ret)
        self.assertEqual(fac.subtotal, Decimal("100000.00"))
        self.assertEqual(fac.retenciones, Decimal("2500.00"))
        self.assertEqual(fac.total, Decimal("97500.00"))

        asiento = AsientoContable.objects.get(empresa=self.empresa, referencia_origen=fac.numero_completo)
        self.assertTrue(asiento.esta_cuadrado)
        self.assertEqual(asiento.total_debito, Decimal("100000.00"))



