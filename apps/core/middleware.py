from django.utils.deprecation import MiddlewareMixin
from apps.empresas.models import Empresa


class TenantMiddleware(MiddlewareMixin):
    """
    Middleware que resuelve la empresa activa para la sesión actual del usuario autenticado.
    Coloca `request.empresa_activa` disponible en toda la aplicación.
    """
    def process_request(self, request):
        request.empresa_activa = None

        if not request.user.is_authenticated:
            return

        # Si el usuario es superadmin y no ha seleccionado empresa
        empresa_id = request.session.get('empresa_activa_id')

        if empresa_id:
            try:
                if request.user.is_superadmin or request.user.is_superuser:
                    request.empresa_activa = Empresa.objects.filter(id=empresa_id, activa=True).first()
                else:
                    asignacion = request.user.empresas_asignadas.filter(
                        empresa_id=empresa_id,
                        activo=True,
                        empresa__activa=True
                    ).select_related('empresa').first()
                    if asignacion:
                        request.empresa_activa = asignacion.empresa
            except Exception:
                request.empresa_activa = None

        # Si no hay empresa activa en sesión, seleccionar la primera disponible para el usuario
        if not request.empresa_activa:
            if request.user.is_superadmin or request.user.is_superuser:
                primera = Empresa.objects.filter(activa=True).first()
                if primera:
                    request.empresa_activa = primera
                    request.session['empresa_activa_id'] = primera.id
            else:
                primera_asig = request.user.empresas_asignadas.filter(
                    activo=True,
                    empresa__activa=True
                ).select_related('empresa').first()
                if primera_asig:
                    request.empresa_activa = primera_asig.empresa
                    request.session['empresa_activa_id'] = primera_asig.empresa.id

        request.user._empresa_activa = request.empresa_activa
