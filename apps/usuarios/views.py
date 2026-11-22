import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_POST

from apps.empresas.models import Empresa, Plan
from apps.usuarios.models import User, EmpresaUsuario
from apps.usuarios.decorators import requiere_admin
from apps.core.config import mail_settings
from apps.core.mail import (
    is_dummy_or_invalid_email,
    send_password_reset_email,
    send_verification_email,
)


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """Token firmado de un solo uso y con expiración para verificación de correo electrónico."""

    def _make_hash_value(self, user, timestamp):
        verified_ts = user.email_verified_at.isoformat() if user.email_verified_at else ""
        return f"{user.pk}{user.password}{user.is_active}{verified_ts}{timestamp}"


email_verification_token = EmailVerificationTokenGenerator()
logger = logging.getLogger("faccol.auth")


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
                if user.email_verified_at is None:
                    messages.warning(
                        request,
                        "Tu cuenta aún está pendiente de verificación. Revisa tu correo electrónico para activar el acceso."
                    )
                else:
                    messages.error(request, "Esta cuenta ha sido desactivada. Comuníquese con el administrador.")
                return render(request, 'usuarios/login.html')

            login(request, user)
            messages.success(request, f"¡Bienvenido de nuevo, {user.first_name or user.username}!")
            next_url = request.GET.get('next') or 'core:dashboard'
            return redirect(next_url)
        else:
            messages.error(request, "Credenciales incorrectas. Verifique su usuario o contraseña.")

    return render(request, 'usuarios/login.html')


def registro_view(request):
    """Registro público SaaS: crea Empresa + Propietario pendiente y envía correo de verificación."""
    if request.user.is_authenticated:
        return redirect('core:dashboard')

    if request.method == 'POST':
        razon_social = request.POST.get('razon_social', '').strip()
        nit = request.POST.get('nit', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        email = request.POST.get('email', '').strip().lower()
        perfil = request.POST.get('perfil_operativo', 'GENERAL').strip().upper()
        if perfil not in ('GENERAL', 'TIENDA'):
            perfil = 'GENERAL'
        p1 = request.POST.get('password1', '').strip()
        p2 = request.POST.get('password2', '').strip()

        form_data = {
            'razon_social': razon_social,
            'nit': nit,
            'first_name': first_name,
            'last_name': last_name,
            'telefono': telefono,
            'email': email,
            'perfil_operativo': perfil,
        }

        if not razon_social or not nit or not email or not p1:
            messages.error(request, "Completa razón social, NIT/cédula, correo electrónico y contraseña.")
            return render(request, 'usuarios/registro.html', {'form_data': form_data})
        if is_dummy_or_invalid_email(email):
            messages.error(request, "Ingresa una dirección de correo electrónico válida y real.")
            return render(request, 'usuarios/registro.html', {'form_data': form_data})
        if len(p1) < 6:
            messages.error(request, "La contraseña debe tener mínimo 6 caracteres.")
            return render(request, 'usuarios/registro.html', {'form_data': form_data})
        if p1 != p2:
            messages.error(request, "Las contraseñas no coinciden.")
            return render(request, 'usuarios/registro.html', {'form_data': form_data})
        if User.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).exists():
            messages.error(request, "Ya existe una cuenta registrada con ese correo electrónico.")
            return render(request, 'usuarios/registro.html', {'form_data': form_data})
        if Empresa.objects.filter(nit=nit).exists():
            messages.error(request, "Ya existe una empresa registrada con ese NIT o cédula.")
            return render(request, 'usuarios/registro.html', {'form_data': form_data})

        with transaction.atomic():
            plan_gratuito = Plan.objects.filter(activo=True, precio_mensual=0).order_by('id').first()
            empresa = Empresa.objects.create(
                razon_social=razon_social,
                nombre_comercial=razon_social,
                nit=nit,
                email=email,
                telefono=telefono,
                perfil_operativo=perfil,
                plan=plan_gratuito,
                activa=True,
            )
            if plan_gratuito:
                empresa.sincronizar_modulos_con_plan(plan_gratuito)

            user = User.objects.create_user(
                username=email,
                email=email,
                password=p1,
                first_name=first_name or razon_social[:30],
                last_name=last_name,
                telefono=telefono,
                activo=False,
                is_active=True,
                email_verified_at=None,
            )
            EmpresaUsuario.objects.create(
                user=user,
                empresa=empresa,
                rol='propietario',
                activo=True,
            )

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = email_verification_token.make_token(user)
        verify_url = request.build_absolute_uri(
            reverse('usuarios:verificar_email', args=[uid, token])
        )
        enviado = send_verification_email(user, verify_url)
        if enviado:
            messages.success(
                request,
                f"¡Empresa '{empresa.display_name}' creada! Enviamos un enlace de activación a {email} para habilitar tu acceso."
            )
        else:
            messages.warning(
                request,
                f"Tu cuenta para '{empresa.display_name}' fue creada, pero el servidor de correo no pudo entregar el enlace de verificación."
            )
        return redirect('usuarios:login')

    return render(request, 'usuarios/registro.html', {'form_data': {}})


def verificar_email_view(request, uidb64, token):
    """Activa cuenta nueva validando token firmado de un solo uso y expiración."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except Exception:
        user = None

    if not user or not email_verification_token.check_token(user, token):
        messages.error(request, "El enlace de verificación es inválido o ya fue utilizado.")
        return redirect('usuarios:login')

    user.email_verified_at = timezone.now()
    user.activo = True
    user.is_active = True
    user.save(update_fields=['email_verified_at', 'activo', 'is_active'])
    messages.success(request, "¡Correo verificado y empresa activada! Ya puedes iniciar sesión en FacCol.")
    return redirect('usuarios:login')


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
                'email_verified_at': timezone.now(),
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


@login_required
@require_POST
def usuario_asignar_clave(request, asignacion_id):
    """
    Permite al SuperAdmin global o al Administrador de la empresa asignar o restablecer
    una contraseña de acceso de forma inmediata desde la tabla de usuarios.
    """
    is_sa = bool(request.user.is_superadmin or request.user.is_superuser)
    empresa = request.empresa_activa

    if is_sa:
        asignacion = get_object_or_404(EmpresaUsuario.objects.select_related('user', 'empresa'), id=asignacion_id)
    elif empresa and request.user.es_admin(empresa):
        asignacion = get_object_or_404(
            EmpresaUsuario.objects.select_related('user', 'empresa'),
            id=asignacion_id,
            empresa=empresa,
        )
    else:
        messages.error(request, "No tienes permisos para asignar contraseñas a este usuario.")
        return redirect('core:dashboard')

    if asignacion.user.es_superadmin_user() and asignacion.user != request.user:
        messages.error(request, "No es posible modificar la contraseña de otro SuperAdmin desde esta vista.")
        return redirect('usuarios:lista')

    nueva_clave = request.POST.get('nueva_clave', '').strip()
    if nueva_clave and len(nueva_clave) < 6:
        messages.error(request, "La contraseña debe tener al menos 6 caracteres.")
        return redirect('usuarios:lista')

    clave_final = nueva_clave or get_random_string(12)
    u = asignacion.user
    u.set_password(clave_final)
    u.save(update_fields=['password'])

    messages.success(
        request,
        f"Contraseña actualizada para {u.email or u.username} ({asignacion.empresa.razon_social}): {clave_final}"
    )
    return redirect('usuarios:lista')


def password_reset_request_view(request):
    if request.user.is_authenticated:
        return redirect('core:dashboard')

    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        from django.conf import settings as django_settings
        backend = getattr(django_settings, 'EMAIL_BACKEND', '')
        mail_sin_credenciales = (
            'locmem' not in backend.lower()
            and not mail_settings.RESEND_API_KEY
            and (not mail_settings.MAIL_HOST or not mail_settings.MAIL_USERNAME or not mail_settings.MAIL_PASSWORD)
        )
        if mail_sin_credenciales:
            messages.warning(
                request,
                "El servidor de correo de la plataforma aún no tiene credenciales SMTP o RESEND_API_KEY configuradas en .env o Backoffice."
            )
            return redirect('usuarios:password_reset')

        if email:
            users = User.objects.filter(email__iexact=email, is_active=True, activo=True)
            for u in users:
                uid = urlsafe_base64_encode(force_bytes(u.pk))
                token = default_token_generator.make_token(u)
                reset_url = request.build_absolute_uri(
                    reverse('usuarios:password_reset_confirm', args=[uid, token])
                )
                try:
                    send_password_reset_email(u, reset_url)
                except Exception as exc:
                    logger.warning("Error enviando recuperacion a %s: %s", email, exc)
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
