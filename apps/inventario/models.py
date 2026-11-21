from decimal import Decimal
from django.db import models
from apps.core.models import EmpresaTenantModel


class Categoria(EmpresaTenantModel):
    nombre = models.CharField(max_length=100, verbose_name="Nombre Categoría")
    descripcion = models.TextField(blank=True, verbose_name="Descripción")
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Categoría"
        verbose_name_plural = "Categorías"
        ordering = ['nombre']
        unique_together = ('empresa', 'nombre')

    def __str__(self):
        return self.nombre


class UnidadMedida(EmpresaTenantModel):
    codigo = models.CharField(max_length=10, verbose_name="Código DIAN / Estándar")
    nombre = models.CharField(max_length=100, verbose_name="Nombre Unidad")
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Unidad de Medida"
        verbose_name_plural = "Unidades de Medida"
        ordering = ['nombre']
        unique_together = ('empresa', 'codigo')

    def __str__(self):
        return f"{self.nombre} ({self.codigo})"


class Producto(EmpresaTenantModel):
    codigo = models.CharField(max_length=50, verbose_name="Código / SKU")
    codigo_barras = models.CharField(max_length=60, blank=True, db_index=True, verbose_name="Código de Barras (EAN/UPC)")
    nombre = models.CharField(max_length=200, verbose_name="Nombre del Producto")
    descripcion = models.TextField(blank=True, verbose_name="Descripción detallada")

    categoria = models.ForeignKey(Categoria, on_delete=models.SET_NULL, null=True, blank=True, related_name='productos')
    unidad_medida = models.ForeignKey(UnidadMedida, on_delete=models.SET_NULL, null=True, blank=True, related_name='productos')

    precio_venta = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Precio de Venta")
    costo_unitario = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Costo de Compra")
    tarifa_iva = models.DecimalField(max_digits=5, decimal_places=2, default=19.00, verbose_name="% IVA")
    impuesto_saludable_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        verbose_name="% Imp. Saludable / IBUA / ICUI / Impoconsumo"
    )
    precio_incluye_impuestos = models.BooleanField(
        default=False,
        verbose_name="El precio de venta ya incluye IVA y otros impuestos"
    )

    # Atributos para menudeo de Tienda de Barrio
    es_favorito_pos = models.BooleanField(default=False, verbose_name="Favorito en Caja Express")
    permite_fraccion = models.BooleanField(default=False, verbose_name="Permite venta fraccionada / por valor")
    factor_conversion = models.DecimalField(
        max_digits=10, decimal_places=3, default=Decimal('1.000'),
        verbose_name="Unidades de menudeo por empaque de compra"
    )

    stock_actual = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Stock Actual")
    stock_minimo = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Stock Mínimo")
    stock_maximo = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Stock Máximo")

    maneja_inventario = models.BooleanField(default=True, verbose_name="¿Controla Stock?")
    imagen = models.ImageField(upload_to='productos/', blank=True, null=True, verbose_name="Foto del Producto")
    activo = models.BooleanField(default=True, verbose_name="Activo")

    class Meta:
        verbose_name = "Producto"
        verbose_name_plural = "Productos"
        ordering = ['nombre']
        unique_together = ('empresa', 'codigo')

    def __str__(self):
        return f"[{self.codigo}] {self.nombre}"

    @property
    def precio_con_iva(self):
        if self.precio_incluye_impuestos:
            return self.precio_venta
        total_pct = (self.tarifa_iva or Decimal('0')) + (self.impuesto_saludable_pct or Decimal('0'))
        return self.precio_venta * (Decimal('1') + (total_pct / Decimal('100')))

    @property
    def precio_base_sin_impuestos(self):
        if not self.precio_incluye_impuestos:
            return self.precio_venta
        total_pct = (self.tarifa_iva or Decimal('0')) + (self.impuesto_saludable_pct or Decimal('0'))
        factor = Decimal('1') + (total_pct / Decimal('100'))
        return (self.precio_venta / factor).quantize(Decimal('0.01')) if factor > 0 else self.precio_venta

    @property
    def stock_bajo(self):
        if self.maneja_inventario and self.stock_minimo > 0:
            return self.stock_actual <= self.stock_minimo
        return False


class MovimientoInventario(EmpresaTenantModel):
    TIPO_CHOICES = [
        ('ENTRADA', 'Entrada'),
        ('SALIDA', 'Salida'),
        ('AJUSTE', 'Ajuste de Inventario'),
    ]

    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='movimientos')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    cantidad = models.DecimalField(max_digits=12, decimal_places=2)
    costo_unitario = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    saldo_anterior = models.DecimalField(max_digits=12, decimal_places=2)
    saldo_nuevo = models.DecimalField(max_digits=12, decimal_places=2)

    motivo = models.CharField(max_length=255, verbose_name="Motivo / Descripción")
    referencia_documento = models.CharField(max_length=100, blank=True, verbose_name="No. Documento Origen")

    class Meta:
        verbose_name = "Movimiento de Inventario"
        verbose_name_plural = "Kardex / Movimientos de Inventario"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.tipo} - {self.producto.nombre} ({self.cantidad})"


class CompraProveedor(EmpresaTenantModel):
    """
    Registro de Compras a Proveedores y Documento Soporte Electrónico DIAN (Resolución 000167)
    para adquisiciones a sujetos no obligados a facturar, con ingreso automático de inventario.
    """
    METODO_PAGO_CHOICES = [
        ('CONTADO', 'Contado'),
        ('CREDITO', 'Crédito (Cuentas por Pagar)'),
    ]

    consecutivo = models.BigIntegerField(verbose_name="Consecutivo Interno / DSE")
    proveedor = models.ForeignKey('terceros.Tercero', on_delete=models.PROTECT, related_name='compras')
    numero_factura_proveedor = models.CharField(max_length=80, blank=True, verbose_name="No. Factura Proveedor")
    fecha = models.DateField(verbose_name="Fecha de Compra")
    metodo_pago = models.CharField(max_length=20, choices=METODO_PAGO_CHOICES, default='CONTADO')
    es_documento_soporte = models.BooleanField(default=False, verbose_name="Emitir Documento Soporte DIAN (DSE)")
    cuds = models.CharField(max_length=255, blank=True, verbose_name="CUDS Documento Soporte")
    estado_dian = models.CharField(max_length=25, default='NO_APLICA')
    total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    observaciones = models.TextField(blank=True)

    class Meta:
        verbose_name = "Compra a Proveedor / DSE"
        verbose_name_plural = "Compras a Proveedores / DSE"
        ordering = ['-fecha', '-consecutivo']
        unique_together = ('empresa', 'consecutivo')

    @property
    def numero_completo(self):
        return f"DSE{self.consecutivo}" if self.es_documento_soporte else f"OC{self.consecutivo}"

    def __str__(self):
        return f"{self.numero_completo} - {self.proveedor.razon_social} (${self.total})"


class CompraItem(models.Model):
    compra = models.ForeignKey(CompraProveedor, on_delete=models.CASCADE, related_name='items')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad_empaque = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('1.000'))
    unidades_por_empaque = models.DecimalField(max_digits=10, decimal_places=3, default=Decimal('1.000'))
    unidades_totales = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('1.000'))
    costo_unitario_empaque = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))

    def save(self, *args, **kwargs):
        self.unidades_totales = (Decimal(str(self.cantidad_empaque)) * Decimal(str(self.unidades_por_empaque))).quantize(Decimal('0.001'))
        self.subtotal = (Decimal(str(self.cantidad_empaque)) * Decimal(str(self.costo_unitario_empaque))).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)
