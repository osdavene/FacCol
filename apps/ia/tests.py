from django.test import TestCase, Client
from django.urls import reverse

from apps.empresas.models import Empresa, Modulo, EmpresaModulo
from apps.usuarios.models import User, EmpresaUsuario


class FacColIaPermisosTests(TestCase):
    def test_bloqueo_modulo_ia_cuando_plan_no_lo_incluye(self):
        empresa = Empresa.objects.create(razon_social="Sin IA SAS", nit="900111999")
        user = User.objects.create_user(
            username="noia@faccol.test", email="noia@faccol.test", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=user, empresa=empresa, rol="admin", activo=True)

        mod_fac = Modulo.objects.create(codigo="facturacion", nombre="Facturación")
        mod_ia = Modulo.objects.create(codigo="ia", nombre="IA")
        EmpresaModulo.objects.create(empresa=empresa, modulo=mod_fac, activo=True)
        EmpresaModulo.objects.create(empresa=empresa, modulo=mod_ia, activo=False)

        client = Client()
        client.force_login(user)
        resp = client.get(reverse("ia:asistente"))
        self.assertEqual(resp.status_code, 302)

