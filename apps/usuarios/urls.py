from django.urls import path
from apps.usuarios import views

app_name = 'usuarios'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('password-reset/', views.password_reset_request_view, name='password_reset'),
    path('password-reset/<uidb64>/<token>/', views.password_reset_confirm_view, name='password_reset_confirm'),
    path('perfil/', views.perfil_view, name='perfil'),
    path('equipo/', views.usuarios_lista, name='lista'),
    path('equipo/nuevo/', views.usuario_crear, name='crear'),
    path('equipo/<int:asignacion_id>/rol/', views.usuario_cambiar_rol, name='cambiar_rol'),
    path('equipo/<int:asignacion_id>/estado/', views.usuario_cambiar_estado, name='cambiar_estado'),
]
