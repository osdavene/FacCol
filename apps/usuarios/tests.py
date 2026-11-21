from django.test import TestCase, Client
from django.urls import reverse
from apps.empresas.models import Empresa
from apps.usuarios.models import User, EmpresaUsuario


class UsuariosClaveYPermisosTests(TestCase):
    def setUp(self):
        self.emp = Empresa.objects.create(razon_social="Empresa Clave SAS", nit="900654321")
        self.superadmin = User.objects.create_user(
            username="sa_u@faccol.test", email="sa_u@faccol.test", password="SafePass!2026",
            is_superadmin=True, is_superuser=True, is_staff=True,
        )
        self.cliente_user = User.objects.create_user(
            username="cli_u@faccol.test", email="cli_u@faccol.test", password="OldPass!2026",
        )
        self.asig = EmpresaUsuario.objects.create(
            user=self.cliente_user, empresa=self.emp, rol="propietario", activo=True
        )

    def test_superadmin_puede_asignar_clave_a_usuario_de_empresa(self):
        client = Client()
        client.force_login(self.superadmin)
        r = client.post(reverse("usuarios:asignar_clave", args=[self.asig.id]), {
            "nueva_clave": "AccesoPrueba2026!",
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        self.cliente_user.refresh_from_db()
        self.assertTrue(self.cliente_user.check_password("AccesoPrueba2026!"))

