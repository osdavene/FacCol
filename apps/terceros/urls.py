from django.urls import path
from apps.terceros import views

app_name = 'terceros'

urlpatterns = [
    path('', views.terceros_lista, name='lista'),
    path('nuevo/', views.tercero_crear, name='crear'),
    path('importar/', views.terceros_importar, name='importar'),
    path('importar-rut/', views.tercero_importar_rut_pdf, name='importar_rut'),
    path('<int:tercero_id>/', views.tercero_detalle, name='detalle'),
    path('<int:tercero_id>/editar/', views.tercero_editar, name='editar'),
    path('<int:tercero_id>/eliminar/', views.tercero_eliminar, name='eliminar'),
]
