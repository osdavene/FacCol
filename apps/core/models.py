from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de creación")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Última actualización")

    class Meta:
        abstract = True


class TenantManager(models.Manager):
    def por_empresa(self, empresa):
        if not empresa:
            return self.none()
        return self.filter(empresa=empresa)


class EmpresaTenantModel(TimestampedModel):
    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
        verbose_name="Empresa"
    )

    objects = TenantManager()

    class Meta:
        abstract = True


class ConfiguracionPlataforma(models.Model):
    """
    Almacenamiento cifrado en base de datos para credenciales sensibles de plataforma
    (Correo Resend/SMTP y Pasarela Wompi), evitando exponer secretos en .env.
    """
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField(blank=True, default='')
    is_secret = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuración de Plataforma"
        verbose_name_plural = "Configuraciones de Plataforma"

    def __str__(self):
        return f"{self.key} ({'secreto' if self.is_secret else 'público'})"


class RegistroActividad(models.Model):
    """Bitácora inmutable multi-tenant: registra quién hizo qué, cuándo, dónde y sobre qué empresa."""

    usuario = models.ForeignKey(
        'usuarios.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs_actividad',
    )
    usuario_nombre = models.CharField(max_length=150, blank=True)
    rol = models.CharField(max_length=40, blank=True)
    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs_actividad',
    )
    modulo = models.CharField(max_length=40, db_index=True)
    accion = models.CharField(max_length=80, db_index=True)
    descripcion = models.TextField()
    objeto_tipo = models.CharField(max_length=60, blank=True)
    objeto_id = models.CharField(max_length=60, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    datos_extra = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['empresa', '-created_at']),
            models.Index(fields=['modulo', '-created_at']),
        ]
        verbose_name = "Registro de Actividad"
        verbose_name_plural = "Registros de Actividad"

    def __str__(self):
        return f"[{self.modulo}] {self.usuario_nombre}: {self.accion}"


class Notificacion(models.Model):
    """Centro de notificaciones in-app por usuario y empresa (campanita en barra superior)."""

    NIVEL_CHOICES = [
        ('info', 'Informativa'),
        ('success', 'Éxito / Pago'),
        ('warning', 'Advertencia'),
        ('danger', 'Crítica / Seguridad'),
    ]

    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notificaciones',
    )
    usuario = models.ForeignKey(
        'usuarios.User',
        on_delete=models.CASCADE,
        related_name='notificaciones',
    )
    categoria = models.CharField(max_length=40, default='sistema', db_index=True)
    nivel = models.CharField(max_length=20, choices=NIVEL_CHOICES, default='info')
    titulo = models.CharField(max_length=180)
    mensaje = models.TextField()
    url = models.CharField(max_length=255, blank=True, default='')
    leida = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['usuario', 'leida', '-created_at']),
            models.Index(fields=['empresa', 'categoria', '-created_at']),
        ]
        verbose_name = "Notificación"
        verbose_name_plural = "Notificaciones"

    def __str__(self):
        return f"[{self.nivel}] {self.usuario_id}: {self.titulo}"


class ApiTokenEmpresa(TimestampedModel):
    """Tokens REST Bearer por empresa (hash SHA-256) para integraciones ERP/e-commerce."""
    empresa = models.ForeignKey(
        'empresas.Empresa',
        on_delete=models.CASCADE,
        related_name='api_tokens',
    )
    nombre = models.CharField(max_length=100)
    prefijo = models.CharField(max_length=16, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    activo = models.BooleanField(default=True)
    ultimo_uso_at = models.DateTimeField(null=True, blank=True)
    creado_por = models.ForeignKey(
        'usuarios.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='api_tokens_creados',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Token API Empresarial"
        verbose_name_plural = "Tokens API Empresariales"

    def __str__(self):
        return f"{self.empresa.display_name} - {self.nombre} ({self.prefijo}...)"
