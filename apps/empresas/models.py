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


class Plan(models.Model):
    COLOR_CHOICES = [
        ('amber', 'Ámbar / Dorado'),
        ('blue', 'Azul Profesional'),
        ('purple', 'Púrpura / Índigo'),
        ('emerald', 'Esmeralda / Verde'),
        ('cyan', 'Cian / Celeste'),
    ]

    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)
    precio_mensual = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    limite_facturas_mes = models.IntegerField(default=500, help_text="0 para ilimitadas")
    limite_usuarios = models.IntegerField(default=5, help_text="0 para ilimitados")
    duracion_meses = models.IntegerField(default=1)
    destacado = models.BooleanField(default=False, verbose_name="Plan Destacado / Más Popular")
    color_tema = models.CharField(max_length=30, default='amber', choices=COLOR_CHOICES)
    caracteristicas = models.TextField(
        blank=True,
        verbose_name="Características / Beneficios (1 por línea)",
        help_text="Lista de ventajas visibles para el cliente, una por línea"
    )
    modulos = models.ManyToManyField(Modulo, blank=True, related_name='planes')
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Plan"
        verbose_name_plural = "Planes"
        ordering = ['precio_mensual']

    def __str__(self):
        return self.nombre

    @property
    def lista_caracteristicas(self):
        if not self.caracteristicas:
            return []
        return [c.strip() for c in self.caracteristicas.split('\n') if c.strip()]

    @property
    def total_empresas(self):
        return self.empresas.count()

    @property
    def empresas_activas(self):
        return self.empresas.filter(activa=True).count()

    @property
    def mrr_generado(self):
        return self.precio_mensual * self.empresas_activas


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

    PERFIL_OPERATIVO_CHOICES = [
        ('GENERAL', 'Empresa Estándar (Completa)'),
        ('TIENDA', 'Tienda de Barrio / Minimercado (Caja Express)'),
    ]

    MODO_DIAN_CHOICES = [
        ('PRUEBAS', 'Habilitación / Pruebas DIAN'),
        ('PRODUCCION', 'Producción DIAN'),
    ]

    razon_social = models.CharField(max_length=200, verbose_name="Razón Social")
    nombre_comercial = models.CharField(max_length=200, blank=True, verbose_name="Nombre Comercial")
    nit = models.CharField(max_length=30, unique=True, verbose_name="NIT / Identificación")
    dv = models.CharField(max_length=1, blank=True, verbose_name="Dígito Verificación")
    tipo_persona = models.CharField(max_length=20, choices=TIPO_PERSONA_CHOICES, default='JURIDICA')
    regimen_tributario = models.CharField(max_length=30, choices=REGIMEN_CHOICES, default='RESPONSABLE_IVA')
    exonerado_art_114_1 = models.BooleanField(default=True, verbose_name="Exonerado Art. 114-1 E.T. (Salud/SENA/ICBF)")
    perfil_operativo = models.CharField(max_length=20, choices=PERFIL_OPERATIVO_CHOICES, default='GENERAL', verbose_name="Perfil Operativo")

    direccion = models.CharField(max_length=200, blank=True, verbose_name="Dirección")
    departamento = models.ForeignKey(Departamento, on_delete=models.SET_NULL, null=True, blank=True)
    municipio = models.ForeignKey(Municipio, on_delete=models.SET_NULL, null=True, blank=True)
    telefono = models.CharField(max_length=50, blank=True, verbose_name="Teléfono")
    email = models.EmailField(blank=True, verbose_name="Correo Electrónico")
    logo = models.ImageField(upload_to='logos/', blank=True, null=True, verbose_name="Logo")

    # Configuración de Facturación DIAN y POS Electrónico (Resolución 000165)
    prefijo_factura = models.CharField(max_length=10, default='FE', verbose_name="Prefijo Factura")
    resolucion_dian = models.CharField(max_length=100, blank=True, verbose_name="Número Resolución DIAN")
    fecha_resolucion = models.DateField(null=True, blank=True, verbose_name="Fecha Resolución")
    rango_desde = models.BigIntegerField(default=1, verbose_name="Rango Desde")
    rango_hasta = models.BigIntegerField(default=5000, verbose_name="Rango Hasta")
    consecutivo_actual = models.BigIntegerField(default=1, verbose_name="Consecutivo Actual")
    prefijo_pos_electronico = models.CharField(max_length=10, default='POS', verbose_name="Prefijo Doc. Equivalente POS")
    consecutivo_pos_actual = models.BigIntegerField(default=1, verbose_name="Consecutivo Actual POS Electrónico")
    dias_vencimiento = models.IntegerField(default=30, verbose_name="Días de vencimiento por defecto")
    modo_dian = models.CharField(max_length=20, choices=MODO_DIAN_CHOICES, default='PRUEBAS')
    dian_software_id = models.CharField(max_length=120, blank=True, verbose_name="Software ID DIAN")
    dian_pin = models.CharField(max_length=255, blank=True, verbose_name="PIN Software DIAN")
    dian_test_set_id = models.CharField(max_length=120, blank=True, verbose_name="TestSetId Habilitación DIAN")

    # Servidor de Correo Independiente de la Empresa (Resend o SMTP propio; vacío = oficial ServiGest)
    resend_api_key = models.CharField(max_length=255, blank=True, verbose_name="Resend API Key Propia")
    smtp_host = models.CharField(max_length=150, blank=True, verbose_name="Host SMTP")
    smtp_port = models.IntegerField(default=587, verbose_name="Puerto SMTP")
    smtp_user = models.CharField(max_length=150, blank=True, verbose_name="Usuario SMTP")
    smtp_password = models.CharField(max_length=255, blank=True, verbose_name="Contraseña SMTP")
    smtp_encryption = models.CharField(max_length=10, default='tls', choices=[('tls', 'TLS'), ('ssl', 'SSL'), ('none', 'Ninguna')])
    smtp_from_address = models.CharField(max_length=150, blank=True, verbose_name="Correo Remitente")
    smtp_from_name = models.CharField(max_length=150, blank=True, verbose_name="Nombre Remitente")
    webhook_notificaciones_url = models.URLField(max_length=300, blank=True, verbose_name="Webhook Externo de Notificaciones")

    # Wompi (Pagos en línea Colombia)
    wompi_public_key = models.CharField(max_length=200, blank=True)
    wompi_private_key = models.CharField(max_length=255, blank=True)
    wompi_integrity_secret = models.CharField(max_length=255, blank=True)
    wompi_events_secret = models.CharField(max_length=255, blank=True)
    wompi_sandbox = models.BooleanField(default=True)

    # Multi-tenancy & Filiales
    empresa_padre = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='filiales')
    plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name='empresas')
    plan_fecha_vencimiento = models.DateField(null=True, blank=True, verbose_name="Vencimiento del Plan")
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

    def get_dian_pin(self) -> str:
        from apps.core.platform_settings import decrypt_secret
        return decrypt_secret(self.dian_pin).strip()

    def obtener_siguiente_consecutivo(self):
        num = self.consecutivo_actual
        self.consecutivo_actual += 1
        self.save(update_fields=['consecutivo_actual'])
        return num

    def obtener_siguiente_consecutivo_pos(self):
        num = self.consecutivo_pos_actual
        self.consecutivo_pos_actual += 1
        self.save(update_fields=['consecutivo_pos_actual'])
        return num

    def tiene_modulo(self, codigo):
        if not self.modulos.exists():
            return True
        return self.empresamodulo_set.filter(modulo__codigo=codigo, activo=True).exists()

    def documentos_mes_actual(self):
        from datetime import date
        from apps.facturacion.models import Factura
        hoy = date.today()
        return Factura.objects.filter(
            empresa=self,
            fecha_emision__year=hoy.year,
            fecha_emision__month=hoy.month,
            tipo_documento__in=('FACTURA', 'POS_ELECTRONICO'),
        ).exclude(estado='ANULADA').count()

    def puede_emitir_documento(self):
        if not self.plan or self.plan.limite_facturas_mes == 0:
            return True
        return self.documentos_mes_actual() < self.plan.limite_facturas_mes

    def puede_agregar_usuario(self):
        if not self.plan or self.plan.limite_usuarios == 0:
            return True
        activos = self.usuarios_asignados.filter(activo=True).count()
        return activos < self.plan.limite_usuarios

    def sincronizar_modulos_con_plan(self, plan):
        """Sincroniza los módulos habilitados de la empresa con los del plan comercial."""
        if not plan:
            return
        plan_mods = list(plan.modulos.all())
        if not plan_mods:
            return
        EmpresaModulo.objects.filter(empresa=self).exclude(modulo__in=plan_mods).delete()
        for mod in plan_mods:
            EmpresaModulo.objects.update_or_create(
                empresa=self,
                modulo=mod,
                defaults={'activo': True},
            )

    def renovar_fecha_plan(self, *, save: bool = True):
        """Calcula y asigna la fecha de vencimiento según la duración del plan contratado."""
        from datetime import date, timedelta

        if self.plan and self.plan.precio_mensual > 0:
            base = self.plan_fecha_vencimiento if (self.plan_fecha_vencimiento and self.plan_fecha_vencimiento > date.today()) else date.today()
            self.plan_fecha_vencimiento = base + timedelta(days=30 * max(1, int(self.plan.duracion_meses or 1)))
        else:
            self.plan_fecha_vencimiento = None
        if save:
            self.save(update_fields=['plan_fecha_vencimiento'])
        return self.plan_fecha_vencimiento


class EmpresaModulo(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    modulo = models.ForeignKey(Modulo, on_delete=models.CASCADE)
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('empresa', 'modulo')
        verbose_name = "Módulo de Empresa"
        verbose_name_plural = "Módulos de Empresas"


class SolicitudCambioPlan(models.Model):
    ESTADOS = [
        ('PENDIENTE', 'Pendiente'),
        ('APROBADA', 'Aprobada'),
        ('RECHAZADA', 'Rechazada'),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='solicitudes_plan')
    plan_actual = models.ForeignKey(Plan, null=True, blank=True, on_delete=models.SET_NULL, related_name='solicitudes_origen')
    plan_solicitado = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name='solicitudes_destino')
    solicitado_por = models.ForeignKey('usuarios.User', null=True, blank=True, on_delete=models.SET_NULL)
    estado = models.CharField(max_length=15, choices=ESTADOS, default='PENDIENTE')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Solicitud de Cambio de Plan"
        verbose_name_plural = "Solicitudes de Cambio de Plan"
