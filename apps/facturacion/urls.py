from django.urls import path
from apps.facturacion import views

app_name = 'facturacion'

urlpatterns = [
    path('facturas/', views.facturas_lista, name='facturas_lista'),
    path('facturas/nueva/', views.factura_crear, name='factura_crear'),
    path('facturas/<int:factura_id>/', views.factura_detalle, name='factura_detalle'),
    path('facturas/<int:factura_id>/pdf/', views.factura_pdf, name='factura_pdf'),
    path('facturas/<int:factura_id>/enviar-email/', views.factura_enviar_email, name='factura_enviar_email'),
    path('recibos/', views.recibos_lista, name='recibos_lista'),
    path('recibos/nuevo/', views.recibo_crear, name='recibo_crear'),
    path('cotizaciones/', views.cotizaciones_lista, name='cotizaciones_lista'),
    path('remisiones/', views.remisiones_lista, name='remisiones_lista'),
    path('notas-credito/', views.notas_credito_lista, name='notas_credito_lista'),
]
