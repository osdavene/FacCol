from django.urls import path
from apps.usuarios import views

app_name = 'usuarios'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('verificar/<uidb64>/<token>/', views.verificar_email_view, name='verificar_email'),
    path('logout/', views.logout_view, name='logout'),
    path('password-reset/', views.password_reset_request_view, name='password_reset'),
    path('password-reset/<uidb64>/<token>/', views.password_reset_confirm_view, name='password_reset_confirm'),
    path('perfil/', views.perfil_view, name='perfil'),
    path('equipo/', views.usuarios_lista, name='lista'),
    path('equipo/nuevo/', views.usuario_crear, name='crear'),
    path('equipo/<int:asignacion_id>/rol/', views.usuario_cambiar_rol, name='cambiar_rol'),
    path('equipo/<int:asignacion_id>/estado/', views.usuario_cambiar_estado, name='cambiar_estado'),
    path('equipo/<int:asignacion_id>/clave/', views.usuario_asignar_clave, name='asignar_clave'),
    path('equipo/<int:asignacion_id>/enviar-enlace/', views.usuario_enviar_enlace_clave, name='enviar_enlace_clave'),
    path('equipo/<int:asignacion_id>/notificar-admin/', views.usuario_notificar_admin_acceso, name='notificar_admin_acceso'),
    path('equipo/<int:asignacion_id>/actualizar-correo/', views.usuario_actualizar_correo, name='actualizar_correo'),
]
