from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from apps.usuarios.models import User, EmpresaUsuario
from apps.empresas.models import Empresa
from apps.contabilidad.models import PlanCuenta, AsientoContable


class ContabilidadViewsTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(razon_social="Contable SAS", nit="900555666")
        self.contador = User.objects.create_user(
            username="contador@faccol.test", email="contador@faccol.test", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=self.contador, empresa=self.empresa, rol="contador", activo=True)
        self.cta_caja = PlanCuenta.objects.create(
            empresa=self.empresa, codigo="110505", nombre="Caja General", naturaleza="DEBITO"
        )
        self.cta_ventas = PlanCuenta.objects.create(
            empresa=self.empresa, codigo="413505", nombre="Comercio al por menor", naturaleza="CREDITO"
        )

    def test_asiento_crear_renderiza_y_guarda_cuadrado(self):
        client = Client()
        client.force_login(self.contador)

        resp_get = client.get(reverse("contabilidad:asiento_crear"))
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, "Registrar Asiento Contable")

        # Intento descuadrado no debe guardar
        client.post(reverse("contabilidad:asiento_crear"), {
            "numero_comprobante": "CD-001",
            "fecha": "2026-11-20",
            "tipo": "VENTA",
            "concepto": "Descuadrado",
            "cuenta[]": [str(self.cta_caja.id), str(self.cta_ventas.id)],
            "tercero[]": ["", ""],
            "descripcion[]": ["Caja", "Venta"],
            "debito[]": ["10000", "0"],
            "credito[]": ["0", "5000"],
        })
        self.assertEqual(AsientoContable.objects.count(), 0)

        # Intento cuadrado debe guardar
        resp_post = client.post(reverse("contabilidad:asiento_crear"), {
            "numero_comprobante": "CD-001",
            "fecha": "2026-11-20",
            "tipo": "VENTA",
            "concepto": "Venta mostrador",
            "cuenta[]": [str(self.cta_caja.id), str(self.cta_ventas.id)],
            "tercero[]": ["", ""],
            "descripcion[]": ["Entrada caja", "Ingreso venta"],
            "debito[]": ["10000", "0"],
            "credito[]": ["0", "10000"],
        }, follow=True)
        self.assertEqual(resp_post.status_code, 200)
        self.assertEqual(AsientoContable.objects.count(), 1)
        asiento = AsientoContable.objects.first()
        self.assertTrue(asiento.esta_cuadrado)
        self.assertEqual(asiento.total_debito, Decimal("10000.00"))

        r_est = client.get(reverse("contabilidad:estados_financieros"))
        self.assertEqual(r_est.status_code, 200)
        self.assertContains(r_est, "SUMAS IGUALES BALANCE DE PRUEBA")
        self.assertContains(r_est, "10.000")

        r_csv = client.get(reverse("contabilidad:estados_financieros") + "?export=csv")
        self.assertEqual(r_csv.status_code, 200)
        self.assertIn("text/csv", r_csv["Content-Type"])
        self.assertIn(b"110505", r_csv.content)
