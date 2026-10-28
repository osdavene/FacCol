from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    TEMAS_CHOICES = [
        ('oscuro', 'Oscuro (Defecto)'),
        ('claro', 'Claro'),
        ('dashtrap', 'Dashtrap (Alto Contraste)'),
    ]

    telefono = models.CharField(max_length=30, blank=True, verbose_name="Teléfono")
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True, verbose_name="Avatar")
    tema = models.CharField(max_length=20, choices=TEMAS_CHOICES, default='oscuro', verbose_name="Tema de Interfaz")
    is_superadmin = models.BooleanField(default=False, verbose_name="Superadmin de Plataforma")
    activo = models.BooleanField(default=True, verbose_name="Usuario Activo")

    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def tema_normalizado(self):
        return self.tema or 'oscuro'

    def empresas_activas(self):
        if self.is_superadmin:
            from apps.empresas.models import Empresa
            return Empresa.objects.filter(activa=True)
        return [eu.empresa for eu in self.empresas_asignadas.filter(activo=True, empresa__activa=True).select_related('empresa')]

    def rol_en_empresa(self, empresa):
        if self.is_superadmin:
            return 'superadmin'
        if not empresa:
            return None
        asignacion = self.empresas_asignadas.filter(empresa=empresa, activo=True).first()
        return asignacion.rol if asignacion else None

    def es_propietario(self, empresa):
        if self.is_superadmin:
            return True
        rol = self.rol_en_empresa(empresa)
        return rol == 'propietario'

    def es_admin(self, empresa):
        if self.is_superadmin:
            return True
        rol = self.rol_en_empresa(empresa)
        return rol in ('propietario', 'admin')


class EmpresaUsuario(models.Model):
    ROLES_CHOICES = [
        ('propietario', 'Propietario (Acceso Total)'),
        ('admin', 'Administrador'),
        ('operador', 'Operador'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='empresas_asignadas')
    empresa = models.ForeignKey('empresas.Empresa', on_delete=models.CASCADE, related_name='usuarios_asignados')
    rol = models.CharField(max_length=20, choices=ROLES_CHOICES, default='operador')
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'empresa')
        verbose_name = "Usuario por Empresa"
        verbose_name_plural = "Usuarios por Empresas"

    def __str__(self):
        return f"{self.user} - {self.empresa} ({self.get_rol_display()})"
