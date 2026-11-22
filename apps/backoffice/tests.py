from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from apps.core.models import RegistroActividad
from apps.usuarios.models import User, EmpresaUsuario
from apps.empresas.models import Empresa, Modulo, Plan


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class BackofficePlanesYEmpresasTests(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            username="sa_bo@faccol.test", email="sa_bo@faccol.test", password="SafePass!2026",
            is_superadmin=True, is_superuser=True, is_staff=True
        )
        self.mod = Modulo.objects.create(codigo="facturacion", nombre="Facturación", activo_por_defecto=True)
        self.plan = Plan.objects.create(nombre="Plan Tienda", precio_mensual=49000)
        self.plan.modulos.add(self.mod)

    def test_crear_empresa_genera_clave_aleatoria_sin_hardcode(self):
        client = Client()
        client.force_login(self.superadmin)
        mail.outbox.clear()
        resp = client.post(reverse("backoffice:empresa_crear"), {
            "razon_social": "Tienda La Esquina",
            "nit": "1098765432",
            "email": "dueno@esquina.co",
            "plan": str(self.plan.id),
            "admin_tipo": "nuevo_cliente",
            "admin_email": "dueno@esquina.co",
            "admin_nombre": "Don Pedro",
            "admin_password": "",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Clave temporal")
        emp = Empresa.objects.get(nit="1098765432")
        self.assertTrue(emp.modulos.filter(codigo="facturacion").exists())
        usuario = User.objects.get(email="dueno@esquina.co")
        self.assertFalse(usuario.check_password("Clave1234*"))
        self.assertTrue(any(m.to == ["dueno@esquina.co"] and "/auth/password-reset/" in m.body for m in mail.outbox))

        r_form = client.get(reverse("backoffice:empresa_crear"))
        self.assertEqual(r_form.status_code, 200)
        self.assertNotContains(r_form, "wompi_public_key")
        self.assertNotContains(r_form, "Pasarela Wompi")
        self.assertNotContains(r_form, "smtp_host")

    def test_crear_empresa_copia_correo_empresa_y_respeta_clave_existente(self):
        client = Client()
        client.force_login(self.superadmin)
        mail.outbox.clear()

        resp = client.post(reverse("backoffice:empresa_crear"), {
            "razon_social": "Norte Logística SAS",
            "nit": "901444555",
            "email": "oficial@nortelog.co",
            "plan": str(self.plan.id),
            "admin_tipo": "nuevo_cliente",
            "admin_email": "socio@nortelog.co",
            "admin_nombre": "Carlos Socio",
            "admin_password": "",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        dests_primera = [addr for m in mail.outbox for addr in m.to]
        self.assertIn("socio@nortelog.co", dests_primera)
        self.assertIn("oficial@nortelog.co", dests_primera)
        self.assertTrue(any("/auth/password-reset/" in m.body for m in mail.outbox if m.to == ["socio@nortelog.co"]))

        # Fijar clave conocida al usuario y verificar que asignarlo a 2da empresa NO la sobrescribe
        socio = User.objects.get(email="socio@nortelog.co")
        socio.set_password("MiClaveSecreta!2026")
        socio.save()
        mail.outbox.clear()

        resp2 = client.post(reverse("backoffice:empresa_crear"), {
            "razon_social": "Norte Logística Sucursal 2",
            "nit": "901444556",
            "email": "sede2@nortelog.co",
            "plan": str(self.plan.id),
            "admin_tipo": "nuevo_cliente",
            "admin_email": "socio@nortelog.co",
            "admin_nombre": "Carlos Socio",
            "admin_password": "ClaveQueNoDebeSobrescribir",
        }, follow=True)
        self.assertEqual(resp2.status_code, 200)
        socio.refresh_from_db()
        self.assertTrue(socio.check_password("MiClaveSecreta!2026"))
        emp2 = Empresa.objects.get(nit="901444556")
        self.assertTrue(EmpresaUsuario.objects.filter(user=socio, empresa=emp2, rol="propietario").exists())
        dests_segunda = [addr for m in mail.outbox for addr in m.to]
        self.assertIn("socio@nortelog.co", dests_segunda)
        self.assertIn("sede2@nortelog.co", dests_segunda)
        self.assertTrue(RegistroActividad.objects.filter(accion="CREAR_EMPRESA", empresa=emp2).exists())
