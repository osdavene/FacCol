from decimal import Decimal
from django.db import models
from apps.core.models import EmpresaTenantModel


class Empleado(EmpresaTenantModel):
    TIPO_DOC_CHOICES = [
        ('CC', 'Cédula de Ciudadanía'),
        ('CE', 'Cédula de Extranjería'),
        ('PASAPORTE', 'Pasaporte'),
        ('PEP', 'Permiso Especial de Permanencia'),
        ('PPT', 'Permiso por Protección Temporal'),
    ]

    TIPO_CONTRATO_CHOICES = [
        ('TERMINO_INDEFINIDO', 'Término Indefinido'),
        ('TERMINO_FIJO', 'Término Fijo'),
        ('OBRA_LABOR', 'Por Obra o Labor'),
        ('APRENDIZAJE', 'Contrato de Aprendizaje'),
        ('PRESTACION_SERVICIOS', 'Prestación de Servicios'),
    ]

    RIESGO_ARL_CHOICES = [
        ('1', 'Clase I - Mínimo (0.522%)'),
        ('2', 'Clase II - Bajo (1.044%)'),
        ('3', 'Clase III - Medio (2.436%)'),
        ('4', 'Clase IV - Alto (4.350%)'),
        ('5', 'Clase V - Máximo (6.960%)'),
    ]

    tipo_documento = models.CharField(max_length=20, choices=TIPO_DOC_CHOICES, default='CC')
    numero_documento = models.CharField(max_length=30, verbose_name="Número de Documento")
    nombres = models.CharField(max_length=100, verbose_name="Nombres")
    apellidos = models.CharField(max_length=100, verbose_name="Apellidos")
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    direccion = models.CharField(max_length=200, blank=True)
    foto = models.ImageField(upload_to='empleados/', blank=True, null=True)

    cargo = models.CharField(max_length=100, verbose_name="Cargo")
    salario_base = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="Salario Base Mensual ($ COP)")
    tiene_auxilio_transporte = models.BooleanField(default=True, verbose_name="¿Aplica Auxilio de Transporte?")
    tipo_contrato = models.CharField(max_length=30, choices=TIPO_CONTRATO_CHOICES, default='TERMINO_INDEFINIDO')
    fecha_ingreso = models.DateField(verbose_name="Fecha de Ingreso")

    eps = models.CharField(max_length=100, blank=True, verbose_name="Entidad EPS (Salud)")
    fondo_pension = models.CharField(max_length=100, blank=True, verbose_name="Fondo de Pensión")
    caja_compensacion = models.CharField(max_length=100, blank=True, verbose_name="Caja de Compensación")
    nivel_riesgo_arl = models.CharField(max_length=5, choices=RIESGO_ARL_CHOICES, default='1')

    activo = models.BooleanField(default=True, verbose_name="Empleado Activo")

    class Meta:
        verbose_name = "Empleado"
        verbose_name_plural = "Empleados"
        ordering = ['apellidos', 'nombres']
        unique_together = ('empresa', 'numero_documento')

    def __str__(self):
        return f"{self.nombres} {self.apellidos} ({self.numero_documento})"

    @property
    def nombre_completo(self):
        return f"{self.nombres} {self.apellidos}"


class Nomina(EmpresaTenantModel):
    ESTADO_CHOICES = [
        ('BORRADOR', 'Borrador'),
        ('LIQUIDADA', 'Liquidada y Aprobada'),
        ('PAGADA', 'Pagada'),
        ('ANULADA', 'Anulada'),
    ]

    periodo_mes = models.IntegerField(verbose_name="Mes (1-12)")
    periodo_ano = models.IntegerField(verbose_name="Año (e.g. 2026)")
    fecha_inicio = models.DateField(verbose_name="Fecha Inicio Período")
    fecha_fin = models.DateField(verbose_name="Fecha Fin Período")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='BORRADOR')

    # Constantes aplicadas en la liquidación
    smmlv = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1423500.00'), verbose_name="SMMLV Vigente")
    auxilio_transporte_legal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('202050.00'), verbose_name="Auxilio de Transporte Vigente")

    total_devengado = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    total_deducciones = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))
    total_neto = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'))

    observaciones = models.TextField(blank=True)

    class Meta:
        verbose_name = "Liquidación de Nómina"
        verbose_name_plural = "Liquidaciones de Nómina"
        ordering = ['-periodo_ano', '-periodo_mes']
        unique_together = ('empresa', 'periodo_mes', 'periodo_ano')

    def __str__(self):
        return f"Nómina {self.periodo_mes}/{self.periodo_ano} - {self.empresa.display_name}"


class NominaEmpleado(models.Model):
    nomina = models.ForeignKey(Nomina, on_delete=models.CASCADE, related_name='detalles')
    empleado = models.ForeignKey(Empleado, on_delete=models.PROTECT, related_name='liquidaciones')

    dias_trabajados = models.IntegerField(default=30)
    sueldo_basico_devengado = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    auxilio_transporte = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    horas_extras = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    bonificaciones = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    total_devengado = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    # Deducciones del Trabajador (Colombia)
    salud_empleado_4 = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    pension_empleado_4 = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    otras_deducciones = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    total_deducciones = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    neto_a_pagar = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    # Aportes Parafiscales y Seguridad Social (Empleador)
    salud_empleador_8_5 = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    pension_empleador_12 = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    arl_empleador = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    sena_2 = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    icbf_3 = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    caja_compensacion_4 = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    # Provisiones Prestaciones Sociales (Ley Colombiana)
    provision_prima = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    provision_cesantias = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    provision_intereses = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    provision_vacaciones = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        verbose_name = "Detalle Nómina Empleado"
        verbose_name_plural = "Detalles Nómina Empleados"
        unique_together = ('nomina', 'empleado')

    def __str__(self):
        return f"{self.empleado.nombre_completo} - Neto: ${self.neto_a_pagar}"
