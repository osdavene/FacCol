from django.urls import path
from apps.inventario import views

app_name = 'inventario'

urlpatterns = [
    path('productos/', views.productos_lista, name='productos_lista'),
    path('productos/nuevo/', views.producto_crear, name='producto_crear'),
    path('productos/<int:producto_id>/editar/', views.producto_editar, name='producto_editar'),
    path('kardex/', views.kardex_view, name='kardex'),
    path('ajuste/', views.ajuste_inventario_crear, name='ajuste_crear'),
    path('compras/', views.compras_lista, name='compras_lista'),
    path('compras/nueva/', views.compra_crear, name='compra_crear'),
    path('compras/<int:compra_id>/', views.compra_detalle, name='compra_detalle'),
    path('compras/<int:compra_id>/xml-dse/', views.compra_xml_dse, name='compra_xml_dse'),
    path('categorias/', views.categorias_lista, name='categorias_lista'),
    path('unidades/', views.unidades_lista, name='unidades_lista'),
]
