from django.db.models import Q
from apps.core.models import Notificacion
from apps.empresas.models import Empresa


def global_context(request):
    empresa = getattr(request, 'empresa_activa', None)
    tema = 'oscuro'
    todas_empresas = []
    user_rol = None
    is_superadmin_user = False

    todos_codigos = ['facturacion', 'pos', 'terceros', 'inventario', 'contabilidad', 'nomina', 'ia']
    modulos_activos = set(todos_codigos)

    notificaciones_recientes = []
    notificaciones_no_leidas_count = 0

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
                if empresa.modulos.exists():
                    modulos_activos = set(
                        empresa.empresamodulo_set.filter(activo=True).values_list('modulo__codigo', flat=True)
                    )

        qs_notif = Notificacion.objects.filter(usuario=request.user)
        if not is_superadmin_user and empresa is not None:
            qs_notif = qs_notif.filter(Q(empresa=empresa) | Q(empresa__isnull=True))
        notificaciones_no_leidas_count = qs_notif.filter(leida=False).count()
        notificaciones_recientes = list(qs_notif.order_by('-created_at')[:8])

    return {
        'empresa_activa': empresa,
        'todas_empresas': todas_empresas,
        'modulos_activos': modulos_activos,
        'es_perfil_tienda': bool(empresa and empresa.perfil_operativo == 'TIENDA'),
        'tema_actual': tema,
        'user_rol': user_rol,
        'is_superadmin_user': is_superadmin_user,
        'notificaciones_recientes': notificaciones_recientes,
        'notificaciones_no_leidas_count': notificaciones_no_leidas_count,
        'app_name': 'FacCol',
        'app_version': '3.0.0 (Python Edition)',
    }
