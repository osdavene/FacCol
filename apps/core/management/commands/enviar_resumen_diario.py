from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import models
from django.db.models import Sum
from django.urls import reverse

from apps.core.config import mail_settings
from apps.core.notifications import (
    destinatarios_propietarios_empresa,
    notificar_evento_seguridad,
    notificar_usuarios_app,
)
from apps.empresas.models import Empresa
from apps.facturacion.models import Factura
from apps.inventario.models import Producto


class Command(BaseCommand):
    help = "Envía un único resumen diario consolidado por empresa (ventas, cartera vencida, stock crítico y pendientes DIAN)."

    def add_arguments(self, parser):
        parser.add_argument("--empresa-id", type=int, default=None, help="Procesar únicamente una empresa específica.")

    def handle(self, *args, **options):
        hoy = date.today()
        qs = Empresa.objects.filter(activa=True).order_by("id")
        if options.get("empresa_id"):
            qs = qs.filter(id=options["empresa_id"])

        app_base = (mail_settings.APP_URL or "").rstrip("/")
        dash_url = reverse("core:dashboard")
        resumenes_enviados = 0

        for emp in qs:
            ventas_hoy = (
                Factura.objects.filter(
                    empresa=emp,
                    tipo_documento__in=("FACTURA", "POS_ELECTRONICO"),
                    fecha_emision=hoy,
                )
                .exclude(estado="ANULADA")
                .aggregate(t=Sum("total"))["t"]
                or Decimal("0.00")
            )
            cartera_vencida_qs = Factura.objects.filter(
                empresa=emp,
                saldo_pendiente__gt=Decimal("0.00"),
                fecha_vencimiento__lt=hoy,
            ).exclude(estado="ANULADA")
            vencidas_cnt = cartera_vencida_qs.count()
            vencidas_val = cartera_vencida_qs.aggregate(t=Sum("saldo_pendiente"))["t"] or Decimal("0.00")

            stock_bajo_cnt = Producto.objects.filter(
                empresa=emp,
                maneja_inventario=True,
                activo=True,
                stock_minimo__gt=0,
                stock_actual__lte=models.F("stock_minimo"),
            ).count()

            dian_pend_cnt = Factura.objects.filter(
                empresa=emp,
                tipo_documento__in=("FACTURA", "POS_ELECTRONICO", "NOTA_CREDITO"),
                estado_dian__in=("PENDIENTE", "CONTINGENCIA", "RECHAZADA"),
            ).count()

            if ventas_hoy <= 0 and vencidas_cnt == 0 and stock_bajo_cnt == 0 and dian_pend_cnt == 0:
                continue

            dests = destinatarios_propietarios_empresa(emp)
            resumen_txt = (
                f"Resumen diario ({hoy.strftime('%d/%m/%Y')}) de {emp.display_name}: "
                f"Ventas hoy ${ventas_hoy:,.0f} COP | Cartera vencida: {vencidas_cnt} docs (${vencidas_val:,.0f} COP) | "
                f"Productos stock crítico: {stock_bajo_cnt} | Pendientes/Alertas DIAN: {dian_pend_cnt}."
            )
            nivel = "warning" if (vencidas_cnt > 0 or stock_bajo_cnt > 0 or dian_pend_cnt > 0) else "info"
            notificar_usuarios_app(
                f"Resumen diario {hoy.strftime('%d/%m/%Y')}",
                resumen_txt,
                empresa=emp,
                roles=("propietario", "admin"),
                nivel=nivel,
                categoria="facturacion",
                url=dash_url,
            )
            notificar_evento_seguridad(
                None,
                f"Resumen diario {emp.display_name} ({hoy.strftime('%d/%m/%Y')})",
                resumen_txt,
                destinatarios=dests,
                empresa=emp,
                action_url=f"{app_base}{dash_url}" if app_base else dash_url,
                action_text="Abrir Panel FacCol",
                categoria="facturacion",
                nivel=nivel,
                registrar_in_app=False,
            )
            resumenes_enviados += 1

        self.stdout.write(f"Resúmenes diarios procesados y enviados: {resumenes_enviados}.")
