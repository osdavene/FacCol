from django.urls import path
from apps.inventario import views

app_name = 'inventario'

urlpatterns = [
    path('productos/', views.productos_lista, name='productos_lista'),
    path('productos/nuevo/', views.producto_crear, name='producto_crear'),
    path('productos/importar/', views.productos_importar, name='productos_importar'),
    path('productos/<int:producto_id>/editar/', views.producto_editar, name='producto_editar'),
    path('kardex/', views.kardex_view, name='kardex'),
    path('ajuste/', views.ajuste_inventario_crear, name='ajuste_crear'),
    path('compras/', views.compras_lista, name='compras_lista'),
    path('compras/nueva/', views.compra_crear, name='compra_crear'),
    path('compras/<int:compra_id>/', views.compra_detalle, name='compra_detalle'),
    path('compras/<int:compra_id>/xml-dse/', views.compra_xml_dse, name='compra_xml_dse'),
    path('compras/<int:compra_id>/transmitir-dse/', views.compra_transmitir_dse, name='compra_transmitir_dse'),
    path('categorias/', views.categorias_lista, name='categorias_lista'),
    path('unidades/', views.unidades_lista, name='unidades_lista'),
    path('bodegas/', views.bodegas_lista, name='bodegas_lista'),
    path('bodegas/traslado/', views.bodega_traslado, name='bodega_traslado'),
    path('ordenes-compra/', views.ordenes_compra_lista, name='ordenes_compra_lista'),
    path('ordenes-compra/<int:orden_id>/convertir/', views.orden_compra_convertir, name='orden_compra_convertir'),
    path('lotes/', views.lotes_lista, name='lotes_lista'),
    path('listas-precios/', views.listas_precios_lista, name='listas_precios_lista'),
    path('compras/importar-xml/', views.compra_importar_xml, name='compra_importar_xml'),
    path('compras/<int:compra_id>/radian/', views.compra_evento_radian, name='compra_evento_radian'),
]
