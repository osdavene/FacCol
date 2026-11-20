from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse

from apps.empresas.models import Empresa, Modulo, EmpresaModulo
from apps.usuarios.models import User, EmpresaUsuario
from apps.terceros.models import Tercero
from apps.servicios.models import ServicioEquipo, OrdenServicio
from apps.contabilidad.models import AsientoContable


class ServiciosYPermisosModulosTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(razon_social="Taller Tech SAS", nit="900555666", prefijo_factura="FE")
        self.user = User.objects.create_user(
            username="tec@faccol.test", email="tec@faccol.test", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=self.user, empresa=self.empresa, rol="admin_empresa", activo=True)
        self.cliente = Tercero.objects.create(
            empresa=self.empresa, numero_documento="80123456", razon_social="Carlos Gómez", es_cliente=True
        )
        self.equipo = ServicioEquipo.objects.create(
            empresa=self.empresa, cliente=self.cliente, tipo_equipo="Laptop",
            marca="Lenovo", modelo="ThinkPad", serial="SN-001"
        )
        self.orden = OrdenServicio.objects.create(
            empresa=self.empresa, consecutivo=1, cliente=self.cliente, equipo=self.equipo,
            falla_reportada="No enciende", costo_repuestos=Decimal("80000.00"), costo_mano_obra=Decimal("50000.00"),
            estado="LISTO"
        )

    def test_bloqueo_modulos_servicios_e_ia(self):
        mod_fac = Modulo.objects.create(codigo="facturacion", nombre="Facturación")
        mod_serv = Modulo.objects.create(codigo="servicios", nombre="Servicios")
        mod_ia = Modulo.objects.create(codigo="ia", nombre="IA")
        # Solo habilitamos facturación, excluimos servicios e IA
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=mod_fac, activo=True)
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=mod_serv, activo=False)
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=mod_ia, activo=False)

        client = Client()
        client.force_login(self.user)

        r_serv = client.get(reverse("servicios:panel"))
        self.assertEqual(r_serv.status_code, 302)

        r_ia = client.get(reverse("ia:asistente"))
        self.assertEqual(r_ia.status_code, 302)

    def test_orden_servicio_facturar_en_un_clic(self):
        client = Client()
        client.force_login(self.user)

        resp = client.post(reverse("servicios:orden_facturar", args=[self.orden.id]), follow=True)
        self.assertEqual(resp.status_code, 200)

        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, "ENTREGADO")
        self.assertIsNotNone(self.orden.factura)
        self.assertEqual(self.orden.factura.total, Decimal("130000.00"))
        self.assertTrue(bool(self.orden.factura.cufe))

        asiento = AsientoContable.objects.get(empresa=self.empresa, tipo="VENTA")
        self.assertTrue(asiento.esta_cuadrado)

