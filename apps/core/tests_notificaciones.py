import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.core.models import Notificacion, RegistroActividad
from apps.core.notifications import (
    notificar_usuarios_app,
)
from apps.core.platform_settings import encrypt_secret, set_platform_setting
from apps.empresas.models import Empresa, Modulo, Plan
from apps.facturacion.dian_service import transmitir_documento_dian
from apps.facturacion.models import Factura, FacturaItem, TransaccionWompi
from apps.inventario.models import Categoria, Producto, UnidadMedida
from apps.nomina.models import Empleado, Nomina
from apps.pos.models import Caja, CajaTurno
from apps.terceros.models import Tercero
from apps.usuarios.models import EmpresaUsuario, User


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CentroNotificacionesYEventosTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(nombre="Plan Pyme", precio_mensual=99000, limite_facturas_mes=5, activo=True)
        for cod, nom in (
            ("facturacion", "Facturación"),
            ("pos", "POS"),
            ("inventario", "Inventario"),
            ("contabilidad", "Contabilidad"),
            ("nomina", "Nómina"),
            ("terceros", "Terceros"),
        ):
            mod, _ = Modulo.objects.get_or_create(codigo=cod, defaults={"nombre": nom})
            self.plan.modulos.add(mod)

        self.empresa = Empresa.objects.create(
            razon_social="Comercializadora Andina SAS",
            nit="901555666",
            email="gerencia@andinasas.co",
            plan=self.plan,
        )
        self.empresa.sincronizar_modulos_con_plan(self.plan)

        self.superadmin = User.objects.create_user(
            username="sa_notifs",
            email="sa_alertas@servigest.online",
            password="SafePass!2026",
            is_superadmin=True,
            is_superuser=True,
            is_staff=True,
        )
        self.propietario = User.objects.create_user(
            username="prop@andinasas.co",
            email="prop@andinasas.co",
            password="SafePass!2026",
            first_name="Laura",
        )
        self.cajero = User.objects.create_user(
            username="cajero@andinasas.co",
            email="cajero@andinasas.co",
            password="SafePass!2026",
            first_name="Mateo",
        )
        self.contador = User.objects.create_user(
            username="contador@andinasas.co",
            email="contador@andinasas.co",
            password="SafePass!2026",
            first_name="Sofía",
        )
        EmpresaUsuario.objects.create(user=self.propietario, empresa=self.empresa, rol="propietario", activo=True)
        EmpresaUsuario.objects.create(user=self.cajero, empresa=self.empresa, rol="vendedor", activo=True)
        EmpresaUsuario.objects.create(user=self.contador, empresa=self.empresa, rol="contador", activo=True)

        self.cat = Categoria.objects.create(empresa=self.empresa, nombre="General")
        self.uni = UnidadMedida.objects.create(empresa=self.empresa, codigo="UND", nombre="Unidad")
        self.prod = Producto.objects.create(
            empresa=self.empresa,
            codigo="P-001",
            nombre="Café Molido 500g",
            categoria=self.cat,
            unidad_medida=self.uni,
            precio_venta=Decimal("18000.00"),
            costo_unitario=Decimal("10000.00"),
            maneja_inventario=True,
            stock_actual=Decimal("10.00"),
            stock_minimo=Decimal("4.00"),
        )
        self.cliente = Tercero.objects.create(
            empresa=self.empresa,
            numero_documento="10203040",
            razon_social="Cliente Demo Real",
            email="cliente.real@andinasas.co",
            es_cliente=True,
        )

    def test_campanita_in_app_contexto_aislamiento_y_marcar_leida(self):
        notificar_usuarios_app(
            "Alerta de prueba",
            "Revisión pendiente en configuración.",
            empresa=self.empresa,
            roles=("propietario",),
            nivel="warning",
            categoria="sistema",
            url="/configuracion/",
        )
        n = Notificacion.objects.get(usuario=self.propietario, titulo="Alerta de prueba")

        c_prop = Client()
        c_prop.force_login(self.propietario)
        r_dash = c_prop.get(reverse("core:dashboard"))
        self.assertEqual(r_dash.status_code, 200)
        self.assertEqual(r_dash.context["notificaciones_no_leidas_count"], 1)
        self.assertContains(r_dash, "Alerta de prueba")

        # Otro usuario no puede marcar como leída una notificación ajena
        c_otro = Client()
        c_otro.force_login(self.cajero)
        r_404 = c_otro.post(reverse("core:notificacion_marcar_leida", args=[n.id]))
        self.assertEqual(r_404.status_code, 404)

        # El propietario la marca leída y redirige a la URL local asociada
        r_leer = c_prop.post(reverse("core:notificacion_marcar_leida", args=[n.id]))
        self.assertEqual(r_leer.status_code, 302)
        self.assertEqual(r_leer["Location"], "/configuracion/")
        n.refresh_from_db()
        self.assertTrue(n.leida)

        # Marcar todas leídas limpia múltiples pendientes
        notificar_usuarios_app("N1", "M1", empresa=self.empresa, roles=("propietario",))
        notificar_usuarios_app("N2", "M2", empresa=self.empresa, roles=("propietario",))
        self.assertEqual(Notificacion.objects.filter(usuario=self.propietario, leida=False).count(), 2)
        c_prop.post(reverse("core:notificaciones_marcar_todas_leidas"))
        self.assertEqual(Notificacion.objects.filter(usuario=self.propietario, leida=False).count(), 0)

    def test_wompi_factura_anomalias_y_suscripcion_sin_clave_plana(self):
        # Configurar Wompi propio para empresa y Wompi de plataforma
        self.empresa.wompi_public_key = "pub_test_emp"
        self.empresa.wompi_private_key = encrypt_secret("prv_test_emp")
        self.empresa.wompi_integrity_secret = encrypt_secret("integ_emp")
        self.empresa.wompi_events_secret = encrypt_secret("events_emp")
        self.empresa.save()

        set_platform_setting("wompi_active_flag", "1")
        set_platform_setting("wompi_public_key", "pub_test_plat")
        set_platform_setting("wompi_events_secret", "plat_events_secret", is_secret=True)

        fac = Factura.objects.create(
            empresa=self.empresa,
            tipo_documento="FACTURA",
            prefijo="FE",
            consecutivo=101,
            tercero=self.cliente,
            fecha_emision=date.today(),
            fecha_vencimiento=date.today(),
            estado="EMITIDA",
            metodo_pago="CREDITO",
        )
        FacturaItem.objects.create(
            factura=fac,
            producto=self.prod,
            descripcion="Café",
            cantidad=Decimal("2"),
            precio_unitario=Decimal("18000.00"),
            porcentaje_iva=Decimal("0.00"),
        )
        fac.recalcular_totales()

        tx_fac = TransaccionWompi.objects.create(
            empresa=self.empresa,
            factura=fac,
            reference=f"FAC-TEST-{fac.id}",
            amount_in_cents=3600000,
            currency="COP",
            customer_email=self.cliente.email,
        )
        anon = Client()
        mail.outbox.clear()
        payload_ok = {
            "event": "transaction.updated",
            "environment": "test",
            "timestamp": 1732200000,
            "data": {
                "transaction": {
                    "id": "wompi-ok-1",
                    "reference": tx_fac.reference,
                    "status": "APPROVED",
                    "amount_in_cents": 3600000,
                    "currency": "COP",
                    "payment_method_type": "CARD",
                }
            },
            "signature": {
                "properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
                "checksum": hashlib.sha256(b"wompi-ok-1APPROVED36000001732200000events_emp").hexdigest(),
            },
        }
        r_ok = anon.post(reverse("facturacion:wompi_webhook"), data=json.dumps(payload_ok), content_type="application/json")
        self.assertEqual(r_ok.status_code, 200)
        dests_ok = [addr for m in mail.outbox for addr in m.to]
        self.assertIn("cliente.real@andinasas.co", dests_ok)
        self.assertIn("prop@andinasas.co", dests_ok)
        self.assertIn("gerencia@andinasas.co", dests_ok)
        self.assertTrue(Notificacion.objects.filter(empresa=self.empresa, categoria="wompi", usuario=self.propietario).exists())

        # Webhook VOIDED posterior alerta sobre anulación
        mail.outbox.clear()
        payload_void = {
            "event": "transaction.updated",
            "environment": "test",
            "timestamp": 1732200050,
            "data": {
                "transaction": {
                    "id": "wompi-ok-1",
                    "reference": tx_fac.reference,
                    "status": "VOIDED",
                    "amount_in_cents": 3600000,
                    "currency": "COP",
                }
            },
            "signature": {
                "properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
                "checksum": hashlib.sha256(b"wompi-ok-1VOIDED36000001732200050events_emp").hexdigest(),
            },
        }
        anon.post(reverse("facturacion:wompi_webhook"), data=json.dumps(payload_void), content_type="application/json")
        self.assertIn("prop@andinasas.co", [addr for m in mail.outbox for addr in m.to])

        # Webhook AMOUNT_MISMATCH alerta anomalía
        tx_bad = TransaccionWompi.objects.create(
            empresa=self.empresa,
            factura=fac,
            reference="FAC-MISMATCH-1",
            amount_in_cents=3600000,
            currency="COP",
        )
        mail.outbox.clear()
        payload_bad = {
            "event": "transaction.updated",
            "environment": "test",
            "timestamp": 1732200090,
            "data": {
                "transaction": {
                    "id": "wompi-bad-1",
                    "reference": tx_bad.reference,
                    "status": "APPROVED",
                    "amount_in_cents": 100,
                    "currency": "COP",
                }
            },
            "signature": {
                "properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
                "checksum": hashlib.sha256(b"wompi-bad-1APPROVED1001732200090events_emp").hexdigest(),
            },
        }
        anon.post(reverse("facturacion:wompi_webhook"), data=json.dumps(payload_bad), content_type="application/json")
        self.assertTrue(Notificacion.objects.filter(empresa=self.empresa, categoria="wompi", nivel="danger").exists())

        # Suscripción SaaS nueva por Wompi envía enlace de activación (NO clave en texto plano) y avisa al SuperAdmin
        tx_sub = TransaccionWompi.objects.create(
            plan=self.plan,
            reference="SAAS-NEW-99",
            amount_in_cents=9900000,
            currency="COP",
            customer_email="nuevo.saas@cliente.co",
            customer_name="Empresa SaaS Nueva",
            customer_nit="901777888",
        )
        mail.outbox.clear()
        payload_sub = {
            "event": "transaction.updated",
            "environment": "test",
            "timestamp": 1732200120,
            "data": {
                "transaction": {
                    "id": "wompi-sub-99",
                    "reference": tx_sub.reference,
                    "status": "APPROVED",
                    "amount_in_cents": 9900000,
                    "currency": "COP",
                    "payment_method_type": "PSE",
                }
            },
            "signature": {
                "properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
                "checksum": hashlib.sha256(b"wompi-sub-99APPROVED99000001732200120plat_events_secret").hexdigest(),
            },
        }
        anon.post(reverse("facturacion:wompi_webhook"), data=json.dumps(payload_sub), content_type="application/json")
        correos_cliente = [m for m in mail.outbox if m.to == ["nuevo.saas@cliente.co"]]
        self.assertEqual(len(correos_cliente), 1)
        self.assertIn("/auth/password-reset/", correos_cliente[0].body)
        self.assertNotIn("Contraseña temporal", correos_cliente[0].body)
        self.assertIn("sa_alertas@servigest.online", [addr for m in mail.outbox for addr in m.to])
        self.assertTrue(Notificacion.objects.filter(usuario=self.superadmin, categoria="wompi").exists())

    def test_dian_contingencia_y_rechazo_alerta_y_deduplica(self):
        fac1 = Factura.objects.create(
            empresa=self.empresa, tipo_documento="FACTURA", prefijo="FE", consecutivo=201,
            tercero=self.cliente, fecha_emision=date.today(), fecha_vencimiento=date.today(),
            estado="EMITIDA", metodo_pago="CONTADO",
        )
        fac2 = Factura.objects.create(
            empresa=self.empresa, tipo_documento="FACTURA", prefijo="FE", consecutivo=202,
            tercero=self.cliente, fecha_emision=date.today(), fecha_vencimiento=date.today(),
            estado="EMITIDA", metodo_pago="CONTADO",
        )
        mail.outbox.clear()
        transmitir_documento_dian(fac1)
        self.assertEqual(fac1.estado_dian, "CONTINGENCIA")
        self.assertIn("prop@andinasas.co", [addr for m in mail.outbox for addr in m.to])
        self.assertIn("contador@andinasas.co", [addr for m in mail.outbox for addr in m.to])
        count_antes = Notificacion.objects.filter(empresa=self.empresa, categoria="dian").count()
        self.assertGreaterEqual(count_antes, 1)

        # Segunda falla idéntica en menos de 30 minutos no duplica correos ni notificaciones
        mail.outbox.clear()
        transmitir_documento_dian(fac2)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Notificacion.objects.filter(empresa=self.empresa, categoria="dian").count(), count_antes)

        # Rechazo explícito HTTP de Proveedor Tecnológico sí alerta con nivel danger
        self.empresa.resolucion_dian = "18764000"
        self.empresa.dian_software_id = "SW-ID"
        self.empresa.dian_pin = "12345"
        self.empresa.dian_test_set_id = "TS-1"
        self.empresa.save()
        set_platform_setting("pt_dian_url", "https://pt.dian.mock/transmitir")
        fac1.generar_cufe()

        class _FakeResp:
            status_code = 422
            headers = {"Content-Type": "application/json"}
            def json(self):
                return {"error": "Regla FAD09 incumplida"}

        mail.outbox.clear()
        with patch("requests.post", return_value=_FakeResp()):
            ok, msg = transmitir_documento_dian(fac1)
        self.assertFalse(ok)
        self.assertEqual(fac1.estado_dian, "RECHAZADA")
        self.assertTrue(Notificacion.objects.filter(empresa=self.empresa, categoria="dian", nivel="danger").exists())
        self.assertIn("prop@andinasas.co", [addr for m in mail.outbox for addr in m.to])

    def test_nota_credito_alerta_propietario_y_copia_cliente(self):
        fac = Factura.objects.create(
            empresa=self.empresa, tipo_documento="FACTURA", prefijo="FE", consecutivo=301,
            tercero=self.cliente, fecha_emision=date.today(), fecha_vencimiento=date.today(),
            estado="EMITIDA", metodo_pago="CONTADO",
        )
        FacturaItem.objects.create(
            factura=fac, producto=self.prod, descripcion="Café",
            cantidad=Decimal("1"), precio_unitario=Decimal("18000.00"), porcentaje_iva=Decimal("0.00"),
        )
        fac.recalcular_totales()

        c_caj = Client()
        c_caj.force_login(self.cajero)
        mail.outbox.clear()
        r = c_caj.post(reverse("facturacion:factura_emitir_nota_credito", args=[fac.id]), {
            "motivo": "Error de digitación",
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        dests = [addr for m in mail.outbox for addr in m.to]
        self.assertIn("prop@andinasas.co", dests)
        self.assertIn("contador@andinasas.co", dests)
        self.assertIn("cliente.real@andinasas.co", dests)
        self.assertTrue(Notificacion.objects.filter(empresa=self.empresa, categoria="facturacion", nivel="warning").exists())

    def test_pos_descuadre_caja_stock_critico_y_cupo_plan(self):
        caja = Caja.objects.create(empresa=self.empresa, nombre="Caja 1", codigo="C1")
        turno = CajaTurno.objects.create(
            empresa=self.empresa, caja=caja, usuario=self.cajero, monto_inicial=Decimal("50000.00")
        )
        c_caj = Client()
        c_caj.force_login(self.cajero)

        # Emitir 4 ventas POS sobre límite 5 -> dispara ALERTA_CUPO_80 y stock crítico al bajar a <= 4
        mail.outbox.clear()
        for i in range(4):
            qty = 2 if i == 3 else 1  # stock: 10 -> 9 -> 8 -> 7 -> 5 (aún > 4)
            r = c_caj.post(
                reverse("pos:pos_procesar_venta"),
                data=json.dumps({
                    "tercero_id": self.cliente.id,
                    "metodo_pago": "EFECTIVO",
                    "tipo_documento": "POS_ELECTRONICO",
                    "items": [{"producto_id": self.prod.id, "cantidad": qty, "precio": 18000}],
                }),
                content_type="application/json",
            )
            self.assertEqual(r.status_code, 200)
        self.assertTrue(RegistroActividad.objects.filter(empresa=self.empresa, accion="ALERTA_CUPO_80").exists())
        self.assertIn("prop@andinasas.co", [addr for m in mail.outbox for addr in m.to])

        # 5ta venta (2 uds: stock 5 -> 3 <= min 4) dispara ALERTA_CUPO_100 y ALERTA_STOCK_MINIMO
        mail.outbox.clear()
        c_caj.post(
            reverse("pos:pos_procesar_venta"),
            data=json.dumps({
                "tercero_id": self.cliente.id,
                "metodo_pago": "EFECTIVO",
                "tipo_documento": "POS_ELECTRONICO",
                "items": [{"producto_id": self.prod.id, "cantidad": 2, "precio": 18000}],
            }),
            content_type="application/json",
        )
        self.assertTrue(RegistroActividad.objects.filter(empresa=self.empresa, accion="ALERTA_CUPO_100").exists())
        self.assertTrue(RegistroActividad.objects.filter(empresa=self.empresa, accion="ALERTA_STOCK_MINIMO", objeto_id=str(self.prod.id)).exists())

        # Cierre de caja con faltante alerta a propietario y en campanita
        mail.outbox.clear()
        c_caj.post(reverse("pos:caja_cerrar_turno", args=[turno.id]), {
            "monto_cierre_real": "100000",
            "observaciones": "Faltante por cambio mal entregado",
        }, follow=True)
        turno.refresh_from_db()
        self.assertLess(turno.diferencia, 0)
        self.assertIn("prop@andinasas.co", [addr for m in mail.outbox for addr in m.to])
        self.assertTrue(Notificacion.objects.filter(empresa=self.empresa, categoria="caja", nivel="warning").exists())

    def test_inventario_ajuste_salida_y_nomina_lote_colillas(self):
        c_prop = Client()
        c_prop.force_login(self.propietario)

        # Ajuste manual de SALIDA (merma)
        mail.outbox.clear()
        c_prop.post(reverse("inventario:ajuste_crear"), {
            "producto": str(self.prod.id),
            "tipo": "SALIDA",
            "cantidad": "7",
            "motivo": "Producto averiado en bodega",
        }, follow=True)
        self.assertTrue(Notificacion.objects.filter(empresa=self.empresa, categoria="inventario").exists())
        self.assertIn("gerencia@andinasas.co", [addr for m in mail.outbox for addr in m.to])

        # Liquidar nómina notifica a contador/empresa y envío masivo de colillas respeta correos válidos
        Empleado.objects.create(
            empresa=self.empresa,
            numero_documento="10001",
            nombres="Juan",
            apellidos="Valdez",
            email="juan.valdez@andinasas.co",
            cargo="Vendedor",
            salario_base=Decimal("1600000.00"),
            fecha_ingreso=date(2025, 1, 15),
        )
        Empleado.objects.create(
            empresa=self.empresa,
            numero_documento="10002",
            nombres="Sin",
            apellidos="Correo",
            email="",
            cargo="Operario",
            salario_base=Decimal("1500000.00"),
            fecha_ingreso=date(2025, 2, 1),
        )
        mail.outbox.clear()
        c_prop.post(reverse("nomina:nomina_liquidar"), {"mes": "11", "ano": "2026"}, follow=True)
        nom = Nomina.objects.get(empresa=self.empresa, periodo_mes=11, periodo_ano=2026)
        self.assertIn("contador@andinasas.co", [addr for m in mail.outbox for addr in m.to])
        self.assertTrue(Notificacion.objects.filter(empresa=self.empresa, categoria="nomina", usuario=self.contador).exists())

        mail.outbox.clear()
        c_prop.post(reverse("nomina:nomina_enviar_todas_colillas", args=[nom.id]), follow=True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["juan.valdez@andinasas.co"])
        self.assertTrue(RegistroActividad.objects.filter(empresa=self.empresa, accion="ENVIAR_COLILLAS_LOTE").exists())

    def test_recibo_manual_recordatorio_fiado_y_autoenvio_pdf_factura(self):
        c_prop = Client()
        c_prop.force_login(self.propietario)

        # Emitir factura con envío automático de PDF al correo del cliente
        mail.outbox.clear()
        r_fac = c_prop.post(reverse("facturacion:factura_crear"), {
            "tercero": str(self.cliente.id),
            "tipo_documento": "FACTURA",
            "metodo_pago": "CREDITO",
            "enviar_email_cliente": "on",
            "item_producto[]": [str(self.prod.id)],
            "item_cantidad[]": ["1"],
            "item_precio[]": ["18000"],
        }, follow=True)
        self.assertEqual(r_fac.status_code, 200)
        fac = Factura.objects.filter(empresa=self.empresa, tipo_documento="FACTURA").latest("id")
        correos_cli = [m for m in mail.outbox if m.to == ["cliente.real@andinasas.co"]]
        self.assertEqual(len(correos_cli), 1)
        self.assertTrue(any(att[0].endswith(".pdf") for att in correos_cli[0].attachments))

        # Recordatorio de fiado/cartera pendiente al correo del cliente
        mail.outbox.clear()
        r_rec = c_prop.post(reverse("facturacion:fiado_enviar_recordatorio", args=[fac.id]), follow=True)
        self.assertEqual(r_rec.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["cliente.real@andinasas.co"])
        self.assertTrue(RegistroActividad.objects.filter(empresa=self.empresa, accion="RECORDATORIO_CARTERA").exists())

        # Recibo de caja manual envía comprobante al cliente y registra notificación in-app
        mail.outbox.clear()
        r_rc = c_prop.post(reverse("facturacion:recibo_crear"), {
            "factura": str(fac.id),
            "valor": "18000",
            "metodo_pago": "NEQUI",
        }, follow=True)
        self.assertEqual(r_rc.status_code, 200)
        self.assertIn("cliente.real@andinasas.co", [addr for m in mail.outbox for addr in m.to])
        self.assertTrue(Notificacion.objects.filter(empresa=self.empresa, categoria="facturacion", titulo__startswith="Recibo de Caja").exists())

    def test_historial_notificaciones_preferencias_correo_y_webhook_ssrf(self):
        c_prop = Client()
        c_prop.force_login(self.propietario)

        # Desactivar correos de inventario en perfil, manteniendo seguridad obligatoria
        c_prop.post(reverse("usuarios:perfil"), {
            "first_name": "Laura",
            "last_name": "Andina",
            "telefono": "3000001122",
            "tema": "oscuro",
            "actualizar_preferencias": "1",
            "pref_email_wompi": "on",
            # pref_email_inventario omitido -> desactivado
        }, follow=True)

        # Bloquear URL interna SSRF y guardar URL pública HTTPS válida
        c_prop.post(reverse("core:configuracion_empresa"), {
            "seccion": "smtp",
            "webhook_notificaciones_url": "http://127.0.0.1:8080/hook",
        }, follow=True)
        self.empresa.refresh_from_db()
        self.assertEqual(self.empresa.webhook_notificaciones_url, "")

        c_prop.post(reverse("core:configuracion_empresa"), {
            "seccion": "smtp",
            "webhook_notificaciones_url": "https://hooks.slack.com/services/T000/B000/ABC",
        }, follow=True)
        self.empresa.refresh_from_db()
        self.assertEqual(self.empresa.webhook_notificaciones_url, "https://hooks.slack.com/services/T000/B000/ABC")

        # Ajuste de inventario SALIDA: usuario no recibe correo por preferencia, pero sí in-app y webhook
        mail.outbox.clear()
        with patch("apps.core.notifications.httpx.post") as mock_wh:
            c_prop.post(reverse("inventario:ajuste_crear"), {
                "producto": str(self.prod.id),
                "tipo": "SALIDA",
                "cantidad": "1",
                "motivo": "Muestra comercial",
            }, follow=True)
            self.assertTrue(mock_wh.called)
        dests = [addr for m in mail.outbox for addr in m.to]
        self.assertNotIn("prop@andinasas.co", dests)
        self.assertIn("gerencia@andinasas.co", dests)

        # Historial paginado y filtrable de notificaciones
        r_hist = c_prop.get(reverse("core:notificaciones_lista"), {"categoria": "inventario", "estado": "no_leidas"})
        self.assertEqual(r_hist.status_code, 200)
        self.assertContains(r_hist, "Ajuste manual de salida")

    def test_alerta_configuracion_plataforma_y_compra_dse(self):
        c_sa = Client()
        c_sa.force_login(self.superadmin)
        mail.outbox.clear()
        c_sa.post(reverse("backoffice:configuracion_plataforma"), {
            "seccion": "ia",
            "gemini_api_key": "AIzaFakeTestKey",
        }, follow=True)
        self.assertIn("sa_alertas@servigest.online", [addr for m in mail.outbox for addr in m.to])
        self.assertTrue(Notificacion.objects.filter(usuario=self.superadmin, categoria="seguridad").exists())

        # Compra marcada como Documento Soporte Electrónico alerta a propietario y contador
        prov = Tercero.objects.create(
            empresa=self.empresa, numero_documento="800111222", razon_social="Proveedor DSE SAS",
            es_cliente=False, es_proveedor=True,
        )
        c_prop = Client()
        c_prop.force_login(self.propietario)
        mail.outbox.clear()
        c_prop.post(reverse("inventario:compra_crear"), {
            "proveedor": str(prov.id),
            "numero_factura_proveedor": "FAC-PROV-9",
            "metodo_pago": "CONTADO",
            "es_documento_soporte": "on",
            "item_producto[]": [str(self.prod.id)],
            "item_cantidad[]": ["2"],
            "item_factor[]": ["1"],
            "item_costo[]": ["9000"],
        }, follow=True)
        self.assertIn("contador@andinasas.co", [addr for m in mail.outbox for addr in m.to])
        self.assertTrue(Notificacion.objects.filter(empresa=self.empresa, categoria="dian", usuario=self.contador).exists())

    def test_comando_resumen_diario_y_cola_dian(self):
        # Factura vencida ayer con saldo pendiente
        Factura.objects.create(
            empresa=self.empresa, tipo_documento="FACTURA", prefijo="FE", consecutivo=901,
            tercero=self.cliente, fecha_emision=date.today() - timedelta(days=10),
            fecha_vencimiento=date.today() - timedelta(days=1),
            estado="EMITIDA", metodo_pago="CREDITO", total=Decimal("50000.00"), saldo_pendiente=Decimal("50000.00"),
        )
        mail.outbox.clear()
        out_res = StringIO()
        call_command("enviar_resumen_diario", empresa_id=self.empresa.id, stdout=out_res)
        self.assertIn("Resúmenes diarios procesados y enviados: 1", out_res.getvalue())
        self.assertIn("prop@andinasas.co", [addr for m in mail.outbox for addr in m.to])

        out_dian = StringIO()
        call_command("procesar_cola_dian", stdout=out_dian)
        self.assertIn("Cola DIAN procesada:", out_dian.getvalue())

    def test_movimiento_caja_retiro_descuenta_esperado_y_alerta_propietario(self):
        caja = Caja.objects.create(empresa=self.empresa, nombre="Caja Mov", codigo="CM1")
        turno = CajaTurno.objects.create(
            empresa=self.empresa,
            caja=caja,
            usuario=self.cajero,
            monto_inicial=Decimal("100000.00"),
            monto_total_esperado=Decimal("100000.00"),
        )
        c_caj = Client()
        c_caj.force_login(self.cajero)

        mail.outbox.clear()
        r_egr = c_caj.post(reverse("pos:caja_movimiento_registrar", args=[turno.id]), {
            "tipo": "EGRESO",
            "monto": "25000",
            "motivo": "Pago proveedor domicilio",
        }, follow=True)
        self.assertEqual(r_egr.status_code, 200)
        turno.refresh_from_db()
        self.assertEqual(turno.monto_total_esperado, Decimal("75000.00"))
        self.assertIn("prop@andinasas.co", [addr for m in mail.outbox for addr in m.to])
        self.assertTrue(Notificacion.objects.filter(empresa=self.empresa, categoria="caja", nivel="warning", titulo__startswith="Retiro de efectivo").exists())

        # INGRESO suma al esperado y no genera alerta de retiro
        mail.outbox.clear()
        c_caj.post(reverse("pos:caja_movimiento_registrar", args=[turno.id]), {
            "tipo": "INGRESO",
            "monto": "10000",
            "motivo": "Sencillo adicional",
        }, follow=True)
        turno.refresh_from_db()
        self.assertEqual(turno.monto_total_esperado, Decimal("85000.00"))
        self.assertEqual(len(mail.outbox), 0)

    def test_recordatorio_masivo_cartera_vencida(self):
        otro_cli = Tercero.objects.create(
            empresa=self.empresa, numero_documento="555111222", razon_social="Cliente Al Día",
            email="aldia@andinasas.co", es_cliente=True,
        )
        Factura.objects.create(
            empresa=self.empresa, tipo_documento="FACTURA", prefijo="FE", consecutivo=951,
            tercero=self.cliente, fecha_emision=date.today() - timedelta(days=15),
            fecha_vencimiento=date.today() - timedelta(days=3),
            estado="EMITIDA", metodo_pago="CREDITO", total=Decimal("40000.00"), saldo_pendiente=Decimal("40000.00"),
        )
        # Al día (no vencida) -> no se incluye cuando solo_vencidos=1
        Factura.objects.create(
            empresa=self.empresa, tipo_documento="FACTURA", prefijo="FE", consecutivo=952,
            tercero=otro_cli, fecha_emision=date.today(),
            fecha_vencimiento=date.today() + timedelta(days=10),
            estado="EMITIDA", metodo_pago="CREDITO", total=Decimal("30000.00"), saldo_pendiente=Decimal("30000.00"),
        )
        c_prop = Client()
        c_prop.force_login(self.propietario)
        mail.outbox.clear()
        r = c_prop.post(reverse("facturacion:fiados_recordar_lote"), {"solo_vencidos": "1"}, follow=True)
        self.assertEqual(r.status_code, 200)
        dests = [addr for m in mail.outbox for addr in m.to]
        self.assertEqual(dests, ["cliente.real@andinasas.co"])
        self.assertTrue(RegistroActividad.objects.filter(empresa=self.empresa, accion="RECORDATORIO_CARTERA_LOTE").exists())

    def test_vencimientos_plan_cupo_usuarios_y_tareas_backoffice(self):
        # 7 días o menos -> alerta warning
        self.empresa.plan_fecha_vencimiento = date.today() + timedelta(days=5)
        self.empresa.save(update_fields=["plan_fecha_vencimiento"])
        mail.outbox.clear()
        call_command("verificar_vencimientos_plan", empresa_id=self.empresa.id)
        self.assertTrue(RegistroActividad.objects.filter(empresa=self.empresa, accion="ALERTA_VENCIMIENTO_PLAN_7D").exists())
        self.assertIn("prop@andinasas.co", [addr for m in mail.outbox for addr in m.to])

        # Plan vencido -> alerta danger + notifica SuperAdmin in-app
        self.empresa.plan_fecha_vencimiento = date.today() - timedelta(days=2)
        self.empresa.save(update_fields=["plan_fecha_vencimiento"])
        mail.outbox.clear()
        call_command("verificar_vencimientos_plan", empresa_id=self.empresa.id)
        self.assertTrue(RegistroActividad.objects.filter(empresa=self.empresa, accion="ALERTA_PLAN_VENCIDO").exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.superadmin, categoria="plan", nivel="danger").exists())

        # Límite de usuarios alcanzado dispara alerta mensual ALERTA_CUPO_USUARIOS
        self.plan.limite_usuarios = 3  # Ya existen propietario, cajero, contador (3)
        self.plan.save(update_fields=["limite_usuarios"])
        c_prop = Client()
        c_prop.force_login(self.propietario)
        mail.outbox.clear()
        c_prop.post(reverse("usuarios:crear"), {
            "email": "extra@andinasas.co",
            "first_name": "Cuarto",
            "rol": "vendedor",
        }, follow=True)
        self.assertTrue(RegistroActividad.objects.filter(empresa=self.empresa, accion="ALERTA_CUPO_USUARIOS").exists())
        self.assertIn("prop@andinasas.co", [addr for m in mail.outbox for addr in m.to])

        # Backoffice ejecutar_tarea_programada
        c_sa = Client()
        c_sa.force_login(self.superadmin)
        r_cron = c_sa.post(reverse("backoffice:ejecutar_tarea_programada"), {"tarea": "verificar_vencimientos_plan"}, follow=True)
        self.assertEqual(r_cron.status_code, 200)
        self.assertTrue(RegistroActividad.objects.filter(accion="CRON_MANUAL_VERIFICAR_VENCIMIENTOS_PLAN").exists())


