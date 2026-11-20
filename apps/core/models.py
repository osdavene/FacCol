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
    (Correo SES/Resend y Pasarela Wompi), evitando exponer secretos en .env.
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
