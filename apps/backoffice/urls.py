from django.urls import path
from apps.backoffice import views

app_name = 'backoffice'

urlpatterns = [
    path('', views.backoffice_dashboard, name='dashboard'),
    path('empresas/', views.empresas_lista, name='empresas_lista'),
    path('empresas/nueva/', views.empresa_crear, name='empresa_crear'),
    path('empresas/<int:empresa_id>/editar/', views.empresa_editar, name='empresa_editar'),
    path('empresas/<int:empresa_id>/toggle-activa/', views.empresa_toggle_activa, name='empresa_toggle_activa'),
]
