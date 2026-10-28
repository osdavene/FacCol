from decimal import Decimal
from django.db import models
from apps.core.models import EmpresaTenantModel


class Caja(EmpresaTenantModel):
    nombre = models.CharField(max_length=100, verbose_name="Nombre de la Caja (e.g. Caja Principal, Caja 2)")
    codigo = models.CharField(max_length=20, verbose_name="Código Identificador")
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Caja Registradora"
        verbose_name_plural = "Cajas Registradoras"
        unique_together = ('empresa', 'codigo')

    def __str__(self):
        return f"{self.nombre} ({self.codigo})"


class CajaTurno(EmpresaTenantModel):
    ESTADO_CHOICES = [
        ('ABIERTA', 'Turno Abierto'),
        ('CERRADA', 'Turno Cerrado'),
    ]

    caja = models.ForeignKey(Caja, on_delete=models.CASCADE, related_name='turnos')
    usuario = models.ForeignKey('usuarios.User', on_delete=models.PROTECT, related_name='turnos_caja')
    fecha_apertura = models.DateTimeField(auto_now_add=True)
    fecha_cierre = models.DateTimeField(null=True, blank=True)

    monto_inicial = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), verbose_name="Base Inicial en Efectivo")
    monto_ventas_efectivo = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    monto_ventas_otros = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    monto_total_esperado = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    monto_cierre_real = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="Arqueo / Conteo Real de Cierre")
    diferencia = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='ABIERTA')
    observaciones = models.TextField(blank=True)

    class Meta:
        verbose_name = "Turno de Caja"
        verbose_name_plural = "Turnos de Caja"
        ordering = ['-fecha_apertura']

    def __str__(self):
        return f"{self.caja.nombre} - {self.usuario.username} ({self.estado})"


class MovimientoCaja(EmpresaTenantModel):
    TIPO_CHOICES = [
        ('INGRESO', 'Ingreso de Efectivo'),
        ('EGRESO', 'Egreso / Retiro de Efectivo'),
    ]

    turno = models.ForeignKey(CajaTurno, on_delete=models.CASCADE, related_name='movimientos')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    motivo = models.CharField(max_length=255, verbose_name="Concepto / Justificación")
    referencia = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = "Movimiento de Caja"
        verbose_name_plural = "Movimientos de Caja"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.tipo}: ${self.monto} - {self.motivo}"
