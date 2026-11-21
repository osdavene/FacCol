from django.urls import path
from apps.nomina import views

app_name = 'nomina'

urlpatterns = [
    path('', views.nominas_lista, name='index'),
    path('empleados/', views.empleados_lista, name='empleados_lista'),
    path('empleados/nuevo/', views.empleado_crear, name='empleado_crear'),
    path('liquidaciones/', views.nominas_lista, name='nominas_lista'),
    path('liquidar/', views.nomina_liquidar, name='nomina_liquidar'),
    path('liquidaciones/<int:nomina_id>/', views.nomina_detalle, name='nomina_detalle'),
    path('desprendibles/<int:detalle_id>/', views.nomina_desprendible, name='nomina_desprendible'),
    path('desprendibles/<int:detalle_id>/xml/', views.nomina_xml_empleado, name='nomina_xml_empleado'),
    path('desprendibles/<int:detalle_id>/enviar/', views.nomina_enviar_colilla, name='nomina_enviar_colilla'),
]
