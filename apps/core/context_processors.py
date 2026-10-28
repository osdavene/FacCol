from apps.empresas.models import Empresa


def global_context(request):
    empresa = getattr(request, 'empresa_activa', None)
    tema = 'oscuro'
    todas_empresas = []
    user_rol = None
    is_superadmin_user = False

    if request.user.is_authenticated:
        tema = getattr(request.user, 'tema', 'oscuro') or 'oscuro'
        is_superadmin_user = bool(
            getattr(request.user, 'is_superadmin', False) or 
            getattr(request.user, 'is_superuser', False)
        )

        if is_superadmin_user:
            user_rol = 'superadmin'
            todas_empresas = []
            empresa = None
        else:
            # En Aislamiento Estricto SaaS:
            # El usuario únicamente ve en su selector las empresas donde tiene membresía activa
            todas_empresas = [
                eu.empresa for eu in request.user.empresas_asignadas.filter(
                    activo=True, empresa__activa=True
                ).select_related('empresa')
            ]
            if empresa:
                user_rol = request.user.rol_en_empresa(empresa)

    return {
        'empresa_activa': empresa,
        'todas_empresas': todas_empresas,
        'tema_actual': tema,
        'user_rol': user_rol,
        'is_superadmin_user': is_superadmin_user,
        'app_name': 'FacCol',
        'app_version': '3.0.0 (Python Edition)',
    }
