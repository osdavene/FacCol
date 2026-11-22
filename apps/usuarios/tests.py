from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from apps.core.models import RegistroActividad
from apps.empresas.models import Empresa
from apps.usuarios.models import User, EmpresaUsuario


class UsuariosClaveYPermisosTests(TestCase):
    def setUp(self):
        self.emp = Empresa.objects.create(
            razon_social="Empresa Clave SAS",
            nit="900654321",
            email="gerencia@empresaclave.co",
        )
        self.superadmin = User.objects.create_user(
            username="sa_u@faccol.test", email="sa_u@faccol.test", password="SafePass!2026",
            is_superadmin=True, is_superuser=True, is_staff=True,
        )
        self.propietario = User.objects.create_user(
            username="dueno@empresaclave.co", email="dueno@empresaclave.co", password="OldPass!2026",
        )
        self.vendedor = User.objects.create_user(
            username="vendedor@empresaclave.co", email="vendedor@empresaclave.co", password="OldPass!2026",
        )
        self.asig_prop = EmpresaUsuario.objects.create(
            user=self.propietario, empresa=self.emp, rol="propietario", activo=True
        )
        self.asig_vend = EmpresaUsuario.objects.create(
            user=self.vendedor, empresa=self.emp, rol="vendedor", activo=True
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_superadmin_no_puede_cambiar_claves_solo_enlaces_y_aviso_admin(self):
        client = Client()
        client.force_login(self.superadmin)

        r_lista = client.get(reverse("usuarios:lista"))
        self.assertEqual(r_lista.status_code, 200)
        self.assertNotContains(r_lista, "Nueva clave")
        self.assertContains(r_lista, "Enviar Enlace")
        self.assertContains(r_lista, "Avisar Admin")

        # Bloqueo estricto 403 si intenta fijar contraseña directamente
        r_block = client.post(reverse("usuarios:asignar_clave", args=[self.asig_vend.id]), {
            "nueva_clave": "AccesoPrueba2026!",
        })
        self.assertEqual(r_block.status_code, 403)
        self.vendedor.refresh_from_db()
        self.assertTrue(self.vendedor.check_password("OldPass!2026"))
        self.assertTrue(RegistroActividad.objects.filter(accion="INTENTO_CAMBIO_CLAVE_BLOQUEADO").exists())

        # SuperAdmin envía enlace seguro al usuario
        r_link = client.post(reverse("usuarios:enviar_enlace_clave", args=[self.asig_vend.id]), follow=True)
        self.assertEqual(r_link.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["vendedor@empresaclave.co"])
        self.assertIn("/auth/password-reset/", mail.outbox[0].body)
        self.assertTrue(RegistroActividad.objects.filter(accion="ENVIAR_ENLACE_CLAVE", objeto_id=str(self.vendedor.id)).exists())

        # SuperAdmin avisa al propietario/admin de la empresa cuando el empleado perdió acceso a su correo
        mail.outbox.clear()
        r_aviso = client.post(reverse("usuarios:notificar_admin_acceso", args=[self.asig_vend.id]), follow=True)
        self.assertEqual(r_aviso.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["dueno@empresaclave.co"])
        self.assertTrue(RegistroActividad.objects.filter(accion="NOTIFICAR_ADMIN_ACCESO", empresa=self.emp).exists())

        # Actualizar correo por SuperAdmin exige motivo auditado
        mail.outbox.clear()
        r_sin_motivo = client.post(reverse("usuarios:actualizar_correo", args=[self.asig_prop.id]), {
            "nuevo_correo": "nuevo_dueno@empresaclave.co",
            "motivo": "",
            "enviar_enlace": "on",
        }, follow=True)
        self.assertContains(r_sin_motivo, "motivo auditado")
        self.propietario.refresh_from_db()
        self.assertEqual(self.propietario.email, "dueno@empresaclave.co")

        r_con_motivo = client.post(reverse("usuarios:actualizar_correo", args=[self.asig_prop.id]), {
            "nuevo_correo": "nuevo_dueno@empresaclave.co",
            "motivo": "Propietario perdió su Gmail anterior, validado con RUT",
            "enviar_enlace": "on",
        }, follow=True)
        self.assertContains(r_con_motivo, "nuevo_dueno@empresaclave.co")
        self.propietario.refresh_from_db()
        self.assertEqual(self.propietario.email, "nuevo_dueno@empresaclave.co")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["nuevo_dueno@empresaclave.co"])
        self.assertTrue(RegistroActividad.objects.filter(accion="ACTUALIZAR_CORREO_USUARIO", objeto_id=str(self.propietario.id)).exists())

        # Admin interno de la empresa puede actualizar el correo de su vendedor y enviarle enlace
        mail.outbox.clear()
        c_prop = Client()
        c_prop.force_login(self.propietario)
        c_prop.post(reverse("usuarios:actualizar_correo", args=[self.asig_vend.id]), {
            "nuevo_correo": "vendedor.nuevo@empresaclave.co",
            "enviar_enlace": "on",
        }, follow=True)
        self.vendedor.refresh_from_db()
        self.assertEqual(self.vendedor.email, "vendedor.nuevo@empresaclave.co")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["vendedor.nuevo@empresaclave.co"])

