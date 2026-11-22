from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import user_passes_test
from django.contrib import messages
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_POST
from apps.empresas.models import Empresa, Plan, Modulo, Departamento, Municipio, SolicitudCambioPlan
from apps.usuarios.models import User, EmpresaUsuario
from apps.facturacion.models import Factura
from apps.core.audit import registrar_actividad
from apps.core.models import RegistroActividad
from apps.core.notifications import (
    destinatarios_propietarios_empresa,
    notificar_evento_seguridad,
)
from apps.core.platform_settings import encrypt_secret, mask_secret, set_platform_setting
from apps.core.mail_service import (
    enviar_correo_sistema,
    enviar_notificacion_bienvenida_empresa,
    get_mail_runtime_config,
)
from apps.core.campaigns import (
    get_campaign_summary,
    get_tenant_banner,
    reset_campaign_cycle,
    save_campaign_draft,
    save_tenant_banner,
    send_campaign_batch,
    send_campaign_test_email,
)
from apps.core.config import mail_settings
from apps.facturacion.wompi_service import get_wompi_runtime_config


def es_superadmin(user):
    return user.is_authenticated and (user.is_superadmin or user.is_superuser)


@user_passes_test(es_superadmin)
def backoffice_dashboard(request):
    """
    Ruta unificada: Redirige directamente al Dashboard Principal, donde el SuperAdmin
    cuenta con su centro de comando global integrado sin rutas dispersas.
    """
    return redirect('core:dashboard')


@user_passes_test(es_superadmin)
def empresas_lista(request):
    busqueda = request.GET.get('q', '').strip()
    empresas = Empresa.objects.select_related('plan').prefetch_related(
        'usuarios_asignados__user'
    ).order_by('-created_at')

    if busqueda:
        empresas = empresas.filter(razon_social__icontains=busqueda) | empresas.filter(nit__icontains=busqueda)

    return render(request, 'backoffice/empresas_lista.html', {'empresas': empresas, 'busqueda': busqueda})


@user_passes_test(es_superadmin)
def empresa_toggle_activa(request, empresa_id):
    empresa = get_object_or_404(Empresa, id=empresa_id)
    empresa.activa = not empresa.activa
    empresa.save(update_fields=['activa'])
    estado_str = "activada" if empresa.activa else "suspendida"
    registrar_actividad(request, 'empresas', 'ACTIVAR_EMPRESA' if empresa.activa else 'SUSPENDER_EMPRESA', f"Empresa '{empresa.razon_social}' {estado_str} por SuperAdmin.", empresa=empresa, objeto=empresa)
    notificar_evento_seguridad(
        request,
        f"Empresa {estado_str}: {empresa.display_name}",
        f"El estado operativo de '{empresa.display_name}' cambió a '{estado_str.upper()}' desde el Backoffice SaaS.",
        destinatarios=destinatarios_propietarios_empresa(empresa),
        empresa=empresa,
        action_url=request.build_absolute_uri(reverse('core:dashboard')),
        action_text="Ir a FacCol",
    )
    messages.success(request, f"La empresa inquilina '{empresa.razon_social}' ha sido {estado_str} exitosamente.")
    return redirect('backoffice:empresas_lista')


@user_passes_test(es_superadmin)
def empresa_crear(request):
    departamentos = Departamento.objects.all()
    planes = Plan.objects.filter(activo=True)

    if request.method == 'POST':
        razon_social = request.POST.get('razon_social', '').strip()
        nit = request.POST.get('nit', '').strip()
        dv = request.POST.get('dv', '').strip()
        email = request.POST.get('email', '').strip()
        plan_id = request.POST.get('plan')

        if not razon_social or not nit:
            messages.error(request, "Razón Social y NIT son obligatorios.")
            return render(request, 'backoffice/empresa_formulario.html', {
                'departamentos': departamentos, 'planes': planes, 'accion': 'Crear'
            })

        empresa = Empresa.objects.create(
            razon_social=razon_social,
            nombre_comercial=request.POST.get('nombre_comercial', '').strip(),
            nit=nit,
            dv=dv,
            regimen_tributario=request.POST.get('regimen_tributario', 'RESPONSABLE_IVA'),
            perfil_operativo=request.POST.get('perfil_operativo', 'GENERAL'),
            email=email,
            telefono=request.POST.get('telefono', '').strip(),
            direccion=request.POST.get('direccion', '').strip(),
            plan_id=plan_id if plan_id else None,
            prefijo_factura=request.POST.get('prefijo_factura', 'FE').strip().upper(),
            prefijo_pos_electronico=request.POST.get('prefijo_pos_electronico', 'POS').strip().upper() or 'POS',
            resolucion_dian=request.POST.get('resolucion_dian', '').strip(),
            modo_dian=request.POST.get('modo_dian', 'PRUEBAS'),
            dian_software_id=request.POST.get('dian_software_id', '').strip(),
            dian_pin=request.POST.get('dian_pin', '').strip(),
            dian_test_set_id=request.POST.get('dian_test_set_id', '').strip(),
        )

        # Habilitar módulos según el plan o por defecto
        if empresa.plan and empresa.plan.modulos.exists():
            for mod in empresa.plan.modulos.all():
                empresa.modulos.add(mod)
        else:
            for mod in Modulo.objects.filter(activo_por_defecto=True):
                empresa.modulos.add(mod)

        # Aislamiento Estricto SaaS: Determinar el Propietario inicial
        admin_tipo = request.POST.get('admin_tipo', 'nuevo_cliente')

        if admin_tipo == 'asignar_superadmin':
            # Empresa propia del SuperAdmin (ej. ServiGest)
            EmpresaUsuario.objects.create(
                user=request.user,
                empresa=empresa,
                rol='propietario',
                activo=True
            )
            messages.success(request, f"Empresa propia '{empresa.razon_social}' creada exitosamente y asignada a tu cuenta como Propietario.")
        else:
            # Empresa de un cliente: Creamos su usuario administrador inicial independiente
            admin_email = request.POST.get('admin_email', '').strip().lower() or email
            admin_nombre = request.POST.get('admin_nombre', '').strip() or razon_social
            raw_password = request.POST.get('admin_password', '').strip()

            client_user, created = User.objects.get_or_create(
                username=admin_email,
                defaults={
                    'email': admin_email,
                    'first_name': admin_nombre,
                    'is_superadmin': False,
                    'is_staff': False,
                    'is_superuser': False,
                    'email_verified_at': timezone.now(),
                }
            )
            admin_password = raw_password or (get_random_string(12) if created else '')
            if admin_password:
                client_user.set_password(admin_password)
                client_user.save()

            EmpresaUsuario.objects.create(
                user=client_user,
                empresa=empresa,
                rol='propietario',
                activo=True
            )

            if admin_password and admin_email:
                login_url = request.build_absolute_uri(reverse('usuarios:login'))
                enviar_notificacion_bienvenida_empresa(empresa, admin_email, admin_nombre, admin_password, login_url)

            cred_msg = f"Usuario: {admin_email} | Clave temporal: {admin_password}" if admin_password else f"Usuario existente asignado: {admin_email}"
            messages.success(
                request,
                f"Empresa inquilina '{empresa.razon_social}' creada exitosamente con Aislamiento Estricto. {cred_msg}"
            )

        plan_txt = empresa.plan.nombre if empresa.plan else "Sin plan asignado"
        registrar_actividad(
            request,
            'empresas',
            'CREAR_EMPRESA',
            f"Creó empresa '{empresa.razon_social}' (NIT {empresa.nit}) con plan '{plan_txt}'.",
            empresa=empresa,
            objeto=empresa,
        )
        return redirect('backoffice:empresas_lista')

    return render(request, 'backoffice/empresa_formulario.html', {
        'departamentos': departamentos,
        'planes': planes,
        'accion': 'Crear'
    })


@user_passes_test(es_superadmin)
def empresa_editar(request, empresa_id):
    empresa = get_object_or_404(Empresa, id=empresa_id)
    departamentos = Departamento.objects.all()
    planes = Plan.objects.filter(activo=True)
    modulos = Modulo.objects.all()

    if request.method == 'POST':
        empresa.razon_social = request.POST.get('razon_social', empresa.razon_social).strip()
        empresa.nombre_comercial = request.POST.get('nombre_comercial', '').strip()
        empresa.nit = request.POST.get('nit', empresa.nit).strip()
        empresa.dv = request.POST.get('dv', '').strip()
        empresa.regimen_tributario = request.POST.get('regimen_tributario', empresa.regimen_tributario)
        empresa.perfil_operativo = request.POST.get('perfil_operativo', empresa.perfil_operativo)
        empresa.email = request.POST.get('email', '').strip()
        empresa.telefono = request.POST.get('telefono', '').strip()
        empresa.direccion = request.POST.get('direccion', '').strip()

        plan_anterior_id = empresa.plan_id
        critico_antes = (
            empresa.modo_dian,
            empresa.resolucion_dian,
            empresa.dian_software_id,
            empresa.smtp_host,
            empresa.smtp_from_address,
            empresa.activa,
        )
        plan_id = request.POST.get('plan')
        empresa.plan_id = int(plan_id) if plan_id else None

        # Configuración DIAN y POS Electrónico
        empresa.prefijo_factura = request.POST.get('prefijo_factura', 'FE').strip().upper()
        empresa.prefijo_pos_electronico = request.POST.get('prefijo_pos_electronico', 'POS').strip().upper() or 'POS'
        empresa.resolucion_dian = request.POST.get('resolucion_dian', '').strip()
        empresa.rango_desde = int(request.POST.get('rango_desde', 1) or 1)
        empresa.rango_hasta = int(request.POST.get('rango_hasta', 5000) or 5000)
        empresa.modo_dian = request.POST.get('modo_dian', empresa.modo_dian)
        empresa.dian_software_id = request.POST.get('dian_software_id', '').strip()
        empresa.dian_pin = request.POST.get('dian_pin', '').strip()
        empresa.dian_test_set_id = request.POST.get('dian_test_set_id', '').strip()

        # Configuración de correo independiente de empresa (Resend propio o SMTP propio)
        if 'clear_resend_api_key' in request.POST:
            empresa.resend_api_key = ''
        else:
            new_emp_resend = request.POST.get('resend_api_key', '').strip()
            if new_emp_resend:
                empresa.resend_api_key = encrypt_secret(new_emp_resend)

        empresa.smtp_host = request.POST.get('smtp_host', '').strip()
        empresa.smtp_port = int(request.POST.get('smtp_port', 587) or 587)
        empresa.smtp_user = request.POST.get('smtp_user', '').strip()
        if 'clear_smtp_password' in request.POST:
            empresa.smtp_password = ''
        else:
            new_smtp_pwd = request.POST.get('smtp_password', '').strip()
            if new_smtp_pwd:
                empresa.smtp_password = encrypt_secret(new_smtp_pwd)
        empresa.smtp_from_address = request.POST.get('smtp_from_address', '').strip()
        empresa.smtp_from_name = request.POST.get('smtp_from_name', '').strip()

        empresa.activa = 'activa' in request.POST
        empresa.save()

        # Actualizar módulos (sincronizados con plan nuevo si se marcó o cambió de plan sin edición manual)
        if 'sincronizar_plan_modulos' in request.POST and empresa.plan:
            empresa.sincronizar_modulos_con_plan(empresa.plan)
        else:
            modulos_seleccionados = request.POST.getlist('modulos[]')
            empresa.modulos.clear()
            for mod_id in modulos_seleccionados:
                empresa.modulos.add(mod_id)

        if empresa.plan_id != plan_anterior_id:
            plan_txt = empresa.plan.nombre if empresa.plan else "Sin plan"
            registrar_actividad(
                request,
                'planes',
                'ASIGNAR_PLAN',
                f"SuperAdmin asignó el plan '{plan_txt}' a '{empresa.razon_social}'.",
                empresa=empresa,
                objeto=empresa,
            )
            notificar_evento_seguridad(
                request,
                f"Plan actualizado: {plan_txt}",
                f"Desde el Backoffice SaaS se asignó el plan '{plan_txt}' a {empresa.display_name}.",
                destinatarios=destinatarios_propietarios_empresa(empresa),
                empresa=empresa,
                action_url=request.build_absolute_uri(reverse('core:dashboard')),
                action_text="Ir a FacCol",
            )
        critico_despues = (
            empresa.modo_dian,
            empresa.resolucion_dian,
            empresa.dian_software_id,
            empresa.smtp_host,
            empresa.smtp_from_address,
            empresa.activa,
        )
        clave_correo_tocada = any(
            k in request.POST for k in ('clear_resend_api_key', 'clear_smtp_password')
        ) or bool(request.POST.get('resend_api_key', '').strip() or request.POST.get('smtp_password', '').strip())
        if critico_antes != critico_despues or clave_correo_tocada:
            notificar_evento_seguridad(
                request,
                "Configuración crítica modificada desde Backoffice",
                f"Se actualizaron parámetros operativos críticos (estado, DIAN o correo) de {empresa.display_name}.",
                destinatarios=destinatarios_propietarios_empresa(empresa),
                empresa=empresa,
                action_url=request.build_absolute_uri(reverse('core:configuracion_empresa')),
                action_text="Revisar configuración",
            )
        registrar_actividad(
            request,
            'empresas',
            'EDITAR_EMPRESA',
            f"Actualizó configuración de '{empresa.razon_social}' (NIT {empresa.nit}).",
            empresa=empresa,
            objeto=empresa,
        )

        messages.success(request, f"Empresa '{empresa.razon_social}' actualizada correctamente.")
        return redirect('backoffice:empresas_lista')

    return render(request, 'backoffice/empresa_formulario.html', {
        'empresa': empresa,
        'departamentos': departamentos,
        'planes': planes,
        'modulos': modulos,
        'modulos_activos_ids': list(empresa.modulos.values_list('id', flat=True)),
        'accion': 'Editar'
    })


# ══════════════════════════════════════════════════════════════════════
# GESTIÓN INTEGRAL DE PLANES & SUSCRIPCIONES SAAS
# ══════════════════════════════════════════════════════════════════════

@user_passes_test(es_superadmin)
def planes_lista(request):
    """
    Catálogo comercial y panel de suscripciones de la plataforma SaaS.
    """
    planes = Plan.objects.prefetch_related('modulos', 'empresas').order_by('precio_mensual')
    total_planes = planes.count()
    planes_activos = planes.filter(activo=True).count()
    total_mrr = sum(p.mrr_generado for p in planes)
    total_empresas_suscritas = Empresa.objects.filter(plan__isnull=False, activa=True).count()
    solicitudes_pendientes = (
        SolicitudCambioPlan.objects.filter(estado='PENDIENTE')
        .select_related('empresa', 'plan_actual', 'plan_solicitado', 'solicitado_por')
        .order_by('-created_at')
    )

    context = {
        'planes': planes,
        'total_planes': total_planes,
        'planes_activos': planes_activos,
        'total_mrr': total_mrr,
        'total_empresas_suscritas': total_empresas_suscritas,
        'solicitudes_pendientes': solicitudes_pendientes,
    }
    return render(request, 'backoffice/planes_lista.html', context)


@user_passes_test(es_superadmin)
@require_POST
def solicitud_plan_resolver(request, solicitud_id):
    """Aprueba o rechaza una solicitud de cambio de plan enviada por un administrador inquilino."""
    from django.utils import timezone
    solicitud = get_object_or_404(
        SolicitudCambioPlan.objects.select_related('empresa', 'plan_solicitado', 'solicitado_por'),
        id=solicitud_id,
    )
    accion = request.POST.get('accion', '').strip().lower()
    if solicitud.estado != 'PENDIENTE':
        messages.info(request, "Esta solicitud ya fue procesada anteriormente.")
        return redirect('backoffice:planes_lista')

    if accion == 'aprobar':
        emp = solicitud.empresa
        nuevo_plan = solicitud.plan_solicitado
        emp.plan = nuevo_plan
        emp.activa = True
        emp.save(update_fields=['plan', 'activa'])
        emp.sincronizar_modulos_con_plan(nuevo_plan)
        solicitud.estado = 'APROBADA'
        solicitud.resolved_at = timezone.now()
        solicitud.save(update_fields=['estado', 'resolved_at'])
        dests = (
            [solicitud.solicitado_por.email] if solicitud.solicitado_por and solicitud.solicitado_por.email else []
        ) + destinatarios_propietarios_empresa(emp)
        notificar_evento_seguridad(
            request,
            f"Cambio de plan aprobado: {nuevo_plan.nombre}",
            f"La solicitud de cambio al plan '{nuevo_plan.nombre}' para '{emp.display_name}' fue aprobada y todos sus módulos están activos.",
            destinatarios=dests,
            empresa=emp,
            action_url=request.build_absolute_uri(reverse('core:dashboard')),
            action_text="Ir a FacCol",
        )
        messages.success(request, f"Plan de '{emp.display_name}' actualizado a '{nuevo_plan.nombre}'.")
        registrar_actividad(request, 'planes', 'APROBAR_PLAN', f"Aprobó solicitud y activó '{nuevo_plan.nombre}' en '{emp.display_name}'.", empresa=emp, objeto=solicitud)
    elif accion == 'rechazar':
        emp = solicitud.empresa
        solicitud.estado = 'RECHAZADA'
        solicitud.resolved_at = timezone.now()
        solicitud.save(update_fields=['estado', 'resolved_at'])
        dests = (
            [solicitud.solicitado_por.email] if solicitud.solicitado_por and solicitud.solicitado_por.email else []
        ) + destinatarios_propietarios_empresa(emp)
        notificar_evento_seguridad(
            request,
            f"Solicitud de cambio de plan rechazada: {solicitud.plan_solicitado.nombre}",
            f"La solicitud de cambio al plan '{solicitud.plan_solicitado.nombre}' para '{emp.display_name}' fue rechazada desde Backoffice SaaS.",
            destinatarios=dests,
            empresa=emp,
            action_url=request.build_absolute_uri(reverse('core:dashboard')),
            action_text="Ver detalles en FacCol",
        )
        registrar_actividad(request, 'planes', 'RECHAZAR_PLAN', f"Rechazó solicitud de cambio a '{solicitud.plan_solicitado.nombre}' para '{emp.display_name}'.", empresa=emp, objeto=solicitud)
        messages.info(request, f"Solicitud de '{emp.display_name}' rechazada.")
    else:
        messages.error(request, "Acción inválida sobre la solicitud.")

    return redirect('backoffice:planes_lista')


@user_passes_test(es_superadmin)
def plan_crear(request):
    """
    Crea un nuevo plan comercial definiendo módulos, límites y precio.
    """
    modulos = Modulo.objects.all().order_by('orden', 'nombre')

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        precio_mensual = Decimal(request.POST.get('precio_mensual', '0') or '0')
        limite_facturas_mes = int(request.POST.get('limite_facturas_mes', 500) or 500)
        limite_usuarios = int(request.POST.get('limite_usuarios', 5) or 5)
        duracion_meses = int(request.POST.get('duracion_meses', 1) or 1)
        color_tema = request.POST.get('color_tema', 'amber')
        destacado = 'destacado' in request.POST
        activo = 'activo' in request.POST
        caracteristicas = request.POST.get('caracteristicas', '').strip()

        if not nombre:
            messages.error(request, "El nombre del plan es obligatorio.")
            return render(request, 'backoffice/plan_formulario.html', {
                'modulos': modulos,
                'colores': Plan.COLOR_CHOICES,
                'accion': 'Crear',
            })

        plan = Plan.objects.create(
            nombre=nombre,
            descripcion=descripcion,
            precio_mensual=precio_mensual,
            limite_facturas_mes=limite_facturas_mes,
            limite_usuarios=limite_usuarios,
            duracion_meses=duracion_meses,
            color_tema=color_tema,
            destacado=destacado,
            activo=activo,
            caracteristicas=caracteristicas,
        )

        modulos_ids = request.POST.getlist('modulos[]') or request.POST.getlist('modulos')
        if modulos_ids:
            plan.modulos.set(modulos_ids)

        messages.success(request, f"Plan '{plan.nombre}' creado exitosamente en el catálogo SaaS.")
        return redirect('backoffice:planes_lista')

    return render(request, 'backoffice/plan_formulario.html', {
        'modulos': modulos,
        'colores': Plan.COLOR_CHOICES,
        'accion': 'Crear',
    })


@user_passes_test(es_superadmin)
def plan_editar(request, plan_id):
    """
    Edita un plan comercial existente.
    """
    plan = get_object_or_404(Plan, id=plan_id)
    modulos = Modulo.objects.all().order_by('orden', 'nombre')

    if request.method == 'POST':
        plan.nombre = request.POST.get('nombre', plan.nombre).strip()
        plan.descripcion = request.POST.get('descripcion', '').strip()
        plan.precio_mensual = Decimal(request.POST.get('precio_mensual', '0') or '0')
        plan.limite_facturas_mes = int(request.POST.get('limite_facturas_mes', 500) or 500)
        plan.limite_usuarios = int(request.POST.get('limite_usuarios', 5) or 5)
        plan.duracion_meses = int(request.POST.get('duracion_meses', 1) or 1)
        plan.color_tema = request.POST.get('color_tema', 'amber')
        plan.destacado = 'destacado' in request.POST
        plan.activo = 'activo' in request.POST
        plan.caracteristicas = request.POST.get('caracteristicas', '').strip()

        if not plan.nombre:
            messages.error(request, "El nombre del plan no puede quedar vacío.")
            return render(request, 'backoffice/plan_formulario.html', {
                'plan': plan,
                'modulos': modulos,
                'colores': Plan.COLOR_CHOICES,
                'modulos_activos_ids': list(plan.modulos.values_list('id', flat=True)),
                'accion': 'Editar',
            })

        plan.save()

        modulos_ids = request.POST.getlist('modulos[]') or request.POST.getlist('modulos')
        plan.modulos.set(modulos_ids)

        messages.success(request, f"Plan '{plan.nombre}' actualizado correctamente.")
        return redirect('backoffice:planes_lista')

    return render(request, 'backoffice/plan_formulario.html', {
        'plan': plan,
        'modulos': modulos,
        'colores': Plan.COLOR_CHOICES,
        'modulos_activos_ids': list(plan.modulos.values_list('id', flat=True)),
        'accion': 'Editar',
    })


@user_passes_test(es_superadmin)
def plan_toggle_activo(request, plan_id):
    """
    Activa o pausa un plan comercial.
    """
    plan = get_object_or_404(Plan, id=plan_id)
    plan.activo = not plan.activo
    plan.save(update_fields=['activo'])
    estado_str = "activado" if plan.activo else "pausado"
    messages.success(request, f"El plan '{plan.nombre}' ha sido {estado_str}.")
    return redirect('backoffice:planes_lista')


@user_passes_test(es_superadmin)
def plan_eliminar(request, plan_id):
    """
    Elimina un plan siempre que no tenga empresas vinculadas.
    """
    plan = get_object_or_404(Plan, id=plan_id)
    if plan.empresas.exists():
        messages.warning(
            request,
            f"No es posible eliminar el plan '{plan.nombre}' porque tiene {plan.empresas.count()} empresas suscritas. Puede pausarlo para impedir nuevas suscripciones."
        )
        return redirect('backoffice:planes_lista')

    nombre = plan.nombre
    plan.delete()
    messages.success(request, f"Plan '{nombre}' eliminado correctamente.")
    return redirect('backoffice:planes_lista')


# ══════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN GLOBAL CIFRADA DE CORREO Y WOMPI (SIN EXPOSICIÓN EN .ENV)
# ══════════════════════════════════════════════════════════════════════

@user_passes_test(es_superadmin)
def configuracion_plataforma(request):
    if request.method == 'POST':
        seccion = request.POST.get('seccion', 'all')

        if seccion in ('mail', 'all'):
            set_platform_setting('mail_host', request.POST.get('mail_host', '').strip(), is_secret=False)
            set_platform_setting('mail_port', request.POST.get('mail_port', '587').strip() or '587', is_secret=False)
            set_platform_setting('mail_encryption', request.POST.get('mail_encryption', 'tls').strip(), is_secret=False)
            set_platform_setting('mail_username', request.POST.get('mail_username', '').strip(), is_secret=False)
            set_platform_setting('mail_from_address', request.POST.get('mail_from_address', '').strip(), is_secret=False)
            set_platform_setting('mail_from_name', request.POST.get('mail_from_name', '').strip(), is_secret=False)

            new_mail_pwd = request.POST.get('mail_password', '').strip()
            if 'clear_mail_password' in request.POST:
                set_platform_setting('mail_password', '', is_secret=True)
            elif new_mail_pwd:
                set_platform_setting('mail_password', new_mail_pwd, is_secret=True)

            new_resend = request.POST.get('resend_api_key', '').strip()
            if 'clear_resend_api_key' in request.POST:
                set_platform_setting('resend_api_key', '', is_secret=True)
            elif new_resend:
                set_platform_setting('resend_api_key', new_resend, is_secret=True)

        if seccion in ('wompi', 'all'):
            set_platform_setting('wompi_active_flag', '1' if 'wompi_active_flag' in request.POST else '0', is_secret=False)
            set_platform_setting('wompi_public_key', request.POST.get('wompi_public_key', '').strip(), is_secret=False)

            for field_name, clear_name in (
                ('wompi_private_key', 'clear_wompi_private_key'),
                ('wompi_integrity_secret', 'clear_wompi_integrity_secret'),
                ('wompi_events_secret', 'clear_wompi_events_secret'),
            ):
                if clear_name in request.POST:
                    set_platform_setting(field_name, '', is_secret=True)
                else:
                    val = request.POST.get(field_name, '').strip()
                    if val:
                        set_platform_setting(field_name, val, is_secret=True)

        if seccion in ('ia', 'all'):
            if 'clear_gemini_api_key' in request.POST:
                set_platform_setting('gemini_api_key', '', is_secret=True)
            else:
                gkey = request.POST.get('gemini_api_key', '').strip()
                if gkey:
                    set_platform_setting('gemini_api_key', gkey, is_secret=True)

        if seccion in ('dian_pt', 'all'):
            set_platform_setting('pt_dian_url', request.POST.get('pt_dian_url', '').strip(), is_secret=False)
            if 'clear_pt_dian_token' in request.POST:
                set_platform_setting('pt_dian_token', '', is_secret=True)
            else:
                pt_tok = request.POST.get('pt_dian_token', '').strip()
                if pt_tok:
                    set_platform_setting('pt_dian_token', pt_tok, is_secret=True)

        registrar_actividad(request, 'configuracion', 'CONFIG_PLATAFORMA', f"Actualizó configuración global cifrada (sección '{seccion}').")
        messages.success(request, "Configuración cifrada de plataforma guardada exitosamente en la base de datos.")
        return redirect('backoffice:configuracion_plataforma')

    mail_cfg = get_mail_runtime_config()
    wompi_cfg = get_wompi_runtime_config()
    from apps.core.platform_settings import get_platform_setting, mask_secret
    masked_gemini = mask_secret(get_platform_setting('gemini_api_key', ''))
    pt_dian_url = get_platform_setting('pt_dian_url', '')
    masked_pt_token = mask_secret(get_platform_setting('pt_dian_token', ''))
    webhook_url = request.build_absolute_uri(reverse('facturacion:wompi_webhook'))
    return render(request, 'backoffice/configuracion_plataforma.html', {
        'mail_cfg': mail_cfg,
        'wompi_cfg': wompi_cfg,
        'masked_gemini': masked_gemini,
        'pt_dian_url': pt_dian_url,
        'masked_pt_token': masked_pt_token,
        'webhook_url': webhook_url,
    })


@user_passes_test(es_superadmin)
@require_POST
def configuracion_probar_correo(request):
    destino = request.POST.get('email_prueba', '').strip() or request.user.email
    try:
        enviar_correo_sistema(
            destino,
            "Prueba de Correo Unificado FacCol",
            "<p>Este correo confirma que el servidor unificado de plataforma (Resend API / SMTP) está operativo.</p>",
            fail_silently=False,
        )
        messages.success(request, f"Correo de prueba enviado correctamente a {destino}.")
    except Exception as exc:
        messages.error(request, f"Error enviando correo de prueba: {exc}")
    return redirect('backoffice:configuracion_plataforma')


# ══════════════════════════════════════════════════════════════════════
# COMUNICADOS POR TANDAS ANTI-SPAM (RESEND) & BANNER DE NOVEDADES
# ══════════════════════════════════════════════════════════════════════

@user_passes_test(es_superadmin)
def comunicados_panel(request):
    tab = request.GET.get('tab', 'correo')
    if tab not in ('correo', 'banner'):
        tab = 'correo'
    summary = get_campaign_summary()
    banner = get_tenant_banner()
    return render(request, 'backoffice/comunicados.html', {
        'summary': summary,
        'banner': banner,
        'tab': tab,
        'resend_configured': bool(mail_settings.RESEND_API_KEY),
    })


def _extraer_borrador_comunicado(post_data):
    try:
        batch_size = int(post_data.get('batch_size') or 45)
    except (TypeError, ValueError):
        batch_size = 45
    return {
        'subject': post_data.get('subject', ''),
        'body_text': post_data.get('body_text', ''),
        'cta_label': post_data.get('cta_label', ''),
        'cta_url': post_data.get('cta_url', ''),
        'batch_size': batch_size,
    }


@user_passes_test(es_superadmin)
@require_POST
def comunicado_guardar_borrador(request):
    datos = _extraer_borrador_comunicado(request.POST)
    save_campaign_draft(**datos)
    messages.success(request, "Borrador de comunicado guardado. Realiza el envío de prueba antes de lanzar la tanda.")
    return redirect(f"{reverse('backoffice:comunicados_panel')}?tab=correo")


@user_passes_test(es_superadmin)
@require_POST
def comunicado_probar(request):
    datos = _extraer_borrador_comunicado(request.POST)
    test_email = request.POST.get('test_email', '').strip() or request.user.email
    ok, msg = send_campaign_test_email(
        test_email=test_email,
        admin_name=request.user.get_full_name() or request.user.username,
        **datos,
    )
    if ok:
        registrar_actividad(request, 'comunicados', 'PROBAR_COMUNICADO', f"Envió prueba de comunicado '{datos['subject']}' a {test_email}.")
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect(f"{reverse('backoffice:comunicados_panel')}?tab=correo")


@user_passes_test(es_superadmin)
@require_POST
def comunicado_enviar_tanda(request):
    datos = _extraer_borrador_comunicado(request.POST)
    res = send_campaign_batch(**datos)
    if res.get('ok'):
        registrar_actividad(request, 'comunicados', 'ENVIAR_TANDA', f"Envió tanda comunicado ({res.get('sent_count', 0)} entregados): {res.get('message', '')}")
        messages.success(request, res.get('message', 'Tanda enviada correctamente.'))
    else:
        messages.error(request, res.get('message', 'No se pudo enviar la tanda.'))
    return redirect(f"{reverse('backoffice:comunicados_panel')}?tab=correo")


@user_passes_test(es_superadmin)
@require_POST
def comunicado_reiniciar(request):
    custom_slug = request.POST.get('custom_slug', '').strip() or None
    nuevo_id = reset_campaign_cycle(custom_slug)
    registrar_actividad(request, 'comunicados', 'REINICIAR_CICLO', f"Reinició ciclo de campaña a '{nuevo_id}'.")
    messages.info(request, f"Ciclo de campaña reiniciado con ID '{nuevo_id}'. Debes probar el borrador nuevamente.")
    return redirect(f"{reverse('backoffice:comunicados_panel')}?tab=correo")


@user_passes_test(es_superadmin)
@require_POST
def comunicado_guardar_banner(request):
    cfg = save_tenant_banner({
        'enabled': 'enabled' in request.POST,
        'campaign_id': request.POST.get('campaign_id', ''),
        'badge': request.POST.get('badge', ''),
        'title': request.POST.get('title', ''),
        'message': request.POST.get('message', ''),
        'cta_label': request.POST.get('cta_label', ''),
        'cta_url': request.POST.get('cta_url', ''),
        'secondary_label': request.POST.get('secondary_label', ''),
        'secondary_url': request.POST.get('secondary_url', ''),
    })
    registrar_actividad(request, 'comunicados', 'GUARDAR_BANNER', f"Actualizó banner '{cfg['campaign_id']}' (activo={cfg['enabled']}).")
    messages.success(request, "Banner informativo del panel del usuario actualizado correctamente.")
    return redirect(f"{reverse('backoffice:comunicados_panel')}?tab=banner")


@user_passes_test(es_superadmin)
def auditoria_global_view(request):
    """Bitácora global de plataforma y todas las empresas para el SuperAdmin."""
    empresa_sel = request.GET.get('empresa', '').strip()
    modulo_sel = request.GET.get('modulo', '').strip()
    q = request.GET.get('q', '').strip()

    qs = RegistroActividad.objects.select_related('empresa', 'usuario').all()
    if empresa_sel == 'plataforma':
        qs = qs.filter(empresa__isnull=True)
    elif empresa_sel.isdigit():
        qs = qs.filter(empresa_id=int(empresa_sel))
    if modulo_sel:
        qs = qs.filter(modulo=modulo_sel)
    if q:
        qs = qs.filter(
            Q(usuario_nombre__icontains=q)
            | Q(accion__icontains=q)
            | Q(descripcion__icontains=q)
        )

    empresas_disp = Empresa.objects.order_by('razon_social')
    modulos_disp = (
        RegistroActividad.objects.values_list('modulo', flat=True).distinct().order_by('modulo')
    )
    return render(request, 'core/auditoria_lista.html', {
        'logs': qs[:200],
        'empresas_disp': empresas_disp,
        'modulos_disp': modulos_disp,
        'empresa_sel': empresa_sel,
        'modulo_sel': modulo_sel,
        'q': q,
        'es_superadmin_vista': True,
    })
