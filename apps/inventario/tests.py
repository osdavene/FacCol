from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse

from apps.empresas.models import Empresa
from apps.usuarios.models import User, EmpresaUsuario
from apps.terceros.models import Tercero
from apps.inventario.models import Producto, MovimientoInventario, CompraProveedor
from apps.contabilidad.models import AsientoContable


class InventarioComprasYDseTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(razon_social="Bodega SAS", nit="900321321")
        self.user = User.objects.create_user(
            username="inv@faccol.test", email="inv@faccol.test", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=self.user, empresa=self.empresa, rol="admin", activo=True)
        self.prov = Tercero.objects.create(
            empresa=self.empresa, numero_documento="70123456", razon_social="Proveedor Rural", es_proveedor=True
        )
        self.prod = Producto.objects.create(
            empresa=self.empresa, codigo="GASEOSA-P", nombre="Gaseosa Personal",
            precio_venta=Decimal("3000.00"), costo_unitario=Decimal("1500.00"),
            factor_conversion=Decimal("12.000"), stock_actual=Decimal("0.00"),
        )

    def test_compra_con_paca_documento_soporte_y_ajuste_kardex(self):
        client = Client()
        client.force_login(self.user)

        r_compra = client.post(reverse("inventario:compra_crear"), {
            "proveedor": str(self.prov.id),
            "numero_factura_proveedor": "DOC-01",
            "metodo_pago": "CONTADO",
            "es_documento_soporte": "on",
            "item_producto[]": [str(self.prod.id)],
            "item_cantidad[]": ["2"],
            "item_factor[]": ["12"],
            "item_costo[]": ["24000"],
        }, follow=True)
        self.assertEqual(r_compra.status_code, 200)

        compra = CompraProveedor.objects.get(empresa=self.empresa)
        self.assertEqual(compra.numero_completo, "DSE1")
        self.assertEqual(compra.total, Decimal("48000.00"))
        self.assertTrue(bool(compra.cuds))

        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock_actual, Decimal("24.00"))
        self.assertEqual(self.prod.costo_unitario, Decimal("2000.00"))

        asiento = AsientoContable.objects.get(empresa=self.empresa, tipo="COMPRA")
        self.assertTrue(asiento.esta_cuadrado)

        r_xml = client.get(reverse("inventario:compra_xml_dse", args=[compra.id]))
        self.assertEqual(r_xml.status_code, 200)
        self.assertIn(b"<cbc:InvoiceTypeCode>05</cbc:InvoiceTypeCode>", r_xml.content)

        # Ajuste manual de merma (-4 unidades)
        r_aj = client.post(reverse("inventario:ajuste_crear"), {
            "producto": str(self.prod.id),
            "tipo": "SALIDA",
            "cantidad": "4",
            "motivo": "Merma / rotura en estantería",
        }, follow=True)
        self.assertEqual(r_aj.status_code, 200)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock_actual, Decimal("20.00"))
        self.assertTrue(MovimientoInventario.objects.filter(producto=self.prod, tipo="SALIDA").exists())

