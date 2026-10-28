from apps.empresas.models import Empresa


def global_context(request):
    empresa = getattr(request, 'empresa_activa', None)
    tema = 'oscuro'
    todas_empresas = []

    if request.user.is_authenticated:
        tema = getattr(request.user, 'tema', 'oscuro') or 'oscuro'
        if getattr(request.user, 'is_superadmin', False):
            todas_empresas = list(Empresa.objects.filter(activa=True)[:30])
        else:
            todas_empresas = [
                eu.empresa for eu in request.user.empresas_asignadas.filter(
                    activo=True, empresa__activa=True
                ).select_related('empresa')
            ]

    return {
        'empresa_activa': empresa,
        'todas_empresas': todas_empresas,
        'tema_actual': tema,
        'app_name': 'FacCol',
        'app_version': '3.0.0 (Python Edition)',
    }
