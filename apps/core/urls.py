from django.urls import path
from apps.core import views

app_name = 'core'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('empresa/cambiar/<int:empresa_id>/', views.cambiar_empresa, name='cambiar_empresa'),
    path('tema/cambiar/<str:tema>/', views.cambiar_tema, name='cambiar_tema'),
    path('api/busqueda-global/', views.busqueda_global, name='busqueda_global'),
]
