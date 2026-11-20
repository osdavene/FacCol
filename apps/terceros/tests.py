from django.test import TestCase, Client
from django.urls import reverse
from apps.usuarios.models import User, EmpresaUsuario
from apps.empresas.models import Empresa
from apps.terceros.models import Tercero


class TercerosViewsTests(TestCase):
    def setUp(self):
        self.emp_a = Empresa.objects.create(razon_social="Empresa A", nit="900777888")
        self.emp_b = Empresa.objects.create(razon_social="Empresa B", nit="900999000")
        self.user_a = User.objects.create_user(
            username="admin_a@faccol.test", email="admin_a@faccol.test", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=self.user_a, empresa=self.emp_a, rol="admin", activo=True)
        self.cliente_a = Tercero.objects.create(
            empresa=self.emp_a, numero_documento="222222222222", razon_social="Consumidor Final", es_cliente=True
        )
        self.cliente_b = Tercero.objects.create(
            empresa=self.emp_b, numero_documento="800123456", razon_social="Cliente B", es_cliente=True
        )

    def test_tercero_detalle_propio_y_aislamiento_entre_empresas(self):
        client = Client()
        client.force_login(self.user_a)

        resp_ok = client.get(reverse("terceros:detalle", args=[self.cliente_a.id]))
        self.assertEqual(resp_ok.status_code, 200)
        self.assertContains(resp_ok, "Consumidor Final")

        resp_ajeno = client.get(reverse("terceros:detalle", args=[self.cliente_b.id]))
        self.assertEqual(resp_ajeno.status_code, 404)
