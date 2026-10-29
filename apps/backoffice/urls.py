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
]
