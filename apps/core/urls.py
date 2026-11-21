from django.urls import path
from apps.core import views

app_name = 'core'

urlpatterns = [
    path('', views.inicio, name='dashboard'),
    path('configuracion/', views.configuracion_empresa, name='configuracion_empresa'),
    path('suscripcion/wompi/iniciar/', views.suscripcion_wompi_iniciar, name='suscripcion_wompi_iniciar'),
    path('suscripcion/wompi/retorno/', views.suscripcion_wompi_retorno, name='suscripcion_wompi_retorno'),
    path('empresa/cambiar/<int:empresa_id>/', views.cambiar_empresa, name='cambiar_empresa'),
    path('tema/cambiar/<str:tema>/', views.cambiar_tema, name='cambiar_tema'),
    path('api/busqueda-global/', views.busqueda_global, name='busqueda_global'),
]
