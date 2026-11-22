from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.urls import reverse

from apps.core.audit import registrar_actividad
from apps.core.config import mail_settings
from apps.core.models import RegistroActividad
from apps.core.notifications import (
    destinatarios_propietarios_empresa,
    notificar_evento_seguridad,
    notificar_usuarios_app,
)
from apps.empresas.models import Empresa


class Command(BaseCommand):
    help = "Verifica suscripciones próximas a vencer (7 días / 1 día) o vencidas y envía alertas."

    def add_arguments(self, parser):
        parser.add_argument("--empresa-id", type=int, default=None, help="Evaluar únicamente una empresa específica.")

    def handle(self, *args, **options):
        hoy = date.today()
        qs = Empresa.objects.filter(
            activa=True,
            plan__isnull=False,
            plan_fecha_vencimiento__isnull=False,
        ).select_related("plan")
        if options.get("empresa_id"):
            qs = qs.filter(id=options["empresa_id"])

        app_base = (mail_settings.APP_URL or "").rstrip("/")
        url_plan = reverse("core:mi_plan")
        action_url = f"{app_base}{url_plan}" if app_base else url_plan
        alertas = 0

        for emp in qs:
            dias = (emp.plan_fecha_vencimiento - hoy).days
            if dias < -15 or dias > 7:
                continue

            if dias < 0:
                accion = "ALERTA_PLAN_VENCIDO"
                nivel = "danger"
                titulo = f"Suscripción vencida: {emp.plan.nombre}"
                resumen = (
                    f"El plan '{emp.plan.nombre}' de {emp.display_name} venció el "
                    f"{emp.plan_fecha_vencimiento.strftime('%d/%m/%Y')}. Renueva para evitar interrupciones."
                )
            elif dias <= 1:
                accion = "ALERTA_VENCIMIENTO_PLAN_1D"
                nivel = "danger"
                etiqueta_dia = "hoy" if dias == 0 else "mañana"
                titulo = f"Tu plan {emp.plan.nombre} vence {etiqueta_dia}"
                resumen = (
                    f"El plan '{emp.plan.nombre}' de {emp.display_name} vence el "
                    f"{emp.plan_fecha_vencimiento.strftime('%d/%m/%Y')}."
                )
            else:
                accion = "ALERTA_VENCIMIENTO_PLAN_7D"
                nivel = "warning"
                titulo = f"Tu plan {emp.plan.nombre} vence en {dias} días"
                resumen = (
                    f"Faltan {dias} días para el vencimiento del plan '{emp.plan.nombre}' de "
                    f"{emp.display_name} ({emp.plan_fecha_vencimiento.strftime('%d/%m/%Y')})."
                )

            ya_enviada = RegistroActividad.objects.filter(
                empresa=emp,
                accion=accion,
                created_at__date__gte=hoy - timedelta(days=7),
            ).exists()
            if ya_enviada:
                continue

            registrar_actividad(None, "planes", accion, resumen, empresa=emp, objeto=emp)
            notificar_usuarios_app(
                titulo,
                resumen,
                empresa=emp,
                roles=("propietario", "admin"),
                superadmins=(dias < 0),
                nivel=nivel,
                categoria="plan",
                url=url_plan,
            )
            notificar_evento_seguridad(
                None,
                titulo,
                resumen,
                destinatarios=destinatarios_propietarios_empresa(emp),
                empresa=emp,
                action_url=action_url,
                action_text="Renovar Mi Plan",
                categoria="plan",
                nivel=nivel,
                registrar_in_app=False,
            )
            alertas += 1

        self.stdout.write(f"Vencimientos evaluados. Alertas enviadas: {alertas}.")
