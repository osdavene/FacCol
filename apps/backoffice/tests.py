from django.test import TestCase, Client
from django.urls import reverse
from apps.usuarios.models import User
from apps.empresas.models import Empresa, Modulo, Plan


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
        emp = Empresa.objects.get(nit="1098765432")
        self.assertTrue(emp.modulos.filter(codigo="facturacion").exists())
        usuario = User.objects.get(email="dueno@esquina.co")
        self.assertFalse(usuario.check_password("Clave1234*"))

        r_form = client.get(reverse("backoffice:empresa_crear"))
        self.assertEqual(r_form.status_code, 200)
        self.assertNotContains(r_form, "wompi_public_key")
        self.assertNotContains(r_form, "Pasarela Wompi")
        self.assertNotContains(r_form, "smtp_host")
