from decimal import Decimal
from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from apps.empresas.models import Empresa
from apps.usuarios.models import User, EmpresaUsuario
from apps.nomina.models import Empleado, Nomina, NominaEmpleado
from apps.contabilidad.models import AsientoContable
from apps.core.platform_settings import set_platform_setting


class NominaElectronicaTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(razon_social="Nómina SAS", nit="900444555")
        self.user = User.objects.create_user(
            username="nomina@faccol.test", email="nomina@faccol.test", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=self.user, empresa=self.empresa, rol="admin", activo=True)
        self.emp = Empleado.objects.create(
            empresa=self.empresa,
            numero_documento="1012345678",
            nombres="Laura",
            apellidos="Martínez",
            email="laura@empresa.co",
            cargo="Vendedora",
            salario_base=Decimal("1500000.00"),
            tiene_auxilio_transporte=True,
            fecha_ingreso="2026-01-15",
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_liquidar_nomina_genera_asiento_xml_cune_desprendible_y_email(self):
        set_platform_setting("mail_from_address", "notificaciones@servigest.online")
        client = Client()
        client.force_login(self.user)

        r_liq = client.post(reverse("nomina:nomina_liquidar"), {
            "periodo_mes": "11",
            "periodo_ano": "2026",
        }, follow=True)
        self.assertEqual(r_liq.status_code, 200)

        nomina = Nomina.objects.get(empresa=self.empresa, periodo_mes=11, periodo_ano=2026)
        detalle = NominaEmpleado.objects.get(nomina=nomina, empleado=self.emp)

        asiento = AsientoContable.objects.get(empresa=self.empresa, tipo="NOMINA")
        self.assertTrue(asiento.esta_cuadrado)

        r_colilla = client.get(reverse("nomina:nomina_desprendible", args=[detalle.id]))
        self.assertEqual(r_colilla.status_code, 200)
        self.assertContains(r_colilla, "CUNE DIAN")

        r_xml = client.get(reverse("nomina:nomina_xml_empleado", args=[detalle.id]))
        self.assertEqual(r_xml.status_code, 200)
        self.assertIn(b"<NominaIndividual", r_xml.content)
        self.assertIn(b'CUNE="', r_xml.content)

        r_mail = client.post(reverse("nomina:nomina_enviar_colilla", args=[detalle.id]), follow=True)
        self.assertEqual(r_mail.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["laura@empresa.co"])

