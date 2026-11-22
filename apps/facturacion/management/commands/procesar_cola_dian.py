from django.core.management.base import BaseCommand
from apps.facturacion.models import Factura
from apps.facturacion.dian_service import transmitir_documento_dian


class Command(BaseCommand):
    help = "Transmite o reintenta documentos electrónicos pendientes o en contingencia ante la DIAN."

    def handle(self, *args, **options):
        pendientes = Factura.objects.filter(
            estado_dian__in=("PENDIENTE", "CONTINGENCIA"),
            tipo_documento__in=("FACTURA", "POS_ELECTRONICO", "NOTA_CREDITO"),
        ).select_related("empresa", "tercero").order_by("id")

        total = pendientes.count()
        enviados = 0
        contingencia = 0
        rechazados = 0

        for fac in pendientes:
            ok, _ = transmitir_documento_dian(fac)
            if ok:
                enviados += 1
            elif fac.estado_dian == "RECHAZADA":
                rechazados += 1
            else:
                contingencia += 1

        self.stdout.write(
            f"Cola DIAN procesada: {total} documentos | {enviados} enviados | {contingencia} contingencia | {rechazados} rechazados."
        )
