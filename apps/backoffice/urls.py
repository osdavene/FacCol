from django.urls import path
from apps.backoffice import views

app_name = 'backoffice'

urlpatterns = [
    path('', views.backoffice_dashboard, name='dashboard'),
    path('empresas/', views.empresas_lista, name='empresas_lista'),
    path('empresas/nueva/', views.empresa_crear, name='empresa_crear'),
    path('empresas/<int:empresa_id>/editar/', views.empresa_editar, name='empresa_editar'),
    path('empresas/<int:empresa_id>/toggle-activa/', views.empresa_toggle_activa, name='empresa_toggle_activa'),
    
    # Gestión de Planes & Suscripciones SaaS
    path('planes/', views.planes_lista, name='planes_lista'),
    path('planes/nuevo/', views.plan_crear, name='plan_crear'),
    path('planes/<int:plan_id>/editar/', views.plan_editar, name='plan_editar'),
    path('planes/<int:plan_id>/toggle-activo/', views.plan_toggle_activo, name='plan_toggle_activo'),
    path('planes/<int:plan_id>/eliminar/', views.plan_eliminar, name='plan_eliminar'),
    path('planes/solicitudes/<int:solicitud_id>/resolver/', views.solicitud_plan_resolver, name='solicitud_plan_resolver'),

    # Configuración Global Cifrada (Correo y Wompi)
    path('configuracion/', views.configuracion_plataforma, name='configuracion_plataforma'),
    path('configuracion/probar-correo/', views.configuracion_probar_correo, name='configuracion_probar_correo'),

    # Comunicados Masivos Anti-Spam & Banner en Panel del Usuario
    path('comunicados/', views.comunicados_panel, name='comunicados_panel'),
    path('comunicados/guardar-borrador/', views.comunicado_guardar_borrador, name='comunicado_guardar_borrador'),
    path('comunicados/probar/', views.comunicado_probar, name='comunicado_probar'),
    path('comunicados/enviar-tanda/', views.comunicado_enviar_tanda, name='comunicado_enviar_tanda'),
    path('comunicados/reiniciar/', views.comunicado_reiniciar, name='comunicado_reiniciar'),
    path('comunicados/banner/', views.comunicado_guardar_banner, name='comunicado_guardar_banner'),
]
