from django.urls import path
from apps.nomina import views

app_name = 'nomina'

urlpatterns = [
    path('empleados/', views.empleados_lista, name='empleados_lista'),
    path('empleados/nuevo/', views.empleado_crear, name='empleado_crear'),
    path('liquidaciones/', views.nominas_lista, name='nominas_lista'),
    path('liquidar/', views.nomina_liquidar, name='nomina_liquidar'),
    path('liquidaciones/<int:nomina_id>/', views.nomina_detalle, name='nomina_detalle'),
]
