import logging
from datetime import timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_POST

from apps.empresas.models import Empresa, Plan
from apps.usuarios.models import User, EmpresaUsuario
from apps.usuarios.decorators import requiere_admin
from apps.core.audit import _extraer_ip, registrar_actividad
from apps.core.config import mail_settings
from apps.core.models import RegistroActividad
from apps.core.platform_settings import decrypt_secret, encrypt_secret
from apps.usuarios.totp import (
    generar_qr_data_uri,
    generar_secreto_totp,
    generar_uri_otpauth,
    verificar_codigo_totp,
)
from apps.core.notifications import (
    CATEGORIAS_PREFERENCIAS,
    destinatarios_propietarios_empresa,
    notificar_evento_seguridad,
    usuario_quiere_correo,
)
from apps.core.mail import (
    get_last_mail_error,
    is_dummy_or_invalid_email,
    send_notification_email,
    send_password_reset_email,
)


def _notificar_cambio_clave_usuario(request, user, origen: str):
    """Notifica al usuario y, si es propietario activo, al correo oficial de sus empresas."""
    destinatarios = [user.email]
    rels_prop = list(
        EmpresaUsuario.objects.filter(
            user=user, activo=True, rol='propietario', empresa__activa=True
        ).select_related('empresa')
    )
    emp_principal = rels_prop[0].empresa if rels_prop else (
        None if user.es_superadmin_user() else (user.empresas_activas()[0] if user.empresas_activas() else None)
    )
    for rel in rels_prop:
        if rel.empresa.email:
            destinatarios.append(rel.empresa.email)

    notificar_evento_seguridad(
        request,
        "Contraseña actualizada",
        f"Se actualizó la contraseña de acceso para {user.email} ({origen}).",
        destinatarios=destinatarios,
        actor=user,
        empresa=emp_principal,
        action_url=request.build_absolute_uri(reverse('usuarios:password_reset')),
        action_text="Solicitar nuevo restablecimiento",
    )


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """Token firmado de un solo uso y con expiración para verificación de correo electrónico."""

    def _make_hash_value(self, user, timestamp):
        verified_ts = user.email_verified_at.isoformat() if user.email_verified_at else ""
        return f"{user.pk}{user.password}{user.is_active}{verified_ts}{timestamp}"


email_verification_token = EmailVerificationTokenGenerator()
logger = logging.getLogger("faccol.auth")

LOGIN_MAX_FALLOS_CUENTA = 6
LOGIN_MAX_FALLOS_IP = 15
AUTH_VENTANA_BLOQUEO_MIN = 15
RESET_MAX_IP = 6
RESET_MAX_EMAIL = 3


def _safe_next_url(request, raw_url: str, fallback: str = 'core:dashboard') -> str:
    candidate = (raw_url or '').strip()
    if candidate and url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def _esta_bloqueado_login(request, login_id: str) -> bool:
    """Bloquea fuerza bruta por IP o por cuenta tras superar el umbral en 15 minutos."""
    desde = timezone.now() - timedelta(minutes=AUTH_VENTANA_BLOQUEO_MIN)
    ip = _extraer_ip(request)
    if ip and RegistroActividad.objects.filter(
        accion='LOGIN_FALLIDO',
        ip_address=ip,
        created_at__gte=desde,
    ).count() >= LOGIN_MAX_FALLOS_IP:
        return True

    if not login_id:
        return False

    u_intento = User.objects.filter(
        Q(username__iexact=login_id) | Q(email__iexact=login_id)
    ).first()
    corte = desde
    if u_intento:
        ultimo_ok = (
            RegistroActividad.objects.filter(
                accion='LOGIN',
                usuario=u_intento,
                created_at__gte=desde,
            )
            .order_by('-created_at')
            .first()
        )
        if ultimo_ok:
            corte = ultimo_ok.created_at

    filtro_cuenta = Q(datos_extra__login_intentado__iexact=login_id)
    if u_intento:
        filtro_cuenta = (
            filtro_cuenta
            | Q(usuario=u_intento)
            | Q(datos_extra__login_intentado__iexact=u_intento.username)
            | Q(datos_extra__login_intentado__iexact=u_intento.email)
        )
    fallos = RegistroActividad.objects.filter(
        accion='LOGIN_FALLIDO',
        created_at__gte=corte,
    ).filter(filtro_cuenta).count()
    return fallos >= LOGIN_MAX_FALLOS_CUENTA


def login_view(request):
    if request.user.is_authenticated:
        return redirect('core:dashboard')

    if request.method == 'POST':
        login_id = request.POST.get('login', '').strip()
        password = request.POST.get('password', '')

        if _esta_bloqueado_login(request, login_id):
            registrar_actividad(
                request,
                'auth',
                'LOGIN_BLOQUEADO_RATE',
                f"Bloqueo temporal por múltiples intentos fallidos para '{login_id}'.",
                datos_extra={'login_intentado': login_id},
            )
            messages.error(
                request,
                f"Demasiados intentos fallidos. Por seguridad, espera {AUTH_VENTANA_BLOQUEO_MIN} minutos o restablece tu contraseña.",
            )
            return render(request, 'usuarios/login.html', status=429)

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
                registrar_actividad(request, 'auth', 'LOGIN_BLOQUEADO', f"Intento de ingreso con cuenta inactiva ({user.username}).", user=user)
                return render(request, 'usuarios/login.html')

            if user.totp_enabled and user.totp_secret:
                raw_next = request.POST.get('next') or request.GET.get('next') or ''
                request.session['pre_2fa_user_id'] = user.id
                request.session['pre_2fa_next'] = _safe_next_url(request, raw_next)
                request.session['pre_2fa_ts'] = timezone.now().timestamp()
                return redirect('usuarios:verificar_2fa')

            login(request, user)
            emp_default = None if user.es_superadmin_user() else (user.empresas_activas()[0] if user.empresas_activas() else None)
            registrar_actividad(request, 'auth', 'LOGIN', f"Inicio de sesión exitoso ({user.username}).", empresa=emp_default, user=user)
            messages.success(request, f"¡Bienvenido de nuevo, {user.first_name or user.username}!")
            raw_next = request.POST.get('next') or request.GET.get('next') or ''
            return redirect(_safe_next_url(request, raw_next))
        else:
            u_intento = User.objects.filter(
                Q(username__iexact=login_id) | Q(email__iexact=login_id)
            ).first()
            registrar_actividad(
                request,
                'auth',
                'LOGIN_FALLIDO',
                f"Credenciales incorrectas para '{login_id}'.",
                user=u_intento,
                datos_extra={'login_intentado': login_id},
            )
            if u_intento and u_intento.email:
                desde_24h = timezone.now() - timedelta(hours=24)
                ultimo_ok = (
                    RegistroActividad.objects.filter(
                        accion='LOGIN',
                        usuario=u_intento,
                        created_at__gte=desde_24h,
                    )
                    .order_by('-created_at')
                    .first()
                )
                corte = ultimo_ok.created_at if ultimo_ok else desde_24h
                fallos_consecutivos = (
                    RegistroActividad.objects.filter(accion='LOGIN_FALLIDO', created_at__gte=corte)
                    .filter(
                        Q(usuario=u_intento)
                        | Q(datos_extra__login_intentado__iexact=u_intento.username)
                        | Q(datos_extra__login_intentado__iexact=u_intento.email)
                    )
                    .count()
                )
                if fallos_consecutivos >= 5 and fallos_consecutivos % 5 == 0:
                    emp_alerta = (
                        None
                        if u_intento.es_superadmin_user()
                        else (u_intento.empresas_activas()[0] if u_intento.empresas_activas() else None)
                    )
                    notificar_evento_seguridad(
                        request,
                        f"Alerta: {fallos_consecutivos} intentos fallidos de inicio de sesión",
                        f"Se detectaron {fallos_consecutivos} intentos fallidos consecutivos de acceso para la cuenta {u_intento.email}.",
                        destinatarios=[u_intento.email],
                        actor=u_intento,
                        empresa=emp_alerta,
                        action_url=request.build_absolute_uri(reverse('usuarios:password_reset')),
                        action_text="Restablecer mi contraseña",
                    )
            messages.error(request, "Credenciales incorrectas. Verifique su usuario o contraseña.")

    return render(request, 'usuarios/login.html')


def verificar_email_view(request, uidb64, token):
    """Activa cuenta validando token firmado de un solo uso y expiración."""
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
    registrar_actividad(request, 'auth', 'VERIFICAR_EMAIL', f"Correo verificado para {user.email or user.username}.", user=user, objeto=user)
    messages.success(request, "¡Correo verificado! Ya puedes iniciar sesión en FacCol.")
    return redirect('usuarios:login')


def logout_view(request):
    if request.user.is_authenticated:
        registrar_actividad(request, 'auth', 'LOGOUT', f"Cierre de sesión ({request.user.username}).")
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

        if 'actualizar_preferencias' in request.POST:
            prefs = dict(user.preferencias_notificaciones or {})
            for cat_val, _lbl in CATEGORIAS_PREFERENCIAS:
                prefs[f'email_{cat_val}'] = f'pref_email_{cat_val}' in request.POST
            user.preferencias_notificaciones = prefs

        # Cambio de contraseña opcional
        nuevo_pwd = request.POST.get('nuevo_password', '').strip()
        clave_actualizada = False
        if nuevo_pwd:
            try:
                validate_password(nuevo_pwd, user=user)
                user.set_password(nuevo_pwd)
                login(request, user)
                clave_actualizada = True
                messages.success(request, "Contraseña actualizada exitosamente.")
            except ValidationError as exc:
                for err in exc.messages:
                    messages.error(request, err)

        user.save()
        if clave_actualizada:
            registrar_actividad(
                request,
                'auth',
                'CAMBIAR_CONTRASENA_PERFIL',
                f"Actualizó su contraseña desde su perfil ({user.username}).",
                user=user,
                objeto=user,
            )
            _notificar_cambio_clave_usuario(request, user, "desde su perfil")
        messages.success(request, "Perfil actualizado correctamente.")
        return redirect('usuarios:perfil')

    prefs_notificaciones = [
        (cat_val, cat_lbl, usuario_quiere_correo(user, cat_val))
        for cat_val, cat_lbl in CATEGORIAS_PREFERENCIAS
    ]
    pending_totp = request.session.get('pending_totp_secret', '')
    totp_qr = ''
    if pending_totp and not user.totp_enabled:
        uri = generar_uri_otpauth(pending_totp, user.email or user.username)
        totp_qr = generar_qr_data_uri(uri)

    return render(request, 'usuarios/perfil.html', {
        'prefs_notificaciones': prefs_notificaciones,
        'pending_totp_secret': pending_totp,
        'totp_qr_uri': totp_qr,
    })


def verificar_2fa_view(request):
    """Segundo paso de autenticación TOTP de 6 dígitos tras validar contraseña."""
    if request.user.is_authenticated:
        return redirect('core:dashboard')

    uid = request.session.get('pre_2fa_user_id')
    ts = request.session.get('pre_2fa_ts') or 0
    if not uid or (timezone.now().timestamp() - float(ts)) > 300:
        for k in ('pre_2fa_user_id', 'pre_2fa_next', 'pre_2fa_ts'):
            request.session.pop(k, None)
        messages.warning(request, "Tu sesión temporal de verificación 2FA expiró. Inicia sesión nuevamente.")
        return redirect('usuarios:login')

    user = User.objects.filter(pk=uid, activo=True).first()
    if not user or not user.totp_enabled or not user.totp_secret:
        return redirect('usuarios:login')

    if request.method == 'POST':
        codigo = request.POST.get('codigo', '').strip()
        secret = decrypt_secret(user.totp_secret)
        if verificar_codigo_totp(secret, codigo):
            next_dest = request.session.get('pre_2fa_next') or 'core:dashboard'
            for k in ('pre_2fa_user_id', 'pre_2fa_next', 'pre_2fa_ts'):
                request.session.pop(k, None)
            login(request, user)
            emp_default = None if user.es_superadmin_user() else (user.empresas_activas()[0] if user.empresas_activas() else None)
            registrar_actividad(request, 'auth', 'LOGIN_2FA', f"Acceso validado con 2FA TOTP ({user.username}).", empresa=emp_default, user=user)
            messages.success(request, f"¡Bienvenido de nuevo, {user.first_name or user.username}!")
            return redirect(_safe_next_url(request, next_dest))

        registrar_actividad(request, 'auth', 'LOGIN_2FA_FALLIDO', f"Código TOTP 2FA incorrecto ({user.username}).", user=user)
        messages.error(request, "Código de verificación 2FA inválido o expirado.")

    return render(request, 'usuarios/verificar_2fa.html', {'usuario_email': user.email or user.username})


@login_required
@require_POST
def perfil_2fa_config(request):
    """Genera secreto QR, activa o desactiva autenticación de dos factores (TOTP) del usuario."""
    user = request.user
    accion = request.POST.get('accion', '')

    if accion == 'iniciar':
        request.session['pending_totp_secret'] = generar_secreto_totp()
        messages.info(request, "Escanea el código QR en tu app autenticadora y confirma el código de 6 dígitos.")
    elif accion == 'activar':
        pending = request.session.get('pending_totp_secret', '')
        codigo = request.POST.get('codigo', '').strip()
        if not pending:
            messages.error(request, "Primero genera un nuevo código QR de configuración.")
        elif verificar_codigo_totp(pending, codigo):
            user.totp_secret = encrypt_secret(pending)
            user.totp_enabled = True
            user.save(update_fields=['totp_secret', 'totp_enabled'])
            request.session.pop('pending_totp_secret', None)
            registrar_actividad(request, 'auth', 'ACTIVAR_2FA', f"Activó autenticación 2FA TOTP ({user.username}).", user=user, objeto=user)
            messages.success(request, "Autenticación de dos factores (2FA) activada correctamente.")
        else:
            messages.error(request, "El código de 6 dígitos no coincide. Verifica la hora de tu dispositivo.")
    elif accion == 'desactivar':
        pwd = request.POST.get('password_confirm', '')
        if not user.check_password(pwd):
            messages.error(request, "Contraseña actual incorrecta. No se desactivó el 2FA.")
        else:
            user.totp_enabled = False
            user.totp_secret = ''
            user.save(update_fields=['totp_enabled', 'totp_secret'])
            request.session.pop('pending_totp_secret', None)
            registrar_actividad(request, 'auth', 'DESACTIVAR_2FA', f"Desactivó autenticación 2FA ({user.username}).", user=user, objeto=user)
            messages.success(request, "Autenticación 2FA desactivada.")
    return redirect('usuarios:perfil')


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
            from datetime import date
            hoy = date.today()
            if not RegistroActividad.objects.filter(
                empresa=empresa,
                accion='ALERTA_CUPO_USUARIOS',
                created_at__year=hoy.year,
                created_at__month=hoy.month,
            ).exists():
                resumen_lim = (
                    f"{empresa.display_name} alcanzó el límite de {empresa.plan.limite_usuarios} usuarios activos "
                    f"incluidos en su plan '{empresa.plan.nombre}'."
                )
                registrar_actividad(request, 'planes', 'ALERTA_CUPO_USUARIOS', resumen_lim, empresa=empresa, objeto=empresa)
                notificar_evento_seguridad(
                    request,
                    "Límite de usuarios del plan alcanzado",
                    resumen_lim,
                    destinatarios=destinatarios_propietarios_empresa(empresa),
                    empresa=empresa,
                    action_url=request.build_absolute_uri(reverse('core:mi_plan')),
                    action_text="Ampliar Plan",
                    categoria='plan',
                    nivel='warning',
                )
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

        if not email:
            messages.error(request, "El correo electrónico es obligatorio.")
            return render(request, 'usuarios/crear.html', {'roles': EmpresaUsuario.ROLES_CHOICES})

        if password:
            try:
                validate_password(password)
            except ValidationError as exc:
                for err in exc.messages:
                    messages.error(request, err)
                return render(request, 'usuarios/crear.html', {'roles': EmpresaUsuario.ROLES_CHOICES})

        enviar_enlace_inicial = not bool(password)
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
            user.set_password(password or get_random_string(20))
            user.save()

        # Asignar a la empresa
        asignacion, _ = EmpresaUsuario.objects.update_or_create(
            user=user,
            empresa=empresa,
            defaults={'rol': rol, 'activo': True}
        )
        rol_display = asignacion.get_rol_display()
        registrar_actividad(
            request,
            'usuarios',
            'CREAR_USUARIO',
            f"Configuró usuario '{user.email}' con rol '{rol_display}' en {empresa.display_name}.",
            empresa=empresa,
            objeto=user,
            datos_extra={'enlace_inicial': enviar_enlace_inicial, 'rol': rol},
        )

        if created and enviar_enlace_inicial:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = request.build_absolute_uri(reverse('usuarios:password_reset_confirm', args=[uid, token]))
            enlace_ok = send_password_reset_email(user, reset_url)
            propietarios = destinatarios_propietarios_empresa(
                empresa, excluir_emails=[request.user.email, user.email]
            )
            notificar_evento_seguridad(
                request,
                "Nuevo integrante agregado al equipo",
                f"Se creó la cuenta de {user.email} en {empresa.display_name} con rol '{rol_display}' y se envió enlace para definir contraseña.",
                destinatarios=propietarios,
                empresa=empresa,
                action_url=request.build_absolute_uri(reverse('usuarios:lista')),
                action_text="Ver equipo y roles",
            )
            if enlace_ok:
                messages.success(request, f"Usuario {user.email} creado con rol {rol_display} y enlace de acceso enviado a su correo.")
            else:
                messages.warning(request, f"Usuario {user.email} creado, pero no se pudo enviar el enlace inicial ({get_last_mail_error()}).")
        else:
            destinatarios = [user.email] + destinatarios_propietarios_empresa(
                empresa, excluir_emails=[request.user.email]
            )
            notificar_evento_seguridad(
                request,
                "Nuevo acceso asignado en empresa",
                f"Se habilitó el acceso de {user.email} en {empresa.display_name} con el rol '{rol_display}'.",
                destinatarios=destinatarios,
                empresa=empresa,
                action_url=request.build_absolute_uri(reverse('usuarios:login')),
                action_text="Ingresar a FacCol",
            )
            messages.success(request, f"Usuario {user.email} configurado exitosamente con rol {rol_display}.")
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
            rol_anterior_display = asignacion.get_rol_display()
            rol_cambiado = asignacion.rol != nuevo_rol
            asignacion.rol = nuevo_rol
            asignacion.save()
            registrar_actividad(
                request,
                'usuarios',
                'CAMBIAR_ROL',
                f"Cambió rol de '{asignacion.user.username}' a '{asignacion.get_rol_display()}'.",
                empresa=empresa,
                objeto=asignacion.user,
            )
            if rol_cambiado:
                dests = [asignacion.user.email] + destinatarios_propietarios_empresa(
                    empresa, excluir_emails=[request.user.email]
                )
                notificar_evento_seguridad(
                    request,
                    "Cambio de rol de usuario",
                    f"El rol de {asignacion.user.email} en {empresa.display_name} cambió de '{rol_anterior_display}' a '{asignacion.get_rol_display()}'.",
                    destinatarios=dests,
                    empresa=empresa,
                    action_url=request.build_absolute_uri(reverse('usuarios:lista')),
                    action_text="Ver equipo y roles",
                )
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
    registrar_actividad(
        request,
        'usuarios',
        'CAMBIAR_ESTADO_USUARIO',
        f"Acceso de '{asignacion.user.username}' {estado_str} en {empresa.display_name}.",
        empresa=empresa,
        objeto=asignacion.user,
    )
    dests = [asignacion.user.email] + destinatarios_propietarios_empresa(
        empresa, excluir_emails=[request.user.email]
    )
    notificar_evento_seguridad(
        request,
        f"Acceso de usuario {estado_str}",
        f"La cuenta de {asignacion.user.email} en {empresa.display_name} fue {estado_str}.",
        destinatarios=dests,
        empresa=empresa,
        action_url=request.build_absolute_uri(reverse('usuarios:lista')),
        action_text="Ver equipo y roles",
    )
    messages.success(request, f"Acceso de {asignacion.user.username} a la empresa ha sido {estado_str}.")
    return redirect('usuarios:lista')


def _resolver_asignacion_accesible(request, asignacion_id):
    is_sa = bool(request.user.is_superadmin or request.user.is_superuser)
    empresa = getattr(request, 'empresa_activa', None)
    if is_sa:
        return get_object_or_404(EmpresaUsuario.objects.select_related('user', 'empresa'), id=asignacion_id), True
    if empresa and request.user.es_admin(empresa):
        return get_object_or_404(
            EmpresaUsuario.objects.select_related('user', 'empresa'),
            id=asignacion_id,
            empresa=empresa,
        ), False
    return None, False


@login_required
@require_POST
def usuario_asignar_clave(request, asignacion_id):
    """
    Bloqueado por privacidad SaaS: ni el SuperAdmin ni terceros deben fijar contraseñas
    directamente; la gestión de acceso se realiza por enlace firmado al correo del usuario.
    """
    asignacion, _ = _resolver_asignacion_accesible(request, asignacion_id)
    emp_obj = asignacion.empresa if asignacion else None
    u_obj = asignacion.user if asignacion else None
    registrar_actividad(
        request,
        'usuarios',
        'INTENTO_CAMBIO_CLAVE_BLOQUEADO',
        f"Intento bloqueado de cambio directo de contraseña para '{getattr(u_obj, 'username', asignacion_id)}'.",
        empresa=emp_obj,
        objeto=u_obj,
    )
    return HttpResponseForbidden(
        "Por privacidad SaaS no se permite establecer contraseñas de otros usuarios directamente. Usa 'Enviar Enlace' al correo del usuario."
    )


@login_required
@require_POST
def usuario_enviar_enlace_clave(request, asignacion_id):
    """Envía al usuario un enlace firmado de un solo uso para que él mismo defina su contraseña."""
    asignacion, is_sa = _resolver_asignacion_accesible(request, asignacion_id)
    if not asignacion:
        messages.error(request, "No tienes permisos para gestionar el acceso de este usuario.")
        return redirect('core:dashboard')

    u = asignacion.user
    if u.es_superadmin_user() and u != request.user:
        messages.error(request, "No puedes enviar enlaces de restablecimiento para otro SuperAdmin.")
        return redirect('usuarios:lista')

    correo = (u.email or '').strip().lower()
    if not correo or is_dummy_or_invalid_email(correo):
        messages.error(request, f"El usuario {u.username} no tiene un correo electrónico real registrado.")
        return redirect('usuarios:lista')

    uid = urlsafe_base64_encode(force_bytes(u.pk))
    token = default_token_generator.make_token(u)
    reset_url = request.build_absolute_uri(reverse('usuarios:password_reset_confirm', args=[uid, token]))
    if send_password_reset_email(u, reset_url):
        registrar_actividad(
            request,
            'usuarios',
            'ENVIAR_ENLACE_CLAVE',
            f"Envió enlace de restablecimiento de clave al correo '{correo}' de {asignacion.empresa.display_name}.",
            empresa=asignacion.empresa,
            objeto=u,
            datos_extra={'via_superadmin': is_sa},
        )
        messages.success(request, f"Enlace seguro de contraseña enviado al correo {correo}.")
    else:
        detalle = get_last_mail_error() or "Verifica RESEND_API_KEY o respaldo SMTP."
        messages.error(request, f"No se pudo enviar el enlace a {correo} ({detalle}).")
    return redirect('usuarios:lista')


@login_required
@require_POST
def usuario_notificar_admin_acceso(request, asignacion_id):
    """Permite al SuperAdmin avisar al Propietario/Admin de la empresa si un miembro perdió acceso a su correo."""
    is_sa = bool(request.user.is_superadmin or request.user.is_superuser)
    if not is_sa:
        return HttpResponseForbidden("Acción exclusiva de SuperAdmin.")

    asignacion = get_object_or_404(EmpresaUsuario.objects.select_related('user', 'empresa'), id=asignacion_id)
    empresa = asignacion.empresa
    u = asignacion.user

    admins_otros = (
        EmpresaUsuario.objects.filter(empresa=empresa, activo=True, rol__in=('propietario', 'admin'))
        .exclude(user=u)
        .select_related('user')
    )
    destinatarios = []
    seen = set()
    for a in admins_otros:
        em = (a.user.email or '').strip().lower()
        if em and not is_dummy_or_invalid_email(em) and em not in seen:
            seen.add(em)
            destinatarios.append(a.user)

    emp_email = (empresa.email or '').strip().lower()
    nombre_afectado = u.get_full_name() or u.username
    panel_url = request.build_absolute_uri(reverse('usuarios:lista'))

    enviados = 0
    for adm in destinatarios:
        if send_notification_email(
            adm,
            f"FacCol — Solicitud de acceso para {nombre_afectado} ({empresa.display_name})",
            "Asistencia de acceso para usuario de tu empresa",
            f"El usuario {nombre_afectado} ({u.email or u.username}) requiere restablecer su acceso o actualizar su correo electrónico en {empresa.display_name}.\n\nIngresa a Equipo y Roles para actualizar su correo o enviarle un nuevo enlace de contraseña.",
            action_url=panel_url,
            action_text="Ir a Equipo de Empresa",
        ):
            enviados += 1

    # Si es único propietario/admin o no hay otros admins con correo, respaldar con correo corporativo de empresa si es distinto
    if enviados == 0 and emp_email and emp_email != (u.email or '').strip().lower() and not is_dummy_or_invalid_email(emp_email):
        uid = urlsafe_base64_encode(force_bytes(u.pk))
        token = default_token_generator.make_token(u)
        reset_url = request.build_absolute_uri(reverse('usuarios:password_reset_confirm', args=[uid, token]))
        class _CorpRecipient:
            def __init__(self, mail_addr, name_str):
                self.email = mail_addr
                self.first_name = name_str
                self.username = mail_addr
        if send_notification_email(
            _CorpRecipient(emp_email, empresa.display_name),
            f"FacCol — Recuperación de acceso de {nombre_afectado} ({empresa.display_name})",
            "Enlace de recuperación al correo oficial de tu empresa",
            f"Se solicitó apoyo para recuperar el acceso de {nombre_afectado} ({u.email or u.username}). Como el correo oficial de la empresa ({emp_email}) está registrado como respaldo, puedes usar este enlace de un solo uso para definir una nueva contraseña.",
            action_url=reset_url,
            action_text="Restablecer Contraseña Ahora",
        ):
            enviados += 1

    if enviados:
        registrar_actividad(
            request,
            'usuarios',
            'NOTIFICAR_ADMIN_ACCESO',
            f"Notificó a administrador/correo oficial de {empresa.display_name} sobre acceso de '{u.email or u.username}'.",
            empresa=empresa,
            objeto=u,
        )
        messages.success(request, f"Aviso de recuperación enviado al administrador/correo oficial de {empresa.display_name}.")
    else:
        messages.warning(
            request,
            "No se encontraron otros administradores con correo válido ni un correo corporativo distinto en la empresa. Usa 'Cambiar Correo' con motivo auditado si validaste la identidad del Propietario."
        )
    return redirect('usuarios:lista')


@login_required
@require_POST
def usuario_actualizar_correo(request, asignacion_id):
    """
    Actualiza el correo del usuario (Admin de empresa para su equipo, o SuperAdmin con motivo
    obligatorio auditado) y opcionalmente envía enlace de recuperación al nuevo correo.
    """
    asignacion, is_sa = _resolver_asignacion_accesible(request, asignacion_id)
    if not asignacion:
        messages.error(request, "No tienes permisos para modificar el correo de este usuario.")
        return redirect('core:dashboard')

    u = asignacion.user
    if u.es_superadmin_user() and u != request.user:
        messages.error(request, "No puedes modificar el correo de otro SuperAdmin desde esta vista.")
        return redirect('usuarios:lista')

    nuevo_correo = (request.POST.get('nuevo_correo') or '').strip().lower()
    motivo = (request.POST.get('motivo') or '').strip()

    if not nuevo_correo or is_dummy_or_invalid_email(nuevo_correo):
        messages.error(request, "Ingresa un correo electrónico válido y real.")
        return redirect('usuarios:lista')

    if is_sa and len(motivo) < 5:
        messages.error(request, "Como SuperAdmin debes indicar el motivo auditado (mínimo 5 caracteres) para actualizar un correo.")
        return redirect('usuarios:lista')

    if User.objects.filter(Q(email__iexact=nuevo_correo) | Q(username__iexact=nuevo_correo)).exclude(pk=u.pk).exists():
        messages.error(request, f"El correo {nuevo_correo} ya pertenece a otra cuenta en FacCol.")
        return redirect('usuarios:lista')

    correo_anterior = (u.email or '').strip()
    if u.username.lower() == correo_anterior.lower() or '@' in u.username:
        u.username = nuevo_correo
    u.email = nuevo_correo
    u.email_verified_at = timezone.now()
    u.save(update_fields=['username', 'email', 'email_verified_at'])

    registrar_actividad(
        request,
        'usuarios',
        'ACTUALIZAR_CORREO_USUARIO',
        f"Actualizó correo de '{correo_anterior or u.username}' a '{nuevo_correo}' en {asignacion.empresa.display_name}."
        + (f" Motivo: {motivo}" if motivo else ""),
        empresa=asignacion.empresa,
        objeto=u,
        datos_extra={'correo_anterior': correo_anterior, 'correo_nuevo': nuevo_correo, 'motivo': motivo},
    )

    dests_alerta = [correo_anterior, nuevo_correo] + destinatarios_propietarios_empresa(
        asignacion.empresa, excluir_emails=[request.user.email]
    )
    notificar_evento_seguridad(
        request,
        "Correo electrónico de acceso actualizado",
        f"El correo de acceso de '{correo_anterior or u.username}' fue actualizado a '{nuevo_correo}' en {asignacion.empresa.display_name}.",
        destinatarios=dests_alerta,
        empresa=asignacion.empresa,
        action_url=request.build_absolute_uri(reverse('usuarios:password_reset')),
        action_text="Restablecer contraseña",
        extra_lineas=[f"Motivo registrado: {motivo}"] if motivo else None,
    )

    if 'enviar_enlace' in request.POST:
        uid = urlsafe_base64_encode(force_bytes(u.pk))
        token = default_token_generator.make_token(u)
        reset_url = request.build_absolute_uri(reverse('usuarios:password_reset_confirm', args=[uid, token]))
        if send_password_reset_email(u, reset_url):
            messages.success(request, f"Correo actualizado a {nuevo_correo} y enlace seguro enviado.")
        else:
            messages.warning(request, f"Correo actualizado a {nuevo_correo}, pero falló el envío del enlace ({get_last_mail_error()}).")
    else:
        messages.success(request, f"Correo actualizado correctamente a {nuevo_correo}.")
    return redirect('usuarios:lista')


def password_reset_request_view(request):
    if request.user.is_authenticated:
        return redirect('core:dashboard')

    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        desde_reset = timezone.now() - timedelta(minutes=AUTH_VENTANA_BLOQUEO_MIN)
        ip = _extraer_ip(request)
        if ip and RegistroActividad.objects.filter(
            accion='PASSWORD_RESET_SOLICITADO',
            ip_address=ip,
            created_at__gte=desde_reset,
        ).count() >= RESET_MAX_IP:
            messages.error(request, f"Demasiadas solicitudes de recuperación desde esta IP. Espera {AUTH_VENTANA_BLOQUEO_MIN} minutos.")
            return render(request, 'usuarios/password_reset_form.html', status=429)
        if email and RegistroActividad.objects.filter(
            accion='PASSWORD_RESET_SOLICITADO',
            datos_extra__email__iexact=email,
            created_at__gte=desde_reset,
        ).count() >= RESET_MAX_EMAIL:
            messages.error(request, f"Ya se enviaron varias solicitudes recientes para este correo. Espera {AUTH_VENTANA_BLOQUEO_MIN} minutos.")
            return render(request, 'usuarios/password_reset_form.html', status=429)

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

        registrar_actividad(
            request,
            'auth',
            'PASSWORD_RESET_SOLICITADO',
            f"Solicitud de restablecimiento de contraseña para '{email or 'sin-correo'}'.",
            datos_extra={'email': email},
        )

        if email:
            users = User.objects.filter(email__iexact=email, is_active=True, activo=True)
            fallidos = 0
            for u in users:
                uid = urlsafe_base64_encode(force_bytes(u.pk))
                token = default_token_generator.make_token(u)
                reset_url = request.build_absolute_uri(
                    reverse('usuarios:password_reset_confirm', args=[uid, token])
                )
                try:
                    if not send_password_reset_email(u, reset_url):
                        fallidos += 1
                except Exception as exc:
                    fallidos += 1
                    logger.warning("Error enviando recuperacion a %s: %s", email, exc)
            if fallidos:
                detalle = get_last_mail_error() or "Verifica RESEND_API_KEY o respaldo SMTP."
                messages.error(
                    request,
                    f"No se pudo entregar el correo de recuperación ({detalle})."
                )
                return redirect('usuarios:password_reset')
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
        if p1 != p2:
            messages.error(request, "Las contraseñas no coinciden.")
        else:
            try:
                validate_password(p1, user=user)
            except ValidationError as exc:
                for err in exc.messages:
                    messages.error(request, err)
                return render(request, 'usuarios/password_reset_confirm.html', {'token_valido': True})
            user.set_password(p1)
            user.save(update_fields=['password'])
            registrar_actividad(
                request,
                'auth',
                'CAMBIAR_CONTRASENA',
                f"Restableció su contraseña mediante enlace de recuperación ({user.username}).",
                user=user,
                objeto=user,
            )
            _notificar_cambio_clave_usuario(request, user, "mediante enlace de recuperación")
            messages.success(request, "Tu contraseña ha sido actualizada. Ya puedes iniciar sesión.")
            return redirect('usuarios:login')

    return render(request, 'usuarios/password_reset_confirm.html', {'token_valido': True})
