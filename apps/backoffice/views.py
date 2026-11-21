from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import user_passes_test
from django.contrib import messages
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_POST
from apps.empresas.models import Empresa, Plan, Modulo, Departamento, Municipio
from apps.usuarios.models import User, EmpresaUsuario
from apps.facturacion.models import Factura
from apps.core.platform_settings import encrypt_secret, set_platform_setting
from apps.core.mail_service import (
    enviar_correo_sistema,
    enviar_notificacion_bienvenida_empresa,
    get_mail_runtime_config,
)
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

        plan_id = request.POST.get('plan')
        empresa.plan_id = plan_id if plan_id else None

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

        # Configuración SMTP propia de empresa (solo sobrescribe clave si se ingresa nueva)
        empresa.smtp_host = request.POST.get('smtp_host', '').strip()
        empresa.smtp_port = int(request.POST.get('smtp_port', 587) or 587)
        empresa.smtp_user = request.POST.get('smtp_user', '').strip()
        new_smtp_pwd = request.POST.get('smtp_password', '').strip()
        if new_smtp_pwd:
            empresa.smtp_password = encrypt_secret(new_smtp_pwd)
        empresa.smtp_from_address = request.POST.get('smtp_from_address', '').strip()

        empresa.activa = 'activa' in request.POST
        empresa.save()

        # Actualizar módulos
        modulos_seleccionados = request.POST.getlist('modulos[]')
        empresa.modulos.clear()
        for mod_id in modulos_seleccionados:
            empresa.modulos.add(mod_id)

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

    context = {
        'planes': planes,
        'total_planes': total_planes,
        'planes_activos': planes_activos,
        'total_mrr': total_mrr,
        'total_empresas_suscritas': total_empresas_suscritas,
    }
    return render(request, 'backoffice/planes_lista.html', context)


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
            "<p>Este correo confirma que el servidor unificado de plataforma (Amazon SES / Resend) está operativo y cifrado en base de datos.</p>",
            fail_silently=False,
        )
        messages.success(request, f"Correo de prueba enviado correctamente a {destino}.")
    except Exception as exc:
        messages.error(request, f"Error enviando correo de prueba: {exc}")
    return redirect('backoffice:configuracion_plataforma')
