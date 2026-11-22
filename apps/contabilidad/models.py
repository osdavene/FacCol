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


class CentroCosto(EmpresaTenantModel):
    """Centros de costos y proyectos para analizar rentabilidad por área/sucursal."""
    codigo = models.CharField(max_length=20)
    nombre = models.CharField(max_length=120)
    activa = models.BooleanField(default=True)

    class Meta:
        unique_together = ('empresa', 'codigo')
        ordering = ['codigo']
        verbose_name = "Centro de Costos"
        verbose_name_plural = "Centros de Costos"

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


class AsientoLinea(models.Model):
    asiento = models.ForeignKey(AsientoContable, on_delete=models.CASCADE, related_name='lineas')
    cuenta = models.ForeignKey(PlanCuenta, on_delete=models.PROTECT, related_name='movimientos')
    tercero = models.ForeignKey('terceros.Tercero', on_delete=models.SET_NULL, null=True, blank=True)
    centro_costo = models.ForeignKey(CentroCosto, on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos')
    descripcion = models.CharField(max_length=255, blank=True)
    debito = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    credito = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))

    def __str__(self):
        return f"{self.cuenta.codigo}: Débito ${self.debito} / Crédito ${self.credito}"


class PeriodoContableCerrado(EmpresaTenantModel):
    """Bloqueo de períodos mensuales/anuales para garantizar inmutabilidad contable."""
    ano = models.IntegerField()
    mes = models.IntegerField(help_text="1-12 para cierre mensual; 0 para cierre anual de resultados")
    cerrado = models.BooleanField(default=True)
    cerrado_por = models.ForeignKey('usuarios.User', null=True, blank=True, on_delete=models.SET_NULL)
    observaciones = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ('empresa', 'ano', 'mes')
        ordering = ['-ano', '-mes']
        verbose_name = "Cierre de Período Contable"
        verbose_name_plural = "Cierres de Períodos Contables"

    def __str__(self):
        return f"{self.ano}-{self.mes:02d} ({'Cerrado' if self.cerrado else 'Abierto'})"


class ComprobanteEgreso(EmpresaTenantModel):
    """Registro de pagos / abonos a proveedores (Cuentas por Pagar) y egresos de tesorería."""
    METODO_PAGO_CHOICES = [
        ('EFECTIVO', 'Efectivo (Caja General)'),
        ('TRANSFERENCIA', 'Transferencia Bancaria'),
        ('NEQUI', 'Nequi / Daviplata'),
        ('TARJETA', 'Tarjeta Empresarial'),
    ]

    consecutivo = models.BigIntegerField(verbose_name="Consecutivo CE")
    tercero = models.ForeignKey('terceros.Tercero', on_delete=models.PROTECT, related_name='egresos')
    compra = models.ForeignKey(
        'inventario.CompraProveedor',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='egresos',
    )
    fecha = models.DateField(verbose_name="Fecha de Pago")
    valor = models.DecimalField(max_digits=16, decimal_places=2)
    metodo_pago = models.CharField(max_length=30, choices=METODO_PAGO_CHOICES, default='TRANSFERENCIA')
    referencia = models.CharField(max_length=100, blank=True)
    observaciones = models.TextField(blank=True)

    class Meta:
        unique_together = ('empresa', 'consecutivo')
        ordering = ['-fecha', '-consecutivo']
        verbose_name = "Comprobante de Egreso"
        verbose_name_plural = "Comprobantes de Egreso"

    @property
    def numero_completo(self):
        return f"CE-{self.consecutivo}"

    def __str__(self):
        return f"{self.numero_completo} - {self.tercero.razon_social} (${self.valor:,.0f})"


class GastoOperativo(EmpresaTenantModel):
    """Causación de gastos y servicios (arriendos, honorarios, servicios) con IVA descontable y ReteFuente."""
    METODO_PAGO_CHOICES = [
        ('CONTADO', 'Contado (Caja / Bancos)'),
        ('CREDITO', 'Crédito (Cuentas por Pagar)'),
    ]

    consecutivo = models.BigIntegerField()
    tercero = models.ForeignKey('terceros.Tercero', on_delete=models.PROTECT, related_name='gastos')
    cuenta_gasto = models.ForeignKey(PlanCuenta, on_delete=models.PROTECT, related_name='gastos_operativos')
    fecha = models.DateField()
    numero_soporte = models.CharField(max_length=80, blank=True)
    concepto = models.CharField(max_length=255)
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    iva = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    retencion_fuente = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    retencion_ica = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    total_pagar = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    metodo_pago = models.CharField(max_length=20, choices=METODO_PAGO_CHOICES, default='CONTADO')
    centro_costo = models.ForeignKey(CentroCosto, on_delete=models.SET_NULL, null=True, blank=True, related_name='gastos')

    class Meta:
        unique_together = ('empresa', 'consecutivo')
        ordering = ['-fecha', '-consecutivo']
        verbose_name = "Gasto Operativo"
        verbose_name_plural = "Gastos Operativos"

    @property
    def numero_completo(self):
        return f"GS-{self.consecutivo}"

    def __str__(self):
        return f"{self.numero_completo} - {self.concepto} (${self.total_pagar:,.0f})"


class CuentaBancaria(EmpresaTenantModel):
    """Cuentas bancarias y cajas menores vinculadas al PUC para conciliación bancaria."""
    TIPO_CHOICES = [
        ('CORRIENTE', 'Cuenta Corriente'),
        ('AHORROS', 'Cuenta de Ahorros'),
        ('CAJA_MENOR', 'Caja Menor / Efectivo'),
        ('BILLETERA', 'Billetera Digital (Nequi / Daviplata)'),
    ]

    nombre = models.CharField(max_length=120)
    banco = models.CharField(max_length=100, blank=True)
    numero_cuenta = models.CharField(max_length=60, blank=True)
    tipo_cuenta = models.CharField(max_length=20, choices=TIPO_CHOICES, default='AHORROS')
    cuenta_contable = models.ForeignKey(PlanCuenta, on_delete=models.PROTECT, related_name='cuentas_bancarias')
    saldo_inicial = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = "Cuenta Bancaria / Tesorería"
        verbose_name_plural = "Cuentas Bancarias y Cajas"

    def __str__(self):
        return f"{self.nombre} ({self.banco or self.get_tipo_cuenta_display()})"


class MovimientoExtractoBancario(EmpresaTenantModel):
    """Movimientos importados desde extractos bancarios para conciliación contra asientos PUC."""
    cuenta_bancaria = models.ForeignKey(CuentaBancaria, on_delete=models.CASCADE, related_name='extractos')
    fecha = models.DateField()
    descripcion = models.CharField(max_length=255)
    referencia = models.CharField(max_length=100, blank=True)
    valor = models.DecimalField(max_digits=16, decimal_places=2, help_text="Positivo=Ingreso/Abono, Negativo=Retiro/Pago")
    conciliado = models.BooleanField(default=False)
    asiento_linea = models.ForeignKey(AsientoLinea, on_delete=models.SET_NULL, null=True, blank=True, related_name='conciliaciones')

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = "Movimiento de Extracto Bancario"
        verbose_name_plural = "Movimientos de Extractos Bancarios"


class ActivoFijo(EmpresaTenantModel):
    """Activos fijos de propiedad, planta y equipo con depreciación mensual NIIF."""
    codigo = models.CharField(max_length=30)
    nombre = models.CharField(max_length=180)
    fecha_compra = models.DateField()
    valor_compra = models.DecimalField(max_digits=16, decimal_places=2)
    valor_residual = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    vida_util_meses = models.IntegerField(default=60)
    depreciacion_acumulada = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    ultima_depreciacion = models.DateField(null=True, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        unique_together = ('empresa', 'codigo')
        ordering = ['codigo']
        verbose_name = "Activo Fijo"
        verbose_name_plural = "Activos Fijos"

    @property
    def cuota_depreciacion_mensual(self):
        base = max(Decimal('0.00'), self.valor_compra - self.valor_residual)
        if self.vida_util_meses <= 0:
            return Decimal('0.00')
        return (base / Decimal(self.vida_util_meses)).quantize(Decimal('0.01'))

    @property
    def valor_neto_libros(self):
        return max(Decimal('0.00'), self.valor_compra - self.depreciacion_acumulada)

    def __str__(self):
        return f"[{self.codigo}] {self.nombre}"
