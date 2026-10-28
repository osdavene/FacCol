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
    nombre = models.CharField(max_length=200, verbose_name="Nombre del Producto")
    descripcion = models.TextField(blank=True, verbose_name="Descripción detallada")

    categoria = models.ForeignKey(Categoria, on_delete=models.SET_NULL, null=True, blank=True, related_name='productos')
    unidad_medida = models.ForeignKey(UnidadMedida, on_delete=models.SET_NULL, null=True, blank=True, related_name='productos')

    precio_venta = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Precio de Venta (sin IVA)")
    costo_unitario = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Costo de Compra")
    tarifa_iva = models.DecimalField(max_digits=5, decimal_places=2, default=19.00, verbose_name="% IVA")

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
        return self.precio_venta * (1 + (self.tarifa_iva / 100))

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
