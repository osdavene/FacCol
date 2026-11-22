from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from apps.facturacion import views as facturacion_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.core.urls')),
    path('portal/<str:token>/', facturacion_views.portal_factura_ver, name='portal_factura_ver'),
    path('portal/<str:token>/pdf/', facturacion_views.portal_factura_pdf, name='portal_factura_pdf'),
    path('portal/<str:token>/attached-xml/', facturacion_views.portal_factura_xml, name='portal_factura_xml'),
    path('portal/<str:token>/wompi/', facturacion_views.portal_factura_pagar_wompi, name='portal_factura_pagar_wompi'),
    path('auth/', include('apps.usuarios.urls')),
    path('terceros/', include('apps.terceros.urls')),
    path('inventario/', include('apps.inventario.urls')),
    path('facturacion/', include('apps.facturacion.urls')),
    path('pos/', include('apps.pos.urls')),
    path('contabilidad/', include('apps.contabilidad.urls')),
    path('nomina/', include('apps.nomina.urls')),
    path('backoffice/', include('apps.backoffice.urls')),
    path('ia/', include('apps.ia.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
