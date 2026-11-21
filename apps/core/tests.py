from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from apps.usuarios.models import User, EmpresaUsuario
from apps.empresas.models import Empresa, Modulo, Plan, SolicitudCambioPlan
from apps.facturacion.models import TransaccionWompi
from apps.facturacion.wompi_service import activate_wompi_transaction
from apps.core.models import ConfiguracionPlataforma
from apps.core.platform_settings import encrypt_secret, get_platform_setting, set_platform_setting
from apps.core.mail_service import get_mail_runtime_config


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


class ConfiguracionCifradaYCorreoTests(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            username="sa_cfg@faccol.test", email="sa_cfg@faccol.test", password="SafePass!2026",
            is_superadmin=True, is_superuser=True, is_staff=True
        )
        self.usuario = User.objects.create_user(
            username="cliente@negocio.co", email="cliente@negocio.co", password="OldPassword123!"
        )

    def test_cifrado_fernet_y_enmascarado_sin_env(self):
        set_platform_setting("mail_password", "super-smtp-secret-999", is_secret=True)
        row = ConfiguracionPlataforma.objects.get(key="mail_password")
        self.assertTrue(row.value.startswith("enc:"))
        self.assertNotIn("super-smtp-secret-999", row.value)
        self.assertEqual(get_platform_setting("mail_password"), "super-smtp-secret-999")

        # Resolución empresa con SMTP propio vs plataforma
        set_platform_setting("mail_host", "email-smtp.us-east-1.amazonaws.com")
        set_platform_setting("mail_username", "PLATFORM_USER")
        set_platform_setting("mail_from_address", "notificaciones@servigest.online")

        emp_sin_smtp = Empresa.objects.create(razon_social="Tienda Sin SMTP", nit="900100200")
        cfg1 = get_mail_runtime_config(emp_sin_smtp)
        self.assertEqual(cfg1["source"], "plataforma")
        self.assertEqual(cfg1["from_address"], "notificaciones@servigest.online")

        emp_con_smtp = Empresa.objects.create(
            razon_social="Empresa Con SMTP",
            nit="900100300",
            smtp_host="smtp.empresa.co",
            smtp_user="facturas@empresa.co",
            smtp_password=encrypt_secret("clave-propia-123"),
            smtp_from_address="facturas@empresa.co",
        )
        cfg2 = get_mail_runtime_config(emp_con_smtp)
        self.assertEqual(cfg2["source"], "empresa")
        self.assertEqual(cfg2["password"], "clave-propia-123")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_recuperacion_contrasena_flujo_completo(self):
        set_platform_setting("mail_from_address", "notificaciones@servigest.online")
        client = Client()
        r_form = client.get(reverse("usuarios:password_reset"))
        self.assertEqual(r_form.status_code, 200)
        self.assertContains(r_form, "css/app.css")
        self.assertContains(r_form, "fc-auth-card")

        resp = client.post(reverse("usuarios:password_reset"), {"email": "cliente@negocio.co"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].alternatives[0][0]
        import re
        m = re.search(r'href="([^"]+/auth/password-reset/[^"]+)"', body)
        self.assertIsNotNone(m)
        reset_path = "/" + m.group(1).split("://", 1)[1].split("/", 1)[1]
        r_confirm_get = client.get(reset_path)
        self.assertEqual(r_confirm_get.status_code, 200)
        self.assertContains(r_confirm_get, "css/app.css")
        self.assertContains(r_confirm_get, "fc-auth-card")

        resp_confirm = client.post(reset_path, {"password1": "NewPass!2026", "password2": "NewPass!2026"}, follow=True)
        self.assertEqual(resp_confirm.status_code, 200)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password("NewPass!2026"))

    def test_backoffice_guarda_configuracion_cifrada_y_no_sobrescribe_si_vacio(self):
        client = Client()
        client.force_login(self.superadmin)
        r = client.post(reverse("backoffice:configuracion_plataforma"), {
            "seccion": "wompi",
            "wompi_active_flag": "on",
            "wompi_public_key": "pub_test_123456789",
            "wompi_private_key": "prv_test_secret_abc",
            "wompi_integrity_secret": "test_integrity_xyz",
            "wompi_events_secret": "test_events_999",
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(get_platform_setting("wompi_integrity_secret"), "test_integrity_xyz")

        # Guardar con campos secretos en blanco debe conservar secretos anteriores
        client.post(reverse("backoffice:configuracion_plataforma"), {
            "seccion": "wompi",
            "wompi_active_flag": "on",
            "wompi_public_key": "pub_test_123456789",
            "wompi_private_key": "",
            "wompi_integrity_secret": "",
            "wompi_events_secret": "",
        }, follow=True)
        self.assertEqual(get_platform_setting("wompi_integrity_secret"), "test_integrity_xyz")

        # Guardar clave Gemini API cifrada y consultar Mi Plan como inquilino
        client.post(reverse("backoffice:configuracion_plataforma"), {
            "seccion": "ia",
            "gemini_api_key": "AIzaSecretTestKey999",
        }, follow=True)
        self.assertEqual(get_platform_setting("gemini_api_key"), "AIzaSecretTestKey999")

        emp = Empresa.objects.create(razon_social="Plan Check SAS", nit="900818283")
        admin_emp = User.objects.create_user(
            username="planadmin@faccol.test", email="planadmin@faccol.test", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=admin_emp, empresa=emp, rol="admin", activo=True)
        c2 = Client()
        c2.force_login(admin_emp)
        r_plan = c2.get(reverse("core:mi_plan"))
        self.assertEqual(r_plan.status_code, 200)
        self.assertContains(r_plan, "Documentos DIAN Mes Actual")


class MiPlanYCambioPlanTests(TestCase):
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_mi_plan_con_y_sin_wompi_y_cambio_multiempresa(self):
        mod_pos = Modulo.objects.create(codigo="pos", nombre="POS", ruta="pos:caja")
        mod_nom = Modulo.objects.create(codigo="nomina", nombre="Nomina", ruta="nomina:nominas_lista")
        plan_tienda = Plan.objects.create(nombre="Plan Tienda", precio_mensual=29900, activo=True)
        plan_tienda.modulos.set([mod_pos])
        plan_emp = Plan.objects.create(nombre="Plan Empresarial", precio_mensual=189000, activo=True)
        plan_emp.modulos.set([mod_pos, mod_nom])

        sa = User.objects.create_user(
            username="sa_plan@faccol.test", email="sa_plan@faccol.test",
            password="SafePass!2026", is_superadmin=True,
            is_superuser=True, is_staff=True,
        )
        set_platform_setting("mail_host", "smtp.test.local", is_secret=False)
        set_platform_setting("mail_from_address", "noreply@faccol.test", is_secret=False)

        emp1 = Empresa.objects.create(razon_social="Empresa Uno SAS", nit="900100100", plan=plan_tienda)
        emp2 = Empresa.objects.create(razon_social="Empresa Dos SAS", nit="900200200", plan=plan_tienda)
        admin = User.objects.create_user(
            username="multi@faccol.test", email="multi@faccol.test", password="SafePass!2026"
        )
        EmpresaUsuario.objects.create(user=admin, empresa=emp1, rol="propietario", activo=True)
        EmpresaUsuario.objects.create(user=admin, empresa=emp2, rol="propietario", activo=True)

        client = Client()
        client.force_login(admin)
        client.get(reverse("core:cambiar_empresa", args=[emp2.id]))

        # Sin Wompi Plataforma configurado: muestra banner explicativo y botón de solicitud en vez de tarjeta muerta
        set_platform_setting("wompi_public_key", "", is_secret=False)
        set_platform_setting("wompi_integrity_secret", "", is_secret=True)
        r_no_wompi = client.get(reverse("core:mi_plan"))
        self.assertEqual(r_no_wompi.status_code, 200)
        self.assertContains(r_no_wompi, "Solicitar Cambio de Plan")
        self.assertContains(r_no_wompi, "Plan Actual Activo")

        r_req = client.post(reverse("core:solicitar_cambio_plan"), {"plan_id": plan_emp.id}, follow=True)
        self.assertEqual(r_req.status_code, 200)
        self.assertContains(r_req, "Solicitud registrada")
        self.assertGreaterEqual(len(mail.outbox), 1)
        self.assertTrue(any("sa_plan@faccol.test" in m.to for m in mail.outbox))
        emp2.refresh_from_db()
        self.assertEqual(emp2.plan_id, plan_tienda.id)
        sol = SolicitudCambioPlan.objects.get(empresa=emp2, estado="PENDIENTE")
        self.assertEqual(sol.plan_solicitado_id, plan_emp.id)

        # El SuperAdmin ve la solicitud en Planes & Suscripciones y la aprueba en un clic
        sa_client = Client()
        sa_client.force_login(sa)
        r_planes = sa_client.get(reverse("backoffice:planes_lista"))
        self.assertContains(r_planes, emp2.display_name)
        sa_client.post(
            reverse("backoffice:solicitud_plan_resolver", args=[sol.id]),
            {"accion": "aprobar"},
            follow=True,
        )
        emp2.refresh_from_db()
        sol.refresh_from_db()
        self.assertEqual(sol.estado, "APROBADA")
        self.assertEqual(emp2.plan_id, plan_emp.id)
        self.assertTrue(emp2.tiene_modulo("nomina"))

        # Devolvemos emp2 a plan_tienda para probar también el flujo automático con Wompi
        emp2.plan = plan_tienda
        emp2.save(update_fields=["plan"])
        emp2.sincronizar_modulos_con_plan(plan_tienda)
        self.assertFalse(emp2.tiene_modulo("nomina"))

        # Con Wompi Plataforma activo: inicia checkout y activa exactamente emp2 (por NIT), sincronizando módulos
        set_platform_setting("wompi_active_flag", "1", is_secret=False)
        set_platform_setting("wompi_public_key", "pub_test_plan_ok", is_secret=False)
        set_platform_setting("wompi_integrity_secret", "integ_test_plan_ok", is_secret=True)
        r_con_wompi = client.get(reverse("core:mi_plan"))
        self.assertContains(r_con_wompi, "Cambiar a este Plan")

        r_init = client.post(reverse("core:suscripcion_wompi_iniciar"), {
            "plan_id": plan_emp.id,
            "razon_social": emp2.razon_social,
            "nit": emp2.nit,
            "email": "multi@faccol.test",
        })
        self.assertEqual(r_init.status_code, 302)
        self.assertIn("checkout.wompi.co", r_init.url)

        tx = TransaccionWompi.objects.get(plan=plan_emp, customer_nit=emp2.nit)
        tx.status = "APPROVED"
        tx.save()
        activate_wompi_transaction(tx)

        emp1.refresh_from_db()
        emp2.refresh_from_db()
        self.assertEqual(emp1.plan_id, plan_tienda.id)
        self.assertEqual(emp2.plan_id, plan_emp.id)
        self.assertTrue(emp2.tiene_modulo("nomina"))

