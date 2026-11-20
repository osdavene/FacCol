import hashlib
import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from apps.usuarios.models import User, EmpresaUsuario
from apps.empresas.models import Empresa
from apps.terceros.models import Tercero
from apps.inventario.models import Producto, MovimientoInventario
from apps.facturacion.models import Factura, FacturaItem, ReciboCaja, TransaccionWompi
from apps.core.platform_settings import set_platform_setting


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
        set_platform_setting("wompi_active_flag", "1")
        set_platform_setting("wompi_public_key", "pub_test_12345")
        set_platform_setting("wompi_integrity_secret", "test_integ_secret", is_secret=True)
        set_platform_setting("wompi_events_secret", "test_events_secret", is_secret=True)

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

        # Firma válida SHA-256 debe aprobar, crear ReciboCaja y marcar factura PAGADA (idempotente)
        raw = f"wompi-tx-1001APPROVED50000001732140000test_events_secret"
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

