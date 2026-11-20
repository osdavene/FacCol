from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from apps.usuarios.models import User, EmpresaUsuario
from apps.empresas.models import Empresa
from apps.terceros.models import Tercero
from apps.inventario.models import Producto, MovimientoInventario
from apps.facturacion.models import Factura


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
