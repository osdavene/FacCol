from django.urls import path
from apps.contabilidad import views

app_name = 'contabilidad'

urlpatterns = [
    path('', views.plan_cuentas_lista, name='index'),
    path('asientos/', views.asientos_lista, name='asientos_lista'),
    path('asientos/nuevo/', views.asiento_crear, name='asiento_crear'),
    path('puc/', views.plan_cuentas_lista, name='plan_cuentas_lista'),
    path('puc/importar/', views.puc_importar, name='puc_importar'),
    path('estados-financieros/', views.estados_financieros_view, name='estados_financieros'),
    path('auxiliar-terceros/', views.auxiliar_terceros_view, name='auxiliar_terceros'),
    path('egresos/', views.egresos_lista, name='egresos_lista'),
    path('egresos/nuevo/', views.egreso_crear, name='egreso_crear'),
    path('gastos/', views.gastos_lista, name='gastos_lista'),
    path('gastos/nuevo/', views.gasto_crear, name='gasto_crear'),
    path('cierres/', views.cierres_lista, name='cierres_lista'),
    path('certificados-retencion/', views.certificados_retencion_view, name='certificados_retencion'),
    path('exogena/', views.exogena_resumen_view, name='exogena_resumen'),
    path('exogena/xml/', views.exogena_xml_descargar, name='exogena_xml'),
    path('centros-costos/', views.centros_costos_lista, name='centros_costos'),
    path('conciliacion-bancaria/', views.conciliacion_bancaria_view, name='conciliacion_bancaria'),
    path('activos-fijos/', views.activos_fijos_lista, name='activos_fijos'),
    path('formularios-tributarios/', views.formularios_tributarios_view, name='formularios_tributarios'),
    path('gastos/extraer-pdf/', views.gasto_extraer_pdf, name='gasto_extraer_pdf'),
]
