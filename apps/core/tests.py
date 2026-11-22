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
        sent_msg = mail.outbox[0]
        self.assertIn("http", sent_msg.body)
        self.assertIn("/auth/password-reset/", sent_msg.body)
        self.assertEqual(sent_msg.extra_headers.get("Auto-Submitted"), "auto-generated")
        self.assertIn("@servigest.online", sent_msg.extra_headers.get("Message-ID", ""))
        body = sent_msg.alternatives[0][0]
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


class ArquitecturaCorreoYCampanasTests(TestCase):
    def setUp(self):
        from django.utils import timezone
        self.now = timezone.now()
        self.sa = User.objects.create_user(
            username="sa_camp@faccol.co", email="sa_camp@faccol.co",
            password="SafePass!2026", is_superadmin=True, is_superuser=True, is_staff=True,
            email_verified_at=self.now,
        )
        self.plan_gratis = Plan.objects.create(nombre="Plan Inicio Gratis", precio_mensual=0, activo=True)
        self.emp = Empresa.objects.create(razon_social="Cliente Verificado SAS", nit="900555666", plan=self.plan_gratis)

    def test_prioridad_env_sobre_backoffice_y_sanitizacion_mail(self):
        import os
        from unittest.mock import patch, MagicMock
        from apps.core.config import mail_settings
        from apps.core.mail import (
            _html_to_plain_text,
            _safe_http_url,
            _sanitize_header,
            build_campaign_email_html,
            is_dummy_or_invalid_email,
            send_resend_only_email,
            send_system_email,
        )

        # Backoffice como respaldo cuando .env no define la clave
        set_platform_setting("mail_host", "smtp.backoffice.co")
        set_platform_setting("mail_username", "backoffice_user")
        with patch.dict(os.environ, {"MAIL_HOST": "", "EMAIL_HOST": ""}, clear=False):
            self.assertEqual(mail_settings.MAIL_HOST, "smtp.backoffice.co")

        # .env tiene prioridad estricta sobre Backoffice
        with patch.dict(os.environ, {"MAIL_HOST": "smtp.env.aws.com", "MAIL_USERNAME": "ENV_USER", "RESEND_API_KEY": "re_env_123"}, clear=False):
            self.assertEqual(mail_settings.MAIL_HOST, "smtp.env.aws.com")
            self.assertEqual(mail_settings.MAIL_USERNAME, "ENV_USER")
            self.assertEqual(mail_settings.RESEND_API_KEY, "re_env_123")

        # Sanitización y URLs seguras
        self.assertEqual(_sanitize_header("Hola\r\nBcc: evil@x.com"), "Hola Bcc: evil@x.com")
        self.assertEqual(_safe_http_url("javascript:alert(1)"), "#")
        self.assertTrue(_safe_http_url("https://faccol.servigest.online/login").startswith("https://"))
        plain = _html_to_plain_text('<p>Hola</p><a href="https://faccol.servigest.online/ok">Activar</a>')
        self.assertIn("Activar (https://faccol.servigest.online/ok)", plain)
        self.assertTrue(is_dummy_or_invalid_email("demo@example.com"))
        self.assertFalse(is_dummy_or_invalid_email("gerencia@miempresa.co"))

        # HTML de campaña con viñetas, tabla presentation, XSS escapado y BAJA
        html_c = build_campaign_email_html(
            recipient_name="<script>alert(1)</script>",
            subject="Lanzamiento FacCol",
            body_text="Intro.\n\nBeneficios clave:\n- Factura DIAN: rápida\n- POS Express: 58mm",
            cta_label="Ir ahora",
            cta_url="https://faccol.servigest.online/auth/login/",
        )
        self.assertNotIn("<script>", html_c)
        self.assertIn("table role='presentation'", html_c)
        self.assertIn("&#10003;", html_c)
        self.assertIn("FACCOL", html_c)
        self.assertIn("BAJA", html_c)

        # send_resend_only_email nunca cae a SMTP si falta RESEND_API_KEY
        with patch.dict(os.environ, {"RESEND_API_KEY": ""}, clear=False):
            set_platform_setting("resend_api_key", "", is_secret=True)
            self.assertFalse(send_resend_only_email("cliente@empresa.co", "Ana", "Asunto", "<p>Hola</p>"))
        self.assertFalse(send_resend_only_email("dummy@test.com", "Ana", "Asunto", "<p>Hola</p>"))
        self.assertTrue(send_system_email("dummy@example.com", "Ana", "Asunto", "<p>Hola</p>"))

        # send_system_email: Resend falla -> fallback automático a smtplib.SMTP real
        mock_resp = MagicMock(status_code=500, text="err")
        mock_smtp_ctx = MagicMock()
        mock_smtp_cls = MagicMock(return_value=mock_smtp_ctx)
        with patch.dict(
            os.environ,
            {
                "DJANGO_TEST_ALLOW_RAW_SMTP": "1",
                "RESEND_API_KEY": "re_test_key",
                "MAIL_HOST": "email-smtp.us-east-1.amazonaws.com",
                "MAIL_PORT": "587",
                "MAIL_ENCRYPTION": "tls",
                "MAIL_USERNAME": "SES_USER",
                "MAIL_PASSWORD": "SES_PASSWORD",
                "MAIL_FROM_ADDRESS": "notificaciones@servigest.online",
            },
            clear=False,
        ), patch("apps.core.mail.httpx.post", return_value=mock_resp), patch("apps.core.mail.smtplib.SMTP", mock_smtp_cls):
            ok = send_system_email("real@empresa.co", "Carlos", "Asunto", "<p>Contenido</p>")
            self.assertTrue(ok)
            mock_smtp_ctx.__enter__.return_value.starttls.assert_called_once()
            mock_smtp_ctx.__enter__.return_value.login.assert_called_once_with("SES_USER", "SES_PASSWORD")
            mock_smtp_ctx.__enter__.return_value.sendmail.assert_called_once()

        # send_resend_only_email con RESEND_API_KEY incluye List-Unsubscribe
        ok_resp = MagicMock(status_code=200, text="{}")
        with patch.dict(
            os.environ,
            {"RESEND_API_KEY": "re_live_mock", "MAIL_ADMIN_RECIPIENT": "bajas@servigest.online"},
            clear=False,
        ), patch("apps.core.mail.httpx.post", return_value=ok_resp) as m_post:
            self.assertTrue(send_resend_only_email("real@empresa.co", "Carlos", "Novedad", "<p>Hola</p>"))
            sent_json = m_post.call_args.kwargs["json"]
            self.assertEqual(sent_json["headers"]["List-Unsubscribe"], "<mailto:bajas@servigest.online?subject=BAJA>")

    def test_campanas_tandas_antispam_banner_y_panel_superadmin(self):
        from apps.core.campaigns import (
            DAILY_SAFE_CAP,
            get_campaign_state,
            get_campaign_summary,
            reset_campaign_cycle,
            save_campaign_state,
            send_campaign_batch,
            send_campaign_test_email,
        )

        u_ok1 = User.objects.create_user(
            username="u1@negocio.co", email="u1@negocio.co", password="SafePass!2026",
            activo=True, is_active=True, email_verified_at=self.now,
        )
        u_ok2 = User.objects.create_user(
            username="u2@negocio.co", email="u2@negocio.co", password="SafePass!2026",
            activo=True, is_active=True, email_verified_at=self.now,
        )
        User.objects.create_user(
            username="noverif@negocio.co", email="noverif@negocio.co", password="SafePass!2026",
            activo=True, is_active=True, email_verified_at=None,
        )
        User.objects.create_user(
            username="dummy@test.com", email="dummy@test.com", password="SafePass!2026",
            activo=True, is_active=True, email_verified_at=self.now,
        )

        # Sin prueba previa bloquea con pretest_required
        res_block = send_campaign_batch(
            subject="Aviso 1", body_text="Cuerpo", cta_label="Ver", cta_url="https://faccol.servigest.online",
            batch_size=1, sleep_seconds=0.0, sender_fn=lambda *a: True,
        )
        self.assertFalse(res_block["ok"])
        self.assertEqual(res_block["error"], "pretest_required")

        # Prueba con dummy rechazada; con correo real habilita tanda
        ok_bad, _ = send_campaign_test_email(
            test_email="bad@example.com", admin_name="Admin",
            subject="Aviso 1", body_text="Cuerpo", cta_label="Ver", cta_url="https://faccol.servigest.online",
            sender_fn=lambda *a: True,
        )
        self.assertFalse(ok_bad)

        ok_good, _ = send_campaign_test_email(
            test_email="admin@servigest.online", admin_name="Admin",
            subject="Aviso 1", body_text="Cuerpo", cta_label="Ver", cta_url="https://faccol.servigest.online",
            sender_fn=lambda *a: True,
        )
        self.assertTrue(ok_good)

        # Si se edita el texto sin re-probar, vuelve a exigir pretest_required
        res_dirty = send_campaign_batch(
            subject="Aviso 1 EDITADO", body_text="Cuerpo", cta_label="Ver", cta_url="https://faccol.servigest.online",
            batch_size=1, sleep_seconds=0.0, sender_fn=lambda *a: True,
        )
        self.assertEqual(res_dirty["error"], "pretest_required")

        # Volvemos a probar y enviamos tanda 1 (batch_size=1) y luego tanda 2 (solo envía el faltante, sin repetir u_ok1)
        send_campaign_test_email(
            test_email="admin@servigest.online", admin_name="Admin",
            subject="Aviso 1", body_text="Cuerpo", cta_label="Ver", cta_url="https://faccol.servigest.online",
            batch_size=1, sender_fn=lambda *a: True,
        )
        sent_to = []
        r1 = send_campaign_batch(
            subject="Aviso 1", body_text="Cuerpo", cta_label="Ver", cta_url="https://faccol.servigest.online",
            batch_size=1, sleep_seconds=0.0, sender_fn=lambda e, *a: (sent_to.append(e) or True),
        )
        self.assertEqual(r1["sent_count"], 1)
        self.assertEqual(r1["pending_remaining"], 1)

        r2 = send_campaign_batch(
            subject="Aviso 1", body_text="Cuerpo", cta_label="Ver", cta_url="https://faccol.servigest.online",
            batch_size=10, sleep_seconds=0.0, sender_fn=lambda e, *a: (sent_to.append(e) or True),
        )
        self.assertEqual(r2["sent_count"], 1)
        self.assertEqual(set(sent_to), {"u1@negocio.co", "u2@negocio.co"})

        # Tope diario 50/50 bloquea
        from django.utils import timezone
        st = get_campaign_state()
        st["daily_counts"][timezone.localdate().strftime("%Y-%m-%d")] = DAILY_SAFE_CAP
        save_campaign_state(st)
        r_cap = send_campaign_batch(
            subject="Aviso 1", body_text="Cuerpo", cta_label="Ver", cta_url="https://faccol.servigest.online",
            batch_size=5, sleep_seconds=0.0, sender_fn=lambda *a: True,
        )
        self.assertEqual(r_cap["error"], "daily_cap_reached")

        # Reiniciar ciclo limpia prueba y cambia ID
        new_cid = reset_campaign_cycle("mayo-2026")
        self.assertEqual(new_cid, "mayo-2026")
        self.assertFalse(get_campaign_summary()["test_passed"])

        # Panel Admin protegido + pestaña Banner visible en dashboard de usuario
        c_user = Client()
        c_user.force_login(u_ok1)
        EmpresaUsuario.objects.create(user=u_ok1, empresa=self.emp, rol="propietario", activo=True)
        self.assertEqual(c_user.get(reverse("backoffice:comunicados_panel")).status_code, 302)

        c_sa = Client()
        c_sa.force_login(self.sa)
        r_panel = c_sa.get(reverse("backoffice:comunicados_panel"))
        self.assertEqual(r_panel.status_code, 200)
        self.assertContains(r_panel, "Elegibles Verificados")
        c_sa.post(reverse("backoffice:comunicado_guardar_banner"), {
            "enabled": "on",
            "campaign_id": "banner-v2",
            "badge": "Estreno",
            "title": "Nueva Caja Express",
            "message": "Prueba ya las mejoras POS.",
            "cta_label": "Ir al POS",
            "cta_url": "/pos/",
        }, follow=True)
        r_dash = c_user.get(reverse("core:dashboard"))
        self.assertContains(r_dash, "Nueva Caja Express")
        self.assertContains(r_dash, "banner_dismissed_")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_registro_publico_y_verificacion_token_unico(self):
        import re
        c = Client()
        r_reg = c.post(reverse("usuarios:registro"), {
            "razon_social": "Comercio Nuevo SAS",
            "nit": "901777888",
            "first_name": "Mateo",
            "last_name": "Rojas",
            "email": "mateo@comercionuevo.co",
            "perfil_operativo": "TIENDA",
            "password1": "ClaveSegura123",
            "password2": "ClaveSegura123",
        }, follow=True)
        self.assertEqual(r_reg.status_code, 200)
        self.assertContains(r_reg, "enlace de activación")

        u = User.objects.get(email="mateo@comercionuevo.co")
        self.assertFalse(u.activo)
        self.assertIsNone(u.email_verified_at)
        emp_nueva = Empresa.objects.get(nit="901777888")
        self.assertEqual(emp_nueva.plan_id, self.plan_gratis.id)

        # No puede iniciar sesión antes de verificar correo
        r_login_antes = c.post(reverse("usuarios:login"), {
            "login": "mateo@comercionuevo.co",
            "password": "ClaveSegura123",
        })
        self.assertContains(r_login_antes, "pendiente de verificación")

        # Verifica token de correo
        self.assertEqual(len(mail.outbox), 1)
        m = re.search(r'href="([^"]+/auth/verificar/[^"]+)"', mail.outbox[0].alternatives[0][0])
        self.assertIsNotNone(m)
        verify_path = "/" + m.group(1).split("://", 1)[1].split("/", 1)[1]

        r_ver = c.get(verify_path, follow=True)
        self.assertContains(r_ver, "Correo verificado")
        u.refresh_from_db()
        self.assertTrue(u.activo)
        self.assertIsNotNone(u.email_verified_at)

        # Token de un solo uso: segundo intento falla
        r_reuso = c.get(verify_path, follow=True)
        self.assertContains(r_reuso, "inválido o ya fue utilizado")

        # Ahora sí inicia sesión
        r_login_ok = c.post(reverse("usuarios:login"), {
            "login": "mateo@comercionuevo.co",
            "password": "ClaveSegura123",
        })
        self.assertEqual(r_login_ok.status_code, 302)


