from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib import messages
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from apps.usuarios.models import User, EmpresaUsuario
from apps.usuarios.decorators import requiere_admin
from apps.core.mail_service import enviar_recuperacion_contrasena


def login_view(request):
    if request.user.is_authenticated:
        return redirect('core:dashboard')

    if request.method == 'POST':
        login_id = request.POST.get('login', '').strip()
        password = request.POST.get('password', '')

        # Intentar autenticar por username o email
        user = authenticate(request, username=login_id, password=password)
        if not user:
            # Buscar por email
            try:
                user_obj = User.objects.get(email__iexact=login_id)
                user = authenticate(request, username=user_obj.username, password=password)
            except User.DoesNotExist:
                user = None

        if user:
            if not user.activo:
                messages.error(request, "Esta cuenta ha sido desactivada. Comuníquese con el administrador.")
                return render(request, 'usuarios/login.html')

            login(request, user)
            messages.success(request, f"¡Bienvenido de nuevo, {user.first_name or user.username}!")
            next_url = request.GET.get('next') or 'core:dashboard'
            return redirect(next_url)
        else:
            messages.error(request, "Credenciales incorrectas. Verifique su usuario o contraseña.")

    return render(request, 'usuarios/login.html')


def logout_view(request):
    logout(request)
    messages.info(request, "Ha cerrado sesión de forma segura.")
    return redirect('usuarios:login')


@login_required
def perfil_view(request):
    user = request.user
    if request.method == 'POST':
        user.first_name = request.POST.get('first_name', user.first_name).strip()
        user.last_name = request.POST.get('last_name', user.last_name).strip()
        user.telefono = request.POST.get('telefono', user.telefono).strip()
        tema = request.POST.get('tema')
        if tema in ('oscuro', 'claro', 'dashtrap'):
            user.tema = tema

        if 'avatar' in request.FILES:
            user.avatar = request.FILES['avatar']

        # Cambio de contraseña opcional
        nuevo_pwd = request.POST.get('nuevo_password', '').strip()
        if nuevo_pwd:
            if len(nuevo_pwd) >= 6:
                user.set_password(nuevo_pwd)
                login(request, user)
                messages.success(request, "Contraseña actualizada exitosamente.")
            else:
                messages.error(request, "La nueva contraseña debe tener mínimo 6 caracteres.")

        user.save()
        messages.success(request, "Perfil actualizado correctamente.")
        return redirect('usuarios:perfil')

    return render(request, 'usuarios/perfil.html')


@login_required
def usuarios_lista(request):
    empresa = request.empresa_activa
    is_sa = bool(request.user.is_superadmin or request.user.is_superuser)

    if not (is_sa or (empresa and request.user.es_admin(empresa))):
        messages.warning(request, "Acceso restringido: Solo administradores o propietarios pueden gestionar el equipo.")
        return redirect('core:dashboard')

    if is_sa and not empresa:
        usuarios_asignados = EmpresaUsuario.objects.all().select_related('user', 'empresa').order_by('empresa__razon_social', '-rol')
    else:
        usuarios_asignados = EmpresaUsuario.objects.filter(
            empresa=empresa
        ).select_related('user', 'empresa').order_by('-rol', 'user__first_name')

    return render(request, 'usuarios/lista.html', {
        'usuarios_asignados': usuarios_asignados,
        'empresa': empresa,
    })


@login_required
@requiere_admin
def usuario_crear(request):
    empresa = request.empresa_activa
    if not empresa:
        messages.warning(request, "Seleccione una empresa activa para asignar nuevos usuarios.")
        return redirect('core:dashboard')

    if request.method == 'POST':
        if not empresa.puede_agregar_usuario():
            messages.error(
                request,
                f"Alcanzaste el límite de usuarios de tu plan ({empresa.plan.limite_usuarios} usuarios). "
                "Solicita ampliación al administrador."
            )
            return redirect('usuarios:lista')

        email = request.POST.get('email', '').strip().lower()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        rol = request.POST.get('rol', 'operador')
        password = request.POST.get('password', '').strip()

        if not email or not password:
            messages.error(request, "El correo electrónico y la contraseña son obligatorios.")
            return render(request, 'usuarios/crear.html', {'roles': EmpresaUsuario.ROLES_CHOICES})

        user, created = User.objects.get_or_create(
            username=email,
            defaults={
                'email': email,
                'first_name': first_name,
                'last_name': last_name,
                'telefono': telefono,
            }
        )
        if created:
            user.set_password(password)
            user.save()

        # Asignar a la empresa
        EmpresaUsuario.objects.update_or_create(
            user=user,
            empresa=empresa,
            defaults={'rol': rol, 'activo': True}
        )

        messages.success(request, f"Usuario {user.email} configurado exitosamente con rol {rol}.")
        return redirect('usuarios:lista')

    roles = EmpresaUsuario.ROLES_CHOICES
    return render(request, 'usuarios/crear.html', {'roles': roles})


@login_required
@requiere_admin
def usuario_cambiar_rol(request, asignacion_id):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('usuarios:lista')

    asignacion = get_object_or_404(EmpresaUsuario, id=asignacion_id, empresa=empresa)
    if request.method == 'POST':
        nuevo_rol = request.POST.get('rol')
        if nuevo_rol in dict(EmpresaUsuario.ROLES_CHOICES):
            asignacion.rol = nuevo_rol
            asignacion.save()
            messages.success(request, f"Rol de {asignacion.user.get_full_name() or asignacion.user.username} actualizado a {asignacion.get_rol_display()}.")

    return redirect('usuarios:lista')


@login_required
@requiere_admin
def usuario_cambiar_estado(request, asignacion_id):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('usuarios:lista')

    asignacion = get_object_or_404(EmpresaUsuario, id=asignacion_id, empresa=empresa)
    if asignacion.user == request.user and not (request.user.is_superadmin or request.user.is_superuser):
        messages.error(request, "No puede desactivar su propio acceso a la empresa.")
        return redirect('usuarios:lista')

    asignacion.activo = not asignacion.activo
    asignacion.save()
    estado_str = "activado" if asignacion.activo else "desactivado"
    messages.success(request, f"Acceso de {asignacion.user.username} a la empresa ha sido {estado_str}.")
    return redirect('usuarios:lista')


def password_reset_request_view(request):
    if request.user.is_authenticated:
        return redirect('core:dashboard')

    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        if email:
            users = User.objects.filter(email__iexact=email, is_active=True, activo=True)
            for u in users:
                uid = urlsafe_base64_encode(force_bytes(u.pk))
                token = default_token_generator.make_token(u)
                reset_url = request.build_absolute_uri(
                    reverse('usuarios:password_reset_confirm', args=[uid, token])
                )
                try:
                    enviar_recuperacion_contrasena(u, reset_url)
                except Exception:
                    pass
        messages.success(
            request,
            "Si el correo está registrado en FacCol, recibirás un enlace para restablecer tu contraseña."
        )
        return redirect('usuarios:login')

    return render(request, 'usuarios/password_reset_form.html')


def password_reset_confirm_view(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except Exception:
        user = None

    token_valido = bool(user and default_token_generator.check_token(user, token))
    if not token_valido:
        messages.error(request, "El enlace de recuperación es inválido o ha expirado. Solicita uno nuevo.")
        return redirect('usuarios:password_reset')

    if request.method == 'POST':
        p1 = request.POST.get('password1', '').strip()
        p2 = request.POST.get('password2', '').strip()
        if len(p1) < 6:
            messages.error(request, "La nueva contraseña debe tener al menos 6 caracteres.")
        elif p1 != p2:
            messages.error(request, "Las contraseñas no coinciden.")
        else:
            user.set_password(p1)
            user.save(update_fields=['password'])
            messages.success(request, "Tu contraseña ha sido actualizada. Ya puedes iniciar sesión.")
            return redirect('usuarios:login')

    return render(request, 'usuarios/password_reset_confirm.html', {'token_valido': True})
