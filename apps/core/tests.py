from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from apps.usuarios.models import User, EmpresaUsuario
from apps.empresas.models import Empresa, Plan
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
        resp = client.post(reverse("usuarios:password_reset"), {"email": "cliente@negocio.co"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].alternatives[0][0]
        import re
        m = re.search(r'href="([^"]+/auth/password-reset/[^"]+)"', body)
        self.assertIsNotNone(m)
        reset_path = "/" + m.group(1).split("://", 1)[1].split("/", 1)[1]
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

