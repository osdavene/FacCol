from django.utils.deprecation import MiddlewareMixin
from apps.empresas.models import Empresa


class TenantMiddleware(MiddlewareMixin):
    """
    Middleware que resuelve la empresa activa para la sesión actual del usuario autenticado.
    Aislamiento Estricto SaaS: Solo se asigna como activa una empresa donde el usuario
    tenga una membresía activa explícita en EmpresaUsuario.
    """
    def process_request(self, request):
        request.empresa_activa = None

        if not request.user.is_authenticated:
            return

        # Si el usuario seleccionó una empresa en la sesión
        empresa_id = request.session.get('empresa_activa_id')

        if empresa_id:
            try:
                asignacion = request.user.empresas_asignadas.filter(
                    empresa_id=empresa_id,
                    activo=True,
                    empresa__activa=True
                ).select_related('empresa').first()
                if asignacion:
                    request.empresa_activa = asignacion.empresa
            except Exception:
                request.empresa_activa = None

        # Si no hay empresa activa seleccionada y el usuario tiene empresas asignadas
        if not request.empresa_activa:
            primera_asig = request.user.empresas_asignadas.filter(
                activo=True,
                empresa__activa=True
            ).select_related('empresa').first()
            if primera_asig:
                request.empresa_activa = primera_asig.empresa
                request.session['empresa_activa_id'] = primera_asig.empresa.id

        request.user._empresa_activa = request.empresa_activa
