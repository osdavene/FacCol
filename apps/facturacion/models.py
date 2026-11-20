import hashlib
from decimal import Decimal
from django.db import models
from apps.core.models import EmpresaTenantModel


class Factura(EmpresaTenantModel):
    TIPO_DOCUMENTO_CHOICES = [
        ('FACTURA', 'Factura de Venta Electrónica'),
        ('POS_ELECTRONICO', 'Documento Equivalente Electrónico POS'),
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

    ESTADO_DIAN_CHOICES = [
        ('PENDIENTE', 'En Cola DIAN'),
        ('CONTINGENCIA', 'Contingencia / Offline'),
        ('ENVIADA', 'Aceptada DIAN'),
        ('RECHAZADA', 'Rechazada DIAN'),
        ('NO_APLICA', 'No Aplica'),
    ]

    tipo_documento = models.CharField(max_length=25, choices=TIPO_DOCUMENTO_CHOICES, default='FACTURA')
    prefijo = models.CharField(max_length=10, default='FE')
    consecutivo = models.BigIntegerField()

    tercero = models.ForeignKey('terceros.Tercero', on_delete=models.PROTECT, related_name='facturas')

    fecha_emision = models.DateField(verbose_name="Fecha de Emisión")
    fecha_vencimiento = models.DateField(verbose_name="Fecha de Vencimiento")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='BORRADOR')
    metodo_pago = models.CharField(max_length=20, choices=METODO_PAGO_CHOICES, default='CONTADO')
    es_fiado = models.BooleanField(default=False, verbose_name="Venta a Fiado")

    # Totales monetarios (COP)
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    descuento = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    iva = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    impuestos_adicionales = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    retenciones = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    saldo_pendiente = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))

    # DIAN y Facturación Electrónica (Resolución 000165)
    cufe = models.CharField(max_length=255, blank=True, verbose_name="Código CUFE / CUDE")
    qr_data = models.TextField(blank=True, verbose_name="Cadena Código QR DIAN")
    estado_dian = models.CharField(max_length=25, choices=ESTADO_DIAN_CHOICES, default='PENDIENTE')
    dian_track_id = models.CharField(max_length=120, blank=True, verbose_name="TrackId DIAN")
    dian_mensaje = models.TextField(blank=True, verbose_name="Respuesta DIAN / Bitácora")

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
        otros_imp = Decimal('0.00')

        for it in items:
            subt += it.subtotal
            desc += it.descuento
            tax += it.iva
            otros_imp += it.otro_impuesto

        self.subtotal = subt
        self.descuento = desc
        self.iva = tax
        self.impuestos_adicionales = otros_imp
        self.total = subt - desc + tax + otros_imp - self.retenciones

        # Si ya se han pagado recibos, calcular saldo
        pagado = self.recibos.aggregate(total_pagado=models.Sum('valor'))['total_pagado'] or Decimal('0.00')
        self.saldo_pendiente = max(Decimal('0.00'), self.total - pagado)

        if self.saldo_pendiente == Decimal('0.00') and self.total > Decimal('0.00') and self.estado == 'EMITIDA':
            self.estado = 'PAGADA'

        self.save(update_fields=[
            'subtotal', 'descuento', 'iva', 'impuestos_adicionales',
            'total', 'saldo_pendiente', 'estado'
        ])

    def generar_cufe(self):
        from apps.facturacion.dian_service import calcular_cufe_y_qr, encolar_documento_dian
        cufe, qr = calcular_cufe_y_qr(self)
        self.cufe = cufe
        self.qr_data = qr
        encolar_documento_dian(self, save=False)
        self.save(update_fields=['cufe', 'qr_data', 'estado_dian', 'dian_mensaje'])


class FacturaItem(models.Model):
    factura = models.ForeignKey(Factura, on_delete=models.CASCADE, related_name='items')
    producto = models.ForeignKey('inventario.Producto', on_delete=models.SET_NULL, null=True, blank=True)

    descripcion = models.CharField(max_length=255, verbose_name="Descripción del Producto / Servicio")
    cantidad = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('1.000'))
    precio_unitario = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    porcentaje_descuento = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    descuento = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    porcentaje_iva = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('19.00'))
    iva = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    porcentaje_otro_impuesto = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    otro_impuesto = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))

    def save(self, *args, **kwargs):
        self.subtotal = (self.cantidad * self.precio_unitario).quantize(Decimal('0.01'))
        self.descuento = (self.subtotal * (self.porcentaje_descuento / Decimal('100.00'))).quantize(Decimal('0.01'))
        base_gravable = self.subtotal - self.descuento
        self.iva = (base_gravable * (self.porcentaje_iva / Decimal('100.00'))).quantize(Decimal('0.01'))
        self.otro_impuesto = (base_gravable * (self.porcentaje_otro_impuesto / Decimal('100.00'))).quantize(Decimal('0.01'))
        self.total = base_gravable + self.iva + self.otro_impuesto
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.descripcion} ({self.cantidad} x ${self.precio_unitario})"


class ReciboCaja(EmpresaTenantModel):
    METODO_PAGO_CHOICES = [
        ('EFECTIVO', 'Efectivo'),
        ('NEQUI', 'Nequi / Daviplata'),
        ('TRANSFERENCIA', 'Transferencia Bancaria (Bancolombia, Bre-B, PSE)'),
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


class TransaccionWompi(EmpresaTenantModel):
    ESTADO_CHOICES = [
        ('PENDING', 'Pendiente'),
        ('APPROVED', 'Aprobada'),
        ('DECLINED', 'Rechazada'),
        ('VOIDED', 'Anulada'),
        ('ERROR', 'Error'),
        ('AMOUNT_MISMATCH', 'Monto no coincide'),
    ]

    reference = models.CharField(max_length=80, unique=True)
    factura = models.ForeignKey(Factura, on_delete=models.SET_NULL, null=True, blank=True, related_name='transacciones_wompi')
    plan = models.ForeignKey('empresas.Plan', on_delete=models.SET_NULL, null=True, blank=True)
    usuario = models.ForeignKey('usuarios.User', on_delete=models.SET_NULL, null=True, blank=True)
    amount_in_cents = models.BigIntegerField()
    currency = models.CharField(max_length=10, default='COP')
    status = models.CharField(max_length=25, choices=ESTADO_CHOICES, default='PENDING')
    wompi_transaction_id = models.CharField(max_length=120, blank=True, db_index=True)
    payment_method_type = models.CharField(max_length=60, blank=True)
    customer_email = models.EmailField(blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Transacción Wompi"
        verbose_name_plural = "Transacciones Wompi"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.reference} ({self.status}) - ${self.amount_in_cents // 100:,.0f} COP"
