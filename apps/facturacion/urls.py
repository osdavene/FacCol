from django.urls import path
from apps.facturacion import views

app_name = 'facturacion'

urlpatterns = [
    path('facturas/', views.facturas_lista, name='facturas_lista'),
    path('facturas/nueva/', views.factura_crear, name='factura_crear'),
    path('facturas/<int:factura_id>/', views.factura_detalle, name='factura_detalle'),
    path('facturas/<int:factura_id>/pdf/', views.factura_pdf, name='factura_pdf'),
    path('facturas/<int:factura_id>/tirilla/', views.factura_tirilla, name='factura_tirilla'),
    path('facturas/<int:factura_id>/xml/', views.factura_xml, name='factura_xml'),
    path('facturas/<int:factura_id>/enviar-email/', views.factura_enviar_email, name='factura_enviar_email'),
    path('fiados/', views.fiados_lista, name='fiados_lista'),
    path('fiados/recordar-lote/', views.fiados_recordar_lote, name='fiados_recordar_lote'),
    path('fiados/<int:factura_id>/recordatorio/', views.fiado_enviar_recordatorio, name='fiado_enviar_recordatorio'),
    path('recibos/', views.recibos_lista, name='recibos_lista'),
    path('recibos/nuevo/', views.recibo_crear, name='recibo_crear'),
    path('cotizaciones/', views.cotizaciones_lista, name='cotizaciones_lista'),
    path('cotizaciones/<int:factura_id>/convertir/', views.cotizacion_convertir_factura, name='cotizacion_convertir_factura'),
    path('remisiones/', views.remisiones_lista, name='remisiones_lista'),
    path('notas-credito/', views.notas_credito_lista, name='notas_credito_lista'),
    path('facturas/<int:factura_id>/nota-credito/', views.factura_emitir_nota_credito, name='factura_emitir_nota_credito'),
    path('dian-cola/', views.dian_cola_view, name='dian_cola'),
    path('dian-cola/procesar/', views.dian_procesar_lote, name='dian_procesar_lote'),
    path('facturas/<int:factura_id>/transmitir-dian/', views.dian_transmitir_documento, name='dian_transmitir_documento'),
    path('pagos/wompi/iniciar/<int:factura_id>/', views.wompi_iniciar, name='wompi_iniciar'),
    path('pagos/wompi/retorno/', views.wompi_retorno, name='wompi_retorno'),
    path('pagos/wompi/webhook/', views.wompi_webhook, name='wompi_webhook'),
]
