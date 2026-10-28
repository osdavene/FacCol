from django.db import models


class Departamento(models.Model):
    codigo = models.CharField(max_length=5, unique=True, verbose_name="Código DANE")
    nombre = models.CharField(max_length=100, verbose_name="Nombre Departamento")

    class Meta:
        verbose_name = "Departamento"
        verbose_name_plural = "Departamentos"
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Municipio(models.Model):
    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='municipios')
    codigo = models.CharField(max_length=10, unique=True, verbose_name="Código DANE")
    nombre = models.CharField(max_length=100, verbose_name="Nombre Municipio")

    class Meta:
        verbose_name = "Municipio"
        verbose_name_plural = "Municipios"
        ordering = ['nombre']

    def __str__(self):
        return f"{self.nombre} ({self.departamento.nombre})"


class Plan(models.Model):
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)
    precio_mensual = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    limite_facturas_mes = models.IntegerField(default=500)
    limite_usuarios = models.IntegerField(default=5)
    duracion_meses = models.IntegerField(default=1)
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Plan"
        verbose_name_plural = "Planes"

    def __str__(self):
        return self.nombre


class Modulo(models.Model):
    codigo = models.CharField(max_length=50, unique=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)
    icono = models.CharField(max_length=50, default='fa-folder')
    ruta = models.CharField(max_length=100, default='#')
    orden = models.IntegerField(default=0)
    activo_por_defecto = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Módulo"
        verbose_name_plural = "Módulos"
        ordering = ['orden', 'nombre']

    def __str__(self):
        return self.nombre


class Empresa(models.Model):
    TIPO_PERSONA_CHOICES = [
        ('NATURAL', 'Persona Natural'),
        ('JURIDICA', 'Persona Jurídica'),
    ]

    REGIMEN_CHOICES = [
        ('RESPONSABLE_IVA', 'Responsable de IVA (Común)'),
        ('NO_RESPONSABLE_IVA', 'No Responsable de IVA (Simplificado)'),
        ('REGIMEN_SIMPLE', 'Régimen Simple de Tributación (RST)'),
    ]

    razon_social = models.CharField(max_length=200, verbose_name="Razón Social")
    nombre_comercial = models.CharField(max_length=200, blank=True, verbose_name="Nombre Comercial")
    nit = models.CharField(max_length=30, unique=True, verbose_name="NIT / Identificación")
    dv = models.CharField(max_length=1, blank=True, verbose_name="Dígito Verificación")
    tipo_persona = models.CharField(max_length=20, choices=TIPO_PERSONA_CHOICES, default='JURIDICA')
    regimen_tributario = models.CharField(max_length=30, choices=REGIMEN_CHOICES, default='RESPONSABLE_IVA')

    direccion = models.CharField(max_length=200, blank=True, verbose_name="Dirección")
    departamento = models.ForeignKey(Departamento, on_delete=models.SET_NULL, null=True, blank=True)
    municipio = models.ForeignKey(Municipio, on_delete=models.SET_NULL, null=True, blank=True)
    telefono = models.CharField(max_length=50, blank=True, verbose_name="Teléfono")
    email = models.EmailField(blank=True, verbose_name="Correo Electrónico")
    logo = models.ImageField(upload_to='logos/', blank=True, null=True, verbose_name="Logo")

    # Configuración de Facturación DIAN
    prefijo_factura = models.CharField(max_length=10, default='FE', verbose_name="Prefijo Factura")
    resolucion_dian = models.CharField(max_length=100, blank=True, verbose_name="Número Resolución DIAN")
    fecha_resolucion = models.DateField(null=True, blank=True, verbose_name="Fecha Resolución")
    rango_desde = models.BigIntegerField(default=1, verbose_name="Rango Desde")
    rango_hasta = models.BigIntegerField(default=5000, verbose_name="Rango Hasta")
    consecutivo_actual = models.BigIntegerField(default=1, verbose_name="Consecutivo Actual")
    dias_vencimiento = models.IntegerField(default=30, verbose_name="Días de vencimiento por defecto")

    # Servidor SMTP de la Empresa
    smtp_host = models.CharField(max_length=150, blank=True, verbose_name="Host SMTP")
    smtp_port = models.IntegerField(default=587, verbose_name="Puerto SMTP")
    smtp_user = models.CharField(max_length=150, blank=True, verbose_name="Usuario SMTP")
    smtp_password = models.CharField(max_length=150, blank=True, verbose_name="Contraseña SMTP")
    smtp_encryption = models.CharField(max_length=10, default='tls', choices=[('tls', 'TLS'), ('ssl', 'SSL'), ('none', 'Ninguna')])
    smtp_from_address = models.CharField(max_length=150, blank=True, verbose_name="Correo Remitente")
    smtp_from_name = models.CharField(max_length=150, blank=True, verbose_name="Nombre Remitente")

    # Wompi (Pagos en línea Colombia)
    wompi_public_key = models.CharField(max_length=200, blank=True)
    wompi_private_key = models.CharField(max_length=200, blank=True)
    wompi_events_secret = models.CharField(max_length=200, blank=True)
    wompi_sandbox = models.BooleanField(default=True)

    # Multi-tenancy & Filiales
    empresa_padre = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='filiales')
    plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name='empresas')
    modulos = models.ManyToManyField(Modulo, through='EmpresaModulo', blank=True, related_name='empresas')
    activa = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"
        ordering = ['razon_social']

    def __str__(self):
        return self.nombre_comercial or self.razon_social

    @property
    def display_name(self):
        return self.nombre_comercial or self.razon_social

    @property
    def nit_completo(self):
        if self.dv:
            return f"{self.nit}-{self.dv}"
        return self.nit

    @property
    def logo_url(self):
        if self.logo:
            return self.logo.url
        return None

    def obtener_siguiente_consecutivo(self):
        num = self.consecutivo_actual
        self.consecutivo_actual += 1
        self.save(update_fields=['consecutivo_actual'])
        return num


class EmpresaModulo(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    modulo = models.ForeignKey(Modulo, on_delete=models.CASCADE)
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('empresa', 'modulo')
        verbose_name = "Módulo de Empresa"
        verbose_name_plural = "Módulos de Empresas"
