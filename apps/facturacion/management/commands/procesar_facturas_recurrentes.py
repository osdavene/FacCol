from datetime import date
from django.core.management.base import BaseCommand
from apps.facturacion.models import FacturaRecurrente
from apps.facturacion.views import _ejecutar_contrato_recurrente


class Command(BaseCommand):
    help = "Emite facturas de contratos recurrentes vencidos a fecha de hoy."

    def handle(self, *args, **options):
        hoy = date.today()
        pendientes = FacturaRecurrente.objects.filter(activa=True, proxima_fecha__lte=hoy, empresa__activa=True)
        total = 0
        for c in pendientes:
            _ejecutar_contrato_recurrente(c)
            total += 1
        self.stdout.write(f"Facturas recurrentes generadas: {total}")
