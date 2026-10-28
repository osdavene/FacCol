from django.urls import path
from apps.pos import views

app_name = 'pos'

urlpatterns = [
    path('terminal/', views.pos_terminal, name='pos_terminal'),
    path('api/procesar-venta/', views.pos_procesar_venta, name='pos_procesar_venta'),
    path('cajas/', views.cajas_lista, name='cajas_lista'),
    path('cajas/abrir/', views.caja_abrir_turno, name='caja_abrir_turno'),
    path('cajas/turnos/<int:turno_id>/cerrar/', views.caja_cerrar_turno, name='caja_cerrar_turno'),
]
