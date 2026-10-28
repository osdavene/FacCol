import hashlib
from decimal import Decimal
from django.db import models
from apps.core.models import EmpresaTenantModel


class Factura(EmpresaTenantModel):
    TIPO_DOCUMENTO_CHOICES = [
        ('FACTURA', 'Factura de Venta Electrónica'),
        ('COTIZACION', 'Cotización Comercial'),
        ('REMISION', 'Remisión de Entrega'),
        ('NOTA_CREDITO', 'Nota de Crédito'),
    ]

    ESTADO_CHOICES = [
        ('BORRADOR', 'Borrador'),
        ('EMITIDA', 'Emitida'),
        ('PAGADA', 'Pagada Totalmente'),
        ('ANULADA', 'Anulada'),
        ('VENCIDA', 'Vencida'),
    ]

    METODO_PAGO_CHOICES = [
        ('CONTADO', 'Contado'),
        ('CREDITO', 'Crédito'),
    ]

    tipo_documento = models.CharField(max_length=25, choices=TIPO_DOCUMENTO_CHOICES, default='FACTURA')
    prefijo = models.CharField(max_length=10, default='FE')
    consecutivo = models.BigIntegerField()

    tercero = models.ForeignKey('terceros.Tercero', on_delete=models.PROTECT, related_name='facturas')

    fecha_emision = models.DateField(verbose_name="Fecha de Emisión")
    fecha_vencimiento = models.DateField(verbose_name="Fecha de Vencimiento")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='BORRADOR')
    metodo_pago = models.CharField(max_length=20, choices=METODO_PAGO_CHOICES, default='CONTADO')

    # Totales monetarios (COP)
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    descuento = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    iva = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    retenciones = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    saldo_pendiente = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))

    # DIAN y Facturación Electrónica
    cufe = models.CharField(max_length=255, blank=True, verbose_name="Código CUFE / CUDE")
    qr_data = models.TextField(blank=True, verbose_name="Cadena Código QR DIAN")

    observaciones = models.TextField(blank=True, verbose_name="Términos y Observaciones")
    pdf_archivo = models.FileField(upload_to='facturas_pdf/', blank=True, null=True)

    # Documento de referencia (para Notas de Crédito)
    factura_referencia = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='notas_credito'
    )

    class Meta:
        verbose_name = "Documento de Facturación"
        verbose_name_plural = "Documentos de Facturación"
        ordering = ['-consecutivo', '-fecha_emision']
        unique_together = ('empresa', 'tipo_documento', 'prefijo', 'consecutivo')

    def __str__(self):
        return f"{self.numero_completo} - {self.tercero.razon_social}"

    @property
    def numero_completo(self):
        return f"{self.prefijo}{self.consecutivo}"

    def recalcular_totales(self):
        items = self.items.all()
        subt = Decimal('0.00')
        desc = Decimal('0.00')
        tax = Decimal('0.00')

        for it in items:
            subt += it.subtotal
            desc += it.descuento
            tax += it.iva

        self.subtotal = subt
        self.descuento = desc
        self.iva = tax
        self.total = subt - desc + tax - self.retenciones

        # Si ya se han pagado recibos, calcular saldo
        pagado = self.recibos.aggregate(total_pagado=models.Sum('valor'))['total_pagado'] or Decimal('0.00')
        self.saldo_pendiente = max(Decimal('0.00'), self.total - pagado)

        if self.saldo_pendiente == Decimal('0.00') and self.total > Decimal('0.00') and self.estado == 'EMITIDA':
            self.estado = 'PAGADA'

        self.save(update_fields=['subtotal', 'descuento', 'iva', 'total', 'saldo_pendiente', 'estado'])

    def generar_cufe(self):
        cadena = f"{self.numero_completo}{self.fecha_emision}{self.total}{self.empresa.nit}{self.tercero.numero_documento}"
        self.cufe = hashlib.sha384(cadena.encode('utf-8')).hexdigest()
        self.qr_data = f"NumFac={self.numero_completo}&FecFac={self.fecha_emision}&NitFac={self.empresa.nit}&DocAdq={self.tercero.numero_documento}&ValFac={self.subtotal}&ValIva={self.iva}&ValTolFac={self.total}&CUFE={self.cufe}"
        self.save(update_fields=['cufe', 'qr_data'])


class FacturaItem(models.Model):
    factura = models.ForeignKey(Factura, on_delete=models.CASCADE, related_name='items')
    producto = models.ForeignKey('inventario.Producto', on_delete=models.SET_NULL, null=True, blank=True)

    descripcion = models.CharField(max_length=255, verbose_name="Descripción del Producto / Servicio")
    cantidad = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1.00'))
    precio_unitario = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    porcentaje_descuento = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    descuento = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    porcentaje_iva = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('19.00'))
    iva = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))

    def save(self, *args, **kwargs):
        self.subtotal = self.cantidad * self.precio_unitario
        self.descuento = (self.subtotal * (self.porcentaje_descuento / Decimal('100.00'))).quantize(Decimal('0.01'))
        base_gravable = self.subtotal - self.descuento
        self.iva = (base_gravable * (self.porcentaje_iva / Decimal('100.00'))).quantize(Decimal('0.01'))
        self.total = base_gravable + self.iva
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.descripcion} ({self.cantidad} x ${self.precio_unitario})"


class ReciboCaja(EmpresaTenantModel):
    METODO_PAGO_CHOICES = [
        ('EFECTIVO', 'Efectivo'),
        ('TRANSFERENCIA', 'Transferencia Bancaria (Bancolombia, Nequi, Daviplata)'),
        ('WOMPI', 'Pago en Línea Wompi (PSE / Tarjeta)'),
        ('TARJETA', 'Datáfono / Tarjeta Crédito o Débito'),
        ('CHEQUE', 'Cheque'),
    ]

    consecutivo = models.BigIntegerField(verbose_name="No. Recibo")
    factura = models.ForeignKey(Factura, on_delete=models.SET_NULL, null=True, blank=True, related_name='recibos')
    tercero = models.ForeignKey('terceros.Tercero', on_delete=models.PROTECT, related_name='recibos_caja')
    fecha = models.DateField(verbose_name="Fecha de Pago")
    valor = models.DecimalField(max_digits=16, decimal_places=2, verbose_name="Valor Recibido ($ COP)")
    metodo_pago = models.CharField(max_length=30, choices=METODO_PAGO_CHOICES, default='EFECTIVO')
    referencia_pago = models.CharField(max_length=100, blank=True, verbose_name="No. Aprobación / Referencia")
    observaciones = models.TextField(blank=True, verbose_name="Observaciones")

    class Meta:
        verbose_name = "Recibo de Caja"
        verbose_name_plural = "Recibos de Caja"
        ordering = ['-consecutivo', '-fecha']
        unique_together = ('empresa', 'consecutivo')

    def __str__(self):
        return f"RC-{self.consecutivo} - ${self.valor} ({self.tercero.razon_social})"
