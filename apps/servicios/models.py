from decimal import Decimal
from django.db import models
from apps.core.models import EmpresaTenantModel


class ServicioEquipo(EmpresaTenantModel):
    cliente = models.ForeignKey('terceros.Tercero', on_delete=models.CASCADE, related_name='equipos')
    tipo_equipo = models.CharField(max_length=100, verbose_name="Tipo (e.g. Laptop, Smartphone, Impresora)")
    marca = models.CharField(max_length=100, verbose_name="Marca")
    modelo = models.CharField(max_length=100, verbose_name="Modelo")
    serial = models.CharField(max_length=100, verbose_name="Número de Serie / IMEI")
    color = models.CharField(max_length=50, blank=True)
    accesorios = models.TextField(blank=True, verbose_name="Accesorios dejados (cargador, funda, cable)")

    class Meta:
        verbose_name = "Equipo para Servicio"
        verbose_name_plural = "Equipos para Servicio Técnico"
        ordering = ['marca', 'modelo']

    def __str__(self):
        return f"{self.marca} {self.modelo} - S/N: {self.serial} ({self.cliente.razon_social})"


class OrdenServicio(EmpresaTenantModel):
    ESTADO_CHOICES = [
        ('RECIBIDO', 'Recibido en Taller'),
        ('EN_DIAGNOSTICO', 'En Diagnóstico'),
        ('PRESUPUESTADO', 'Presupuestado / Esperando Aprobación'),
        ('EN_REPARACION', 'En Reparación'),
        ('LISTO', 'Listo para Entrega'),
        ('ENTREGADO', 'Entregado al Cliente'),
        ('GARANTIA', 'En Garantía'),
        ('CANCELADO', 'Cancelado / No Reparado'),
    ]

    PRIORIDAD_CHOICES = [
        ('BAJA', 'Baja'),
        ('NORMAL', 'Normal'),
        ('ALTA', 'Alta / Urgente'),
    ]

    consecutivo = models.BigIntegerField(verbose_name="No. Orden")
    cliente = models.ForeignKey('terceros.Tercero', on_delete=models.PROTECT, related_name='ordenes_servicio')
    equipo = models.ForeignKey(ServicioEquipo, on_delete=models.PROTECT, related_name='ordenes')
    tecnico_asignado = models.ForeignKey('usuarios.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='servicios_asignados')

    fecha_ingreso = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y Hora de Ingreso")
    fecha_estimada_entrega = models.DateField(null=True, blank=True, verbose_name="Fecha Estimada Entrega")
    fecha_entrega_real = models.DateTimeField(null=True, blank=True, verbose_name="Fecha Real de Entrega")

    falla_reportada = models.TextField(verbose_name="Problema / Falla descrita por el Cliente")
    diagnostico_tecnico = models.TextField(blank=True, verbose_name="Diagnóstico Técnico")
    solucion_aplicada = models.TextField(blank=True, verbose_name="Trabajo / Solución Aplicada")

    costo_repuestos = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), verbose_name="Costo de Repuestos ($ COP)")
    costo_mano_obra = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), verbose_name="Mano de Obra ($ COP)")
    total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), verbose_name="Total ($ COP)")

    estado = models.CharField(max_length=30, choices=ESTADO_CHOICES, default='RECIBIDO', verbose_name="Estado Kanban")
    prioridad = models.CharField(max_length=20, choices=PRIORIDAD_CHOICES, default='NORMAL')

    factura = models.ForeignKey('facturacion.Factura', on_delete=models.SET_NULL, null=True, blank=True, related_name='ordenes_servicio')

    class Meta:
        verbose_name = "Orden de Servicio Técnico"
        verbose_name_plural = "Órdenes de Servicio Técnico"
        ordering = ['-consecutivo']
        unique_together = ('empresa', 'consecutivo')

    def __str__(self):
        return f"OS-{self.consecutivo} - {self.equipo.marca} {self.equipo.modelo} ({self.get_estado_display()})"

    def save(self, *args, **kwargs):
        self.total = self.costo_repuestos + self.costo_mano_obra
        super().save(*args, **kwargs)


class EvidenciaServicio(models.Model):
    TIPO_CHOICES = [
        ('FOTO_INGRESO', 'Foto de Estado al Ingreso'),
        ('FOTO_PROCESO', 'Foto Durante Reparación'),
        ('FOTO_FINAL', 'Foto de Equipo Listo'),
        ('DOCUMENTO', 'Documento / Reporte PDF'),
    ]

    orden = models.ForeignKey(OrdenServicio, on_delete=models.CASCADE, related_name='evidencias')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default='FOTO_INGRESO')
    archivo = models.FileField(upload_to='servicios_evidencias/')
    descripcion = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evidencia de Servicio"
        verbose_name_plural = "Evidencias de Servicio"
        ordering = ['-created_at']

    def __str__(self):
        return f"Evidencia OS-{self.orden.consecutivo} ({self.get_tipo_display()})"
