from django.core.management.base import BaseCommand
from apps.facturacion.models import Factura
from apps.facturacion.dian_service import generar_xml_ubl


class Command(BaseCommand):
    help = "Procesa documentos electrónicos pendientes o en contingencia para transmisión DIAN."

    def handle(self, *args, **options):
        pendientes = Factura.objects.filter(
            estado_dian__in=("PENDIENTE", "CONTINGENCIA"),
            tipo_documento__in=("FACTURA", "POS_ELECTRONICO", "NOTA_CREDITO"),
        ).select_related("empresa", "tercero")

        total = pendientes.count()
        preparados = 0
        sin_config = 0

        for fac in pendientes:
            if not fac.cufe:
                fac.generar_cufe()
            if fac.empresa.dian_software_id and fac.empresa.resolucion_dian:
                generar_xml_ubl(fac)
                preparados += 1
            else:
                sin_config += 1

        self.stdout.write(
            f"Cola DIAN revisada: {total} documentos | {preparados} con XML UBL 2.1 listo | {sin_config} esperando configuración DIAN."
        )
