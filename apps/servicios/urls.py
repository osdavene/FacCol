from django.urls import path
from apps.servicios import views

app_name = 'servicios'

urlpatterns = [
    path('panel/', views.servicios_panel, name='panel'),
    path('kanban/', views.kanban_view, name='kanban'),
    path('api/kanban/cambiar-estado/', views.kanban_cambiar_estado, name='kanban_cambiar_estado'),
    path('ordenes/', views.ordenes_lista, name='ordenes_lista'),
    path('ordenes/nueva/', views.orden_crear, name='orden_crear'),
    path('ordenes/<int:orden_id>/', views.orden_detalle, name='orden_detalle'),
]
