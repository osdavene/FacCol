from django.urls import path
from apps.core import views

app_name = 'core'

urlpatterns = [
    path('', views.inicio, name='dashboard'),
    path('configuracion/', views.configuracion_empresa, name='configuracion_empresa'),
    path('configuracion/probar-correo/', views.configuracion_empresa_probar_correo, name='configuracion_empresa_probar_correo'),
    path('auditoria/', views.auditoria_empresa_view, name='auditoria_empresa'),
    path('mi-plan/', views.mi_plan_view, name='mi_plan'),
    path('mi-plan/solicitar/', views.solicitar_cambio_plan, name='solicitar_cambio_plan'),
    path('suscripcion/wompi/iniciar/', views.suscripcion_wompi_iniciar, name='suscripcion_wompi_iniciar'),
    path('suscripcion/wompi/retorno/', views.suscripcion_wompi_retorno, name='suscripcion_wompi_retorno'),
    path('empresa/cambiar/<int:empresa_id>/', views.cambiar_empresa, name='cambiar_empresa'),
    path('tema/cambiar/<str:tema>/', views.cambiar_tema, name='cambiar_tema'),
    path('api/busqueda-global/', views.busqueda_global, name='busqueda_global'),
    path('notificaciones/<int:notif_id>/leer/', views.notificacion_marcar_leida, name='notificacion_marcar_leida'),
    path('notificaciones/leer-todas/', views.notificaciones_marcar_todas_leidas, name='notificaciones_marcar_todas_leidas'),
]
