from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from apps.usuarios.models import User, EmpresaUsuario


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
    if not empresa:
        return redirect('core:dashboard')

    usuarios_asignados = EmpresaUsuario.objects.filter(
        empresa=empresa
    ).select_related('user').order_by('-rol', 'user__first_name')

    return render(request, 'usuarios/lista.html', {'usuarios_asignados': usuarios_asignados})


@login_required
def usuario_crear(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    if not request.user.es_admin(empresa):
        messages.error(request, "No tiene permisos para agregar usuarios a esta empresa.")
        return redirect('usuarios:lista')

    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        rol = request.POST.get('rol', 'operador')
        password = request.POST.get('password', '').strip()

        if not email or not password:
            messages.error(request, "El correo electrónico y la contraseña son obligatorios.")
            return render(request, 'usuarios/crear.html')

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
def usuario_cambiar_rol(request, asignacion_id):
    empresa = request.empresa_activa
    if not empresa or not request.user.es_admin(empresa):
        messages.error(request, "No tiene permisos para modificar roles.")
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
def usuario_cambiar_estado(request, asignacion_id):
    empresa = request.empresa_activa
    if not empresa or not request.user.es_admin(empresa):
        messages.error(request, "No tiene permisos para modificar el estado de usuarios.")
        return redirect('usuarios:lista')

    asignacion = get_object_or_404(EmpresaUsuario, id=asignacion_id, empresa=empresa)
    if asignacion.user == request.user and not request.user.is_superadmin:
        messages.error(request, "No puede desactivar su propio acceso a la empresa.")
        return redirect('usuarios:lista')

    asignacion.activo = not asignacion.activo
    asignacion.save()
    estado_str = "activado" if asignacion.activo else "desactivado"
    messages.success(request, f"Acceso de {asignacion.user.username} a la empresa ha sido {estado_str}.")
    return redirect('usuarios:lista')
