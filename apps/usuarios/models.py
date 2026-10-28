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
        if self.is_superadmin or self.is_superuser:
            from apps.empresas.models import Empresa
            return Empresa.objects.filter(activa=True)
        return [eu.empresa for eu in self.empresas_asignadas.filter(activo=True, empresa__activa=True).select_related('empresa')]

    def es_superadmin_user(self):
        return bool(self.is_superadmin or self.is_superuser)

    def rol_en_empresa(self, empresa=None):
        empresa = empresa or getattr(self, '_empresa_activa', None)
        if not empresa:
            return 'superadmin' if self.es_superadmin_user() else None
        asignacion = self.empresas_asignadas.filter(empresa=empresa, activo=True).first()
        if asignacion:
            return asignacion.rol
        # Si es superadmin pero no está asignado a esta empresa, no tiene rol operativo en ella
        return 'superadmin' if self.es_superadmin_user() else None

    @property
    def rol_actual(self):
        if not getattr(self, '_empresa_activa', None) and self.es_superadmin_user():
            return 'superadmin'
        return self.rol_en_empresa()

    @property
    def rol_display(self):
        r = self.rol_actual
        roles_dict = {
            'superadmin': 'Superadministrador Global',
            'propietario': 'Propietario',
            'admin': 'Administrador General',
            'contador': 'Contador / Financiero',
            'vendedor': 'Vendedor / Facturador',
            'operador': 'Operador de Almacén',
        }
        return roles_dict.get(r, 'Usuario')

    def es_propietario(self, empresa=None):
        empresa = empresa or getattr(self, '_empresa_activa', None)
        if not empresa:
            return False
        asignacion = self.empresas_asignadas.filter(empresa=empresa, activo=True).first()
        return bool(asignacion and asignacion.rol == 'propietario')

    def es_admin(self, empresa=None):
        empresa = empresa or getattr(self, '_empresa_activa', None)
        if not empresa:
            return False
        asignacion = self.empresas_asignadas.filter(empresa=empresa, activo=True).first()
        return bool(asignacion and asignacion.rol in ('propietario', 'admin'))

    def es_contador(self, empresa=None):
        empresa = empresa or getattr(self, '_empresa_activa', None)
        if not empresa:
            return False
        asignacion = self.empresas_asignadas.filter(empresa=empresa, activo=True).first()
        return bool(asignacion and asignacion.rol in ('propietario', 'admin', 'contador'))

    def es_vendedor(self, empresa=None):
        empresa = empresa or getattr(self, '_empresa_activa', None)
        if not empresa:
            return False
        asignacion = self.empresas_asignadas.filter(empresa=empresa, activo=True).first()
        return bool(asignacion and asignacion.rol in ('propietario', 'admin', 'vendedor'))

    def es_operador(self, empresa=None):
        empresa = empresa or getattr(self, '_empresa_activa', None)
        if not empresa:
            return False
        asignacion = self.empresas_asignadas.filter(empresa=empresa, activo=True).first()
        return bool(asignacion and asignacion.rol in ('propietario', 'admin', 'operador'))

    def puede_facturar(self, empresa=None):
        empresa = empresa or getattr(self, '_empresa_activa', None)
        if not empresa:
            return False
        asignacion = self.empresas_asignadas.filter(empresa=empresa, activo=True).first()
        return bool(asignacion and asignacion.rol in ('propietario', 'admin', 'vendedor', 'contador'))

    def puede_inventario(self, empresa=None):
        empresa = empresa or getattr(self, '_empresa_activa', None)
        if not empresa:
            return False
        asignacion = self.empresas_asignadas.filter(empresa=empresa, activo=True).first()
        return bool(asignacion and asignacion.rol in ('propietario', 'admin', 'operador', 'vendedor'))

    def puede_contabilidad(self, empresa=None):
        empresa = empresa or getattr(self, '_empresa_activa', None)
        if not empresa:
            return False
        asignacion = self.empresas_asignadas.filter(empresa=empresa, activo=True).first()
        return bool(asignacion and asignacion.rol in ('propietario', 'admin', 'contador'))

    def puede_nomina(self, empresa=None):
        empresa = empresa or getattr(self, '_empresa_activa', None)
        if not empresa:
            return False
        asignacion = self.empresas_asignadas.filter(empresa=empresa, activo=True).first()
        return bool(asignacion and asignacion.rol in ('propietario', 'admin', 'contador'))


class EmpresaUsuario(models.Model):
    ROLES_CHOICES = [
        ('propietario', 'Propietario (Acceso Total)'),
        ('admin', 'Administrador General'),
        ('contador', 'Contador / Financiero'),
        ('vendedor', 'Vendedor / Facturador'),
        ('operador', 'Operador de Almacén'),
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
