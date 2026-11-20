from django.test import TestCase, Client
from django.urls import reverse
from apps.usuarios.models import User, EmpresaUsuario
from apps.empresas.models import Empresa, Plan


class AislamientoMultiTenantTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(nombre="Plan Pro Test", precio_mensual=99000)
        self.emp_a = Empresa.objects.create(razon_social="Tienda A SAS", nit="900111222", plan=self.plan)
        self.emp_b = Empresa.objects.create(razon_social="Tienda B SAS", nit="900333444", plan=self.plan)

        self.superadmin = User.objects.create_user(
            username="sa@faccol.test", email="sa@faccol.test", password="SafePass!2026",
            is_superadmin=True, is_superuser=True, is_staff=True
        )
        self.user_a = User.objects.create_user(
            username="duenoa@faccol.test", email="duenoa@faccol.test", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=self.user_a, empresa=self.emp_a, rol="propietario", activo=True)

    def test_superadmin_dashboard_y_aislamiento_operativo(self):
        client = Client()
        client.force_login(self.superadmin)
        resp = client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Centro de Comando Global SuperAdmin")

        # SuperAdmin no puede operar rutas de inquilinos
        for url_name in ("facturacion:facturas_lista", "contabilidad:asientos_lista", "pos:pos_terminal"):
            r = client.get(reverse(url_name))
            self.assertEqual(r.status_code, 302)

    def test_inquilino_a_no_puede_cambiar_a_empresa_b(self):
        client = Client()
        client.force_login(self.user_a)
        resp = client.get(reverse("core:cambiar_empresa", args=[self.emp_b.id]))
        self.assertEqual(resp.status_code, 404)
