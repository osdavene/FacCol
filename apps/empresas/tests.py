from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.contabilidad.models import AsientoContable
from apps.core.platform_settings import set_platform_setting
from apps.empresas.models import Empresa
from apps.facturacion.models import Factura
from apps.inventario.models import CompraProveedor, Producto
from apps.nomina.models import Nomina
from apps.servicios.models import OrdenServicio
from apps.terceros.models import Tercero
from apps.usuarios.models import User


class SeedEmpresaPruebasTests(TestCase):
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_crear_empresa_pruebas_inyecta_todos_los_modulos_y_es_idempotente(self):
        set_platform_setting("mail_from_address", "notificaciones@servigest.online")
        User.objects.create_user(
            username="sa_test@faccol.test", email="sa_test@faccol.test", password="SafePass!2026",
            is_superadmin=True, is_superuser=True, is_staff=True,
        )
        call_command("seed_base")
        call_command("crear_empresa_pruebas", "--notificar-superadmin")

        emp = Empresa.objects.get(nit="901777999")
        self.assertEqual(Producto.objects.filter(empresa=emp).count(), 10)
        self.assertGreaterEqual(Tercero.objects.filter(empresa=emp).count(), 8)
        self.assertEqual(CompraProveedor.objects.filter(empresa=emp).count(), 2)
        self.assertGreaterEqual(Factura.objects.filter(empresa=emp).count(), 8)
        self.assertEqual(OrdenServicio.objects.filter(empresa=emp).count(), 4)
        self.assertEqual(Nomina.objects.filter(empresa=emp).count(), 1)

        asientos = AsientoContable.objects.filter(empresa=emp)
        self.assertGreaterEqual(asientos.count(), 6)
        for asiento in asientos:
            self.assertTrue(asiento.esta_cuadrado)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["sa_test@faccol.test"])
        self.assertIn("pruebas.empresa@faccol.online", mail.outbox[0].alternatives[0][0])

        # Idempotencia en segunda ejecución
        call_command("crear_empresa_pruebas", "--notificar-superadmin")
        self.assertEqual(Empresa.objects.filter(nit="901777999").count(), 1)
        self.assertEqual(len(mail.outbox), 1)
