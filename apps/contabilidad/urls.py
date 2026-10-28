from django.urls import path
from apps.contabilidad import views

app_name = 'contabilidad'

urlpatterns = [
    path('asientos/', views.asientos_lista, name='asientos_lista'),
    path('asientos/nuevo/', views.asiento_crear, name='asiento_crear'),
    path('puc/', views.plan_cuentas_lista, name='plan_cuentas_lista'),
]
