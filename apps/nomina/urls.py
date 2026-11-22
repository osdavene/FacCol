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
    path('liquidaciones/<int:nomina_id>/enviar-colillas/', views.nomina_enviar_todas_colillas, name='nomina_enviar_todas_colillas'),
    path('liquidaciones/<int:nomina_id>/pila/', views.nomina_exportar_pila, name='nomina_exportar_pila'),
    path('liquidaciones/<int:nomina_id>/transmitir-dian/', views.nomina_transmitir_lote, name='nomina_transmitir_lote'),
    path('desprendibles/<int:detalle_id>/', views.nomina_desprendible, name='nomina_desprendible'),
    path('desprendibles/<int:detalle_id>/xml/', views.nomina_xml_empleado, name='nomina_xml_empleado'),
    path('desprendibles/<int:detalle_id>/enviar/', views.nomina_enviar_colilla, name='nomina_enviar_colilla'),
]
