from django.db import models
from apps.core.models import EmpresaTenantModel


class Tercero(EmpresaTenantModel):
    TIPO_PERSONA_CHOICES = [
        ('NATURAL', 'Persona Natural'),
        ('JURIDICA', 'Persona Jurídica'),
    ]

    TIPO_DOCUMENTO_CHOICES = [
        ('CC', 'Cédula de Ciudadanía'),
        ('NIT', 'NIT (Número de Identificación Tributaria)'),
        ('CE', 'Cédula de Extranjería'),
        ('TI', 'Tarjeta de Identidad'),
        ('PASAPORTE', 'Pasaporte'),
        ('DIE', 'Documento de Identificación Extranjero'),
    ]

    REGIMEN_CHOICES = [
        ('RESPONSABLE_IVA', 'Responsable de IVA (Común)'),
        ('NO_RESPONSABLE_IVA', 'No Responsable de IVA (Simplificado)'),
        ('REGIMEN_SIMPLE', 'Régimen Simple de Tributación (RST)'),
        ('GRAN_CONTRIBUYENTE', 'Gran Contribuyente'),
        ('AUTORRETENEDOR', 'Autorretenedor'),
    ]

    es_cliente = models.BooleanField(default=True, verbose_name="Es Cliente")
    es_proveedor = models.BooleanField(default=False, verbose_name="Es Proveedor")

    tipo_persona = models.CharField(max_length=20, choices=TIPO_PERSONA_CHOICES, default='NATURAL')
    tipo_documento = models.CharField(max_length=20, choices=TIPO_DOCUMENTO_CHOICES, default='CC')
    numero_documento = models.CharField(max_length=30, verbose_name="Número de Documento")
    dv = models.CharField(max_length=1, blank=True, verbose_name="Dígito de Verificación")

    razon_social = models.CharField(max_length=200, verbose_name="Razón Social / Nombre Completo")
    nombre_comercial = models.CharField(max_length=200, blank=True, verbose_name="Nombre Comercial")

    direccion = models.CharField(max_length=200, blank=True, verbose_name="Dirección")
    departamento = models.ForeignKey('empresas.Departamento', on_delete=models.SET_NULL, null=True, blank=True)
    municipio = models.ForeignKey('empresas.Municipio', on_delete=models.SET_NULL, null=True, blank=True)

    telefono = models.CharField(max_length=50, blank=True, verbose_name="Teléfono")
    email = models.EmailField(blank=True, verbose_name="Correo Electrónico")

    regimen = models.CharField(max_length=30, choices=REGIMEN_CHOICES, default='NO_RESPONSABLE_IVA')
    responsabilidades_fiscales = models.CharField(max_length=150, blank=True, help_text="Códigos DIAN separados por coma (e.g. R-99-PN, O-13, O-15)")

    # Retenciones automáticas
    aplica_retefuente = models.BooleanField(default=False)
    tarifa_retefuente = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    aplica_reteica = models.BooleanField(default=False)
    tarifa_reteica = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    activo = models.BooleanField(default=True, verbose_name="Activo")
    observaciones = models.TextField(blank=True, verbose_name="Observaciones")

    class Meta:
        verbose_name = "Tercero"
        verbose_name_plural = "Terceros"
        ordering = ['razon_social']
        unique_together = ('empresa', 'numero_documento')

    def __str__(self):
        return f"{self.razon_social} ({self.numero_documento})"

    @property
    def documento_completo(self):
        if self.tipo_documento == 'NIT' and self.dv:
            return f"{self.numero_documento}-{self.dv}"
        return self.numero_documento
