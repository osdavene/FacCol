from django.db import models
from apps.core.models import EmpresaTenantModel


class ConversacionIA(EmpresaTenantModel):
    usuario = models.ForeignKey('usuarios.User', on_delete=models.CASCADE, related_name='conversaciones_ia')
    titulo = models.CharField(max_length=200, default="Nueva consulta con FacCol IA")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Conversación IA"
        verbose_name_plural = "Conversaciones IA"
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.titulo} ({self.usuario.username})"


class MensajeIA(models.Model):
    ROL_CHOICES = [
        ('user', 'Usuario'),
        ('model', 'FacCol IA'),
    ]

    conversacion = models.ForeignKey(ConversacionIA, on_delete=models.CASCADE, related_name='mensajes')
    rol = models.CharField(max_length=20, choices=ROL_CHOICES)
    contenido = models.TextField()
    tokens_usados = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mensaje IA"
        verbose_name_plural = "Mensajes IA"
        ordering = ['created_at']

    def __str__(self):
        return f"{self.rol}: {self.contenido[:40]}..."
