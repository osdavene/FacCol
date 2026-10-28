from decimal import Decimal
from django.db import models
from apps.core.models import EmpresaTenantModel


class PlanCuenta(EmpresaTenantModel):
    NATURALEZA_CHOICES = [
        ('DEBITO', 'Débito'),
        ('CREDITO', 'Crédito'),
    ]

    codigo = models.CharField(max_length=20, verbose_name="Código de Cuenta (PUC)")
    nombre = models.CharField(max_length=150, verbose_name="Nombre de la Cuenta")
    nivel = models.IntegerField(default=1, help_text="1: Clase, 2: Grupo, 4: Cuenta, 6: Subcuenta, 8+: Auxiliar")
    naturaleza = models.CharField(max_length=10, choices=NATURALEZA_CHOICES, default='DEBITO')
    permite_movimiento = models.BooleanField(default=True, verbose_name="¿Es cuenta de movimiento (asiento)?")
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Cuenta Contable (PUC)"
        verbose_name_plural = "Plan de Cuentas (PUC Colombia)"
        ordering = ['codigo']
        unique_together = ('empresa', 'codigo')

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


class AsientoContable(EmpresaTenantModel):
    TIPO_COMPROBANTE_CHOICES = [
        ('INGRESO', 'Comprobante de Ingreso'),
        ('EGRESO', 'Comprobante de Egreso'),
        ('VENTA', 'Asiento de Ventas'),
        ('COMPRA', 'Asiento de Compras'),
        ('NOMINA', 'Asiento de Nómina'),
        ('GENERAL', 'Comprobante de Diario / General'),
    ]

    numero_comprobante = models.CharField(max_length=50, verbose_name="No. Comprobante")
    fecha = models.DateField(verbose_name="Fecha Contable")
    tipo = models.CharField(max_length=30, choices=TIPO_COMPROBANTE_CHOICES, default='GENERAL')
    concepto = models.CharField(max_length=255, verbose_name="Concepto / Detalle")
    referencia_origen = models.CharField(max_length=100, blank=True, verbose_name="Documento Origen (e.g. FE-101)")

    class Meta:
        verbose_name = "Asiento Contable"
        verbose_name_plural = "Asientos Contables"
        ordering = ['-fecha', '-numero_comprobante']

    def __str__(self):
        return f"{self.numero_comprobante} - {self.concepto} ({self.fecha})"

    @property
    def total_debito(self):
        return self.lineas.aggregate(total=models.Sum('debito'))['total'] or Decimal('0.00')

    @property
    def total_credito(self):
        return self.lineas.aggregate(total=models.Sum('credito'))['total'] or Decimal('0.00')

    @property
    def esta_cuadrado(self):
        return abs(self.total_debito - self.total_credito) < Decimal('0.01')


class AsientoLinea(models.Model):
    asiento = models.ForeignKey(AsientoContable, on_delete=models.CASCADE, related_name='lineas')
    cuenta = models.ForeignKey(PlanCuenta, on_delete=models.PROTECT, related_name='movimientos')
    tercero = models.ForeignKey('terceros.Tercero', on_delete=models.SET_NULL, null=True, blank=True)
    descripcion = models.CharField(max_length=255, blank=True)
    debito = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    credito = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))

    def __str__(self):
        return f"{self.cuenta.codigo}: Débito ${self.debito} / Crédito ${self.credito}"
