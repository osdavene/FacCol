from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from apps.core.models import RegistroActividad
from apps.empresas.models import Empresa, Plan
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
        destinos_sa = [addr for msg in mail.outbox for addr in msg.to]
        self.assertIn("dueno@empresaclave.co", destinos_sa)
        self.assertIn("nuevo_dueno@empresaclave.co", destinos_sa)
        self.assertIn("gerencia@empresaclave.co", destinos_sa)
        self.assertTrue(any("/auth/password-reset/" in msg.body for msg in mail.outbox))
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
        destinos_vend = [addr for msg in mail.outbox for addr in msg.to]
        self.assertIn("vendedor@empresaclave.co", destinos_vend)
        self.assertIn("vendedor.nuevo@empresaclave.co", destinos_vend)
        self.assertIn("gerencia@empresaclave.co", destinos_vend)
        self.assertTrue(any("/auth/password-reset/" in msg.body for msg in mail.outbox))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class NotificacionesSeguridadTests(TestCase):
    def setUp(self):
        self.plan_base = Plan.objects.create(nombre="Emprendedor", precio_mensual=50000, activo=True)
        self.plan_pro = Plan.objects.create(nombre="Corporativo", precio_mensual=180000, activo=True)
        self.emp = Empresa.objects.create(
            razon_social="Comercializadora Norte SAS",
            nit="901222333",
            email="gerencia@nortesas.co",
            plan=self.plan_base,
        )
        self.superadmin = User.objects.create_user(
            username="sa_sec@faccol.test", email="sa_sec@faccol.test", password="SafePass!2026",
            is_superadmin=True, is_superuser=True, is_staff=True,
        )
        self.propietario = User.objects.create_user(
            username="socio@nortesas.co", email="socio@nortesas.co", password="PropPass!2026",
        )
        self.operador = User.objects.create_user(
            username="operador@nortesas.co", email="operador@nortesas.co", password="OperPass!2026",
        )
        self.asig_prop = EmpresaUsuario.objects.create(
            user=self.propietario, empresa=self.emp, rol="propietario", activo=True
        )
        self.asig_oper = EmpresaUsuario.objects.create(
            user=self.operador, empresa=self.emp, rol="operador", activo=True
        )

    def test_doble_alerta_cambio_correo_sin_enlace(self):
        client = Client()
        client.force_login(self.propietario)
        mail.outbox.clear()
        client.post(reverse("usuarios:actualizar_correo", args=[self.asig_oper.id]), {
            "nuevo_correo": "operador.nuevo@nortesas.co",
        }, follow=True)
        self.operador.refresh_from_db()
        self.assertEqual(self.operador.email, "operador.nuevo@nortesas.co")
        destinos = [addr for msg in mail.outbox for addr in msg.to]
        self.assertIn("operador@nortesas.co", destinos)
        self.assertIn("operador.nuevo@nortesas.co", destinos)
        self.assertIn("gerencia@nortesas.co", destinos)

    def test_alerta_cambio_clave_perfil_y_reset(self):
        # Cambio desde perfil por propietario notifica al usuario y al correo oficial de empresa
        client = Client()
        client.force_login(self.propietario)
        mail.outbox.clear()
        client.post(reverse("usuarios:perfil"), {
            "first_name": "Socio",
            "last_name": "Norte",
            "telefono": "3001112233",
            "tema": "oscuro",
            "nuevo_password": "NuevaProp!2026",
        }, follow=True)
        destinos_perfil = [addr for msg in mail.outbox for addr in msg.to]
        self.assertIn("socio@nortesas.co", destinos_perfil)
        self.assertIn("gerencia@nortesas.co", destinos_perfil)

        # Restablecimiento mediante token notifica al operador
        mail.outbox.clear()
        uid = urlsafe_base64_encode(force_bytes(self.operador.pk))
        token = default_token_generator.make_token(self.operador)
        client.logout()
        r = client.post(reverse("usuarios:password_reset_confirm", args=[uid, token]), {
            "password1": "ResetOper!2026",
            "password2": "ResetOper!2026",
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        destinos_reset = [addr for msg in mail.outbox for addr in msg.to]
        self.assertEqual(destinos_reset, ["operador@nortesas.co"])

    def test_alerta_cinco_intentos_fallidos_consecutivos(self):
        client = Client()
        mail.outbox.clear()
        for intento in range(1, 5):
            client.post(reverse("usuarios:login"), {
                "login": "operador@nortesas.co",
                "password": f"MalaClave{intento}",
            })
        self.assertEqual(len(mail.outbox), 0)

        # 5to fallo consecutivo dispara alerta al correo del usuario
        client.post(reverse("usuarios:login"), {
            "login": "operador@nortesas.co",
            "password": "MalaClave5",
        })
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["operador@nortesas.co"])
        self.assertIn("5 intentos fallidos", mail.outbox[0].subject)

        # Un login exitoso reinicia la racha: 1 fallo posterior no vuelve a alertar
        mail.outbox.clear()
        client.post(reverse("usuarios:login"), {
            "login": "operador@nortesas.co",
            "password": "OperPass!2026",
        })
        client.logout()
        client.post(reverse("usuarios:login"), {
            "login": "operador@nortesas.co",
            "password": "OtroFallo1",
        })
        self.assertEqual(len(mail.outbox), 0)

    def test_crear_usuario_sin_clave_y_cambio_rol_estado(self):
        client = Client()
        client.force_login(self.propietario)
        mail.outbox.clear()

        r_crear = client.post(reverse("usuarios:crear"), {
            "email": "contadora@nortesas.co",
            "first_name": "Laura",
            "last_name": "Gómez",
            "telefono": "3110001122",
            "rol": "contador",
            "password": "",
        }, follow=True)
        self.assertEqual(r_crear.status_code, 200)
        nuevo_u = User.objects.get(email="contadora@nortesas.co")
        destinos_crear = [addr for msg in mail.outbox for addr in msg.to]
        self.assertIn("contadora@nortesas.co", destinos_crear)
        self.assertIn("gerencia@nortesas.co", destinos_crear)
        self.assertTrue(any("/auth/password-reset/" in m.body for m in mail.outbox if m.to == ["contadora@nortesas.co"]))
        self.assertTrue(EmpresaUsuario.objects.filter(user=nuevo_u, empresa=self.emp, rol="contador").exists())

        # Cambio de rol notifica al colaborador y al correo oficial de empresa
        mail.outbox.clear()
        client.post(reverse("usuarios:cambiar_rol", args=[self.asig_oper.id]), {"rol": "admin"}, follow=True)
        destinos_rol = [addr for msg in mail.outbox for addr in msg.to]
        self.assertIn("operador@nortesas.co", destinos_rol)
        self.assertIn("gerencia@nortesas.co", destinos_rol)

        # Cambio de estado (desactivar acceso) notifica al colaborador y a empresa
        mail.outbox.clear()
        client.post(reverse("usuarios:cambiar_estado", args=[self.asig_oper.id]), follow=True)
        destinos_estado = [addr for msg in mail.outbox for addr in msg.to]
        self.assertIn("operador@nortesas.co", destinos_estado)
        self.assertIn("gerencia@nortesas.co", destinos_estado)

    def test_alerta_configuracion_empresa_y_backoffice_plan(self):
        c_prop = Client()
        c_prop.force_login(self.propietario)
        mail.outbox.clear()
        c_prop.post(reverse("core:configuracion_empresa"), {
            "seccion": "wompi",
            "wompi_public_key": "pub_test_123",
            "wompi_private_key": "prv_test_456",
        }, follow=True)
        destinos_wompi = [addr for msg in mail.outbox for addr in msg.to]
        self.assertIn("socio@nortesas.co", destinos_wompi)
        self.assertIn("gerencia@nortesas.co", destinos_wompi)

        c_sa = Client()
        c_sa.force_login(self.superadmin)
        mail.outbox.clear()
        c_sa.post(reverse("backoffice:empresa_toggle_activa", args=[self.emp.id]), follow=True)
        destinos_susp = [addr for msg in mail.outbox for addr in msg.to]
        self.assertIn("socio@nortesas.co", destinos_susp)
        self.assertIn("gerencia@nortesas.co", destinos_susp)

        # Asignación de plan desde backoffice
        mail.outbox.clear()
        c_sa.post(reverse("backoffice:empresa_editar", args=[self.emp.id]), {
            "razon_social": self.emp.razon_social,
            "nit": self.emp.nit,
            "regimen_tributario": self.emp.regimen_tributario,
            "perfil_operativo": self.emp.perfil_operativo,
            "email": self.emp.email,
            "plan": str(self.plan_pro.id),
            "prefijo_factura": "FE",
            "prefijo_pos_electronico": "POS",
            "modo_dian": self.emp.modo_dian,
            "activa": "on",
        }, follow=True)
        destinos_plan = [addr for msg in mail.outbox for addr in msg.to]
        self.assertIn("socio@nortesas.co", destinos_plan)
        self.assertIn("gerencia@nortesas.co", destinos_plan)
        self.assertTrue(any("Corporativo" in m.subject or "Corporativo" in m.body for m in mail.outbox))


