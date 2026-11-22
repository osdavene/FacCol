import secrets
import time
from decimal import Decimal
from datetime import date
from urllib.parse import urlencode
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import models
from django.db.models import Sum, Count, Q
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.empresas.models import Empresa, Plan, SolicitudCambioPlan
from apps.usuarios.models import User, EmpresaUsuario
from apps.usuarios.decorators import requiere_admin
from apps.facturacion.models import Factura, ReciboCaja, TransaccionWompi
from apps.facturacion.wompi_service import (
    build_integrity_signature,
    get_platform_wompi_config,
    get_wompi_runtime_config,
    sync_wompi_transaction_by_id,
)
from apps.core.audit import registrar_actividad
from apps.core.campaigns import get_tenant_banner
from apps.core.mail_service import enviar_correo_sistema, get_mail_runtime_config
from apps.core.models import RegistroActividad
from apps.core.platform_settings import encrypt_secret
from apps.inventario.models import Producto, MovimientoInventario
from apps.terceros.models import Tercero
from apps.contabilidad.models import AsientoContable, PlanCuenta
from apps.nomina.models import Empleado


def inicio(request):
    """
    Página principal:
    - Visitantes anónimos ven el catálogo comercial SaaS con pago directo de planes vía Wompi de Plataforma.
    - Usuarios autenticados entran directamente a su Dashboard operativo o SuperAdmin.
    """
    if request.user.is_authenticated:
        return dashboard(request)

    planes = Plan.objects.filter(activo=True).prefetch_related('modulos').order_by('precio_mensual')
    wompi_platform = get_platform_wompi_config()
    return render(request, 'core/landing.html', {
        'planes': planes,
        'wompi_platform': wompi_platform,
    })


@require_POST
def suscripcion_wompi_iniciar(request):
    """Inicia el pago de un Plan SaaS desde la página principal usando únicamente el Wompi de la Plataforma."""
    plan_id = request.POST.get('plan_id')
    plan = get_object_or_404(Plan, id=plan_id, activo=True)
    razon_social = request.POST.get('razon_social', '').strip()
    nit = request.POST.get('nit', '').strip()
    email = request.POST.get('email', '').strip().lower()

    destino_error = (
        'core:mi_plan'
        if request.user.is_authenticated and not request.user.es_superadmin_user()
        else 'core:dashboard'
    )

    if not email or not razon_social:
        messages.error(request, "Ingresa el nombre de tu negocio y un correo electrónico válido.")
        return redirect(destino_error)

    if plan.precio_mensual <= 0:
        messages.info(request, "Este plan es gratuito o personalizado; contáctanos para activarlo.")
        return redirect(destino_error)

    wompi_cfg = get_platform_wompi_config()
    if not wompi_cfg['enabled']:
        messages.error(request, "La pasarela de pagos Wompi de la plataforma aún no está habilitada.")
        return redirect(destino_error)

    amount_in_cents = int(plan.precio_mensual * 100)
    currency = "COP"
    reference = f"PLAN-{plan.id}-{int(time.time())}-{secrets.token_hex(3).upper()}"

    TransaccionWompi.objects.create(
        empresa=None,
        plan=plan,
        usuario=request.user if request.user.is_authenticated else None,
        reference=reference,
        amount_in_cents=amount_in_cents,
        currency=currency,
        status='PENDING',
        customer_name=razon_social,
        customer_nit=nit,
        customer_email=email,
    )

    sig = build_integrity_signature(reference, amount_in_cents, currency, wompi_cfg['integrity_secret'])
    redirect_url = request.build_absolute_uri(reverse('core:suscripcion_wompi_retorno'))
    params = {
        "public-key": wompi_cfg["public_key"],
        "currency": currency,
        "amount-in-cents": str(amount_in_cents),
        "reference": reference,
        "signature:integrity": sig,
        "redirect-url": redirect_url,
        "customer-data:email": email,
        "customer-data:full-name": razon_social,
    }
    return redirect(f"https://checkout.wompi.co/p/?{urlencode(params)}")


def suscripcion_wompi_retorno(request):
    """Retorno público tras pagar un Plan SaaS en la pasarela Wompi de la Plataforma."""
    wompi_tx_id = request.GET.get('id', '').strip()
    tx = None
    error_msg = None

    if wompi_tx_id:
        wompi_cfg = get_platform_wompi_config()
        if wompi_cfg['has_required_keys']:
            try:
                tx = sync_wompi_transaction_by_id(wompi_tx_id, wompi_cfg, platform_only=True)
                if tx and tx.status == 'AMOUNT_MISMATCH':
                    error_msg = "El monto pagado en Wompi no coincide con el valor del plan."
            except Exception:
                error_msg = "Estamos verificando tu pago con Wompi."

    return render(request, 'core/suscripcion_retorno.html', {
        'tx': tx,
        'wompi_tx_id': wompi_tx_id,
        'error_msg': error_msg,
    })


@login_required
@requiere_admin
def configuracion_empresa(request):
    """
    Autogestión de credenciales propias por cada empresa inquilina:
    - Su propia pasarela Wompi (para recibir pagos directamente en su cuenta).
    - Sus credenciales DIAN y perfil operativo (Tienda de Barrio vs Estándar).
    - Su propio SMTP opcional (si lo deja vacío usa el correo de la plataforma Resend / SMTP).
    """
    if request.user.es_superadmin_user():
        return redirect('backoffice:configuracion_plataforma')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    if request.method == 'POST':
        seccion = request.POST.get('seccion', 'wompi')

        if seccion == 'wompi':
            empresa.wompi_public_key = request.POST.get('wompi_public_key', '').strip()
            for attr, clear_field in (
                ('wompi_private_key', 'clear_wompi_private_key'),
                ('wompi_integrity_secret', 'clear_wompi_integrity_secret'),
                ('wompi_events_secret', 'clear_wompi_events_secret'),
            ):
                if clear_field in request.POST:
                    setattr(empresa, attr, '')
                else:
                    val = request.POST.get(attr, '').strip()
                    if val:
                        setattr(empresa, attr, encrypt_secret(val))
            empresa.save()
            registrar_actividad(request, 'configuracion', 'CONFIG_WOMPI_EMPRESA', f"Actualizó pasarela Wompi propia de {empresa.display_name}.", empresa=empresa, objeto=empresa)
            messages.success(request, "Pasarela Wompi propia guardada y cifrada para tu empresa.")

        elif seccion == 'dian':
            empresa.perfil_operativo = request.POST.get('perfil_operativo', empresa.perfil_operativo)
            empresa.regimen_tributario = request.POST.get('regimen_tributario', empresa.regimen_tributario)
            empresa.prefijo_factura = (request.POST.get('prefijo_factura', empresa.prefijo_factura) or 'FE').strip().upper()
            empresa.prefijo_pos_electronico = (request.POST.get('prefijo_pos_electronico', empresa.prefijo_pos_electronico) or 'POS').strip().upper()
            empresa.resolucion_dian = request.POST.get('resolucion_dian', '').strip()
            empresa.modo_dian = request.POST.get('modo_dian', empresa.modo_dian)
            empresa.dian_software_id = request.POST.get('dian_software_id', '').strip()
            empresa.dian_pin = request.POST.get('dian_pin', '').strip()
            empresa.dian_test_set_id = request.POST.get('dian_test_set_id', '').strip()
            empresa.save()
            registrar_actividad(request, 'configuracion', 'CONFIG_DIAN_EMPRESA', f"Actualizó parámetros DIAN/perfil operativo de {empresa.display_name}.", empresa=empresa, objeto=empresa)
            messages.success(request, "Configuración DIAN y perfil de negocio actualizados.")

        elif seccion == 'smtp':
            empresa.smtp_host = request.POST.get('smtp_host', '').strip()
            empresa.smtp_port = int(request.POST.get('smtp_port', 587) or 587)
            empresa.smtp_encryption = request.POST.get('smtp_encryption', 'tls').strip()
            empresa.smtp_user = request.POST.get('smtp_user', '').strip()
            empresa.smtp_from_address = request.POST.get('smtp_from_address', '').strip()
            empresa.smtp_from_name = request.POST.get('smtp_from_name', '').strip()
            if 'clear_smtp_password' in request.POST:
                empresa.smtp_password = ''
            else:
                new_pwd = request.POST.get('smtp_password', '').strip()
                if new_pwd:
                    empresa.smtp_password = encrypt_secret(new_pwd)
            if 'clear_resend_api_key' in request.POST:
                empresa.resend_api_key = ''
            else:
                new_resend = request.POST.get('resend_api_key', '').strip()
                if new_resend:
                    empresa.resend_api_key = encrypt_secret(new_resend)
            empresa.save()
            registrar_actividad(request, 'configuracion', 'CONFIG_CORREO_EMPRESA', f"Actualizó configuración de correo independiente para {empresa.display_name}.", empresa=empresa, objeto=empresa)
            messages.success(request, "Configuración de correo de tu empresa actualizada.")

        return redirect('core:configuracion_empresa')

    wompi_cfg = get_wompi_runtime_config(empresa)
    mail_cfg = get_mail_runtime_config(empresa)
    webhook_url = request.build_absolute_uri(reverse('facturacion:wompi_webhook'))
    return render(request, 'core/configuracion_empresa.html', {
        'empresa': empresa,
        'wompi_cfg': wompi_cfg,
        'mail_cfg': mail_cfg,
        'webhook_url': webhook_url,
    })


@login_required
@requiere_admin
@require_POST
def configuracion_empresa_probar_correo(request):
    if request.user.es_superadmin_user():
        return redirect('backoffice:configuracion_plataforma')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    destino = request.POST.get('email_prueba', '').strip() or empresa.email or request.user.email
    mail_cfg = get_mail_runtime_config(empresa)
    origen_label = (
        f"correo propio de {empresa.display_name}"
        if mail_cfg['source'] == 'empresa'
        else "correo oficial de ServiGest (respaldo automático)"
    )
    try:
        enviar_correo_sistema(
            destino,
            f"Prueba de Correo Facturación — {empresa.display_name}",
            f"<p>Este mensaje confirma que las facturas de <b>{empresa.display_name}</b> salen correctamente usando el <b>{origen_label}</b>.</p>",
            empresa=empresa,
            reply_to=[empresa.email] if empresa.email else None,
            fail_silently=False,
        )
        registrar_actividad(request, 'configuracion', 'PROBAR_CORREO_EMPRESA', f"Probó envío de correo hacia {destino} ({mail_cfg['source']}).", empresa=empresa, objeto=empresa)
        messages.success(request, f"Correo de prueba enviado a {destino} usando {origen_label}.")
    except Exception as exc:
        messages.error(request, f"No se pudo enviar la prueba de correo: {exc}")
    return redirect('core:configuracion_empresa')


@login_required
@requiere_admin
def auditoria_empresa_view(request):
    """Bitácora de actividad aislada estrictamente a la empresa activa."""
    if request.user.es_superadmin_user():
        return redirect('backoffice:auditoria_lista')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    modulo_sel = request.GET.get('modulo', '').strip()
    q = request.GET.get('q', '').strip()
    qs = RegistroActividad.objects.filter(empresa=empresa).select_related('usuario')
    if modulo_sel:
        qs = qs.filter(modulo=modulo_sel)
    if q:
        qs = qs.filter(
            Q(usuario_nombre__icontains=q)
            | Q(accion__icontains=q)
            | Q(descripcion__icontains=q)
        )
    modulos_disp = (
        RegistroActividad.objects.filter(empresa=empresa)
        .values_list('modulo', flat=True)
        .distinct()
        .order_by('modulo')
    )
    return render(request, 'core/auditoria_lista.html', {
        'logs': qs[:200],
        'modulos_disp': modulos_disp,
        'modulo_sel': modulo_sel,
        'q': q,
        'es_superadmin_vista': False,
        'empresa': empresa,
    })


@login_required
def dashboard(request):
    is_superadmin = bool(request.user.is_superadmin or request.user.is_superuser)
    empresa = request.empresa_activa
    rol_actual = request.user.rol_actual or ('superadmin' if is_superadmin else 'usuario')

    # Modo Plataforma SaaS SuperAdmin: Centro de Control Global Exclusivo
    if is_superadmin:
        total_empresas = Empresa.objects.count()
        empresas_activas = Empresa.objects.filter(activa=True).count()
        empresas_inactivas = Empresa.objects.filter(activa=False).count()
        total_usuarios = User.objects.count()
        folios_dian_procesados = Factura.objects.count()
        mrr_suscripciones = Empresa.objects.filter(
            activa=True,
            plan__isnull=False
        ).aggregate(total=Sum('plan__precio_mensual'))['total'] or Decimal('0.00')
        empresas_recientes = Empresa.objects.select_related('plan').prefetch_related(
            'usuarios_asignados__user'
        ).order_by('-created_at')[:10]

        context = {
            'is_superadmin': True,
            'rol_actual': 'superadmin',
            'total_empresas': total_empresas,
            'empresas_activas': empresas_activas,
            'empresas_inactivas': empresas_inactivas,
            'total_usuarios': total_usuarios,
            'folios_dian_procesados': folios_dian_procesados,
            'mrr_suscripciones': mrr_suscripciones,
            'empresas_recientes': empresas_recientes,
            'sin_empresa_activa': True,
            'modo_plataforma': True,
        }
        return render(request, 'core/dashboard.html', context)

    # Si no hay empresa activa para un usuario estándar
    if not empresa:
        return render(request, 'core/sin_empresa.html')

    # Métricas principales de la empresa activa
    hoy = date.today()
    primer_dia_mes = hoy.replace(day=1)

    # Ventas del mes (Facturas y DEE POS)
    ventas_mes = Factura.objects.filter(
        empresa=empresa,
        tipo_documento__in=('FACTURA', 'POS_ELECTRONICO'),
        fecha_emision__gte=primer_dia_mes
    ).exclude(estado='ANULADA').aggregate(total=Sum('total'))['total'] or Decimal('0.00')

    # Facturas y Fiados pendientes / Cartera por cobrar
    cartera_pendiente = Factura.objects.filter(
        empresa=empresa,
        tipo_documento__in=('FACTURA', 'POS_ELECTRONICO'),
        saldo_pendiente__gt=Decimal('0.00')
    ).exclude(estado='ANULADA').aggregate(total=Sum('saldo_pendiente'))['total'] or Decimal('0.00')

    # Recaudos de caja del mes
    recaudos_mes = ReciboCaja.objects.filter(
        empresa=empresa,
        fecha__gte=primer_dia_mes
    ).aggregate(total=Sum('valor'))['total'] or Decimal('0.00')

    # Total clientes activos
    total_clientes = Tercero.objects.filter(empresa=empresa, es_cliente=True, activo=True).count()

    # Productos con stock bajo
    productos_stock_bajo = Producto.objects.filter(
        empresa=empresa,
        maneja_inventario=True,
        stock_actual__lte=models.F('stock_minimo'),
        stock_minimo__gt=0,
        activo=True
    )[:6]

    # Últimos 6 documentos fiscales emitidos
    ultimas_facturas = Factura.objects.filter(
        empresa=empresa,
        tipo_documento__in=('FACTURA', 'POS_ELECTRONICO')
    ).select_related('tercero').order_by('-fecha_emision', '-consecutivo')[:6]

    # Métricas adicionales según rol
    ultimos_asientos = []
    total_cuentas_puc = 0
    if is_superadmin or rol_actual in ('propietario', 'admin', 'contador'):
        ultimos_asientos = AsientoContable.objects.filter(
            empresa=empresa
        ).prefetch_related('lineas__cuenta').order_by('-fecha', '-numero_comprobante')[:5]
        total_cuentas_puc = PlanCuenta.objects.filter(empresa=empresa, activa=True).count()

    total_productos = 0
    ultimos_movimientos = []
    if is_superadmin or rol_actual in ('propietario', 'admin', 'operador'):
        total_productos = Producto.objects.filter(empresa=empresa, activo=True).count()
        ultimos_movimientos = MovimientoInventario.objects.filter(
            empresa=empresa
        ).select_related('producto').order_by('-created_at')[:5]

    context = {
        'rol_actual': rol_actual,
        'ventas_mes': ventas_mes,
        'cartera_pendiente': cartera_pendiente,
        'recaudos_mes': recaudos_mes,
        'total_clientes': total_clientes,
        'productos_stock_bajo': productos_stock_bajo,
        'ultimas_facturas': ultimas_facturas,
        'ultimos_asientos': ultimos_asientos,
        'total_cuentas_puc': total_cuentas_puc,
        'total_productos': total_productos,
        'ultimos_movimientos': ultimos_movimientos,
        'announcement_banner': get_tenant_banner(),
    }
    return render(request, 'core/dashboard.html', context)


@login_required
def cambiar_empresa(request, empresa_id):
    # Aislamiento Estricto SaaS:
    # El SuperAdmin gestiona la infraestructura de la plataforma global y no opera inquilinos.
    if request.user.es_superadmin_user():
        messages.info(request, "Como SuperAdmin operas en Modo Plataforma SaaS Global.")
        return redirect('core:dashboard')

    # Solo se permite cambiar a empresas donde el usuario tenga membresía activa
    asignacion = get_object_or_404(
        request.user.empresas_asignadas,
        empresa_id=empresa_id,
        activo=True,
        empresa__activa=True
    )
    empresa = asignacion.empresa

    request.session['empresa_activa_id'] = empresa.id
    registrar_actividad(request, 'empresas', 'CAMBIAR_EMPRESA', f"Cambió contexto activo a {empresa.display_name}.", empresa=empresa, objeto=empresa)
    messages.success(request, f"Contexto activo cambiado a: {empresa.display_name}")
    next_url = request.GET.get('next') or request.META.get('HTTP_REFERER') or 'core:dashboard'
    return redirect(next_url)


@login_required
def cambiar_tema(request, tema):
    if tema in ('oscuro', 'claro', 'dashtrap'):
        request.user.tema = tema
        request.user.save(update_fields=['tema'])
        messages.success(request, f"Tema visual actualizado a: {tema.capitalize()}")
    next_url = request.META.get('HTTP_REFERER') or 'core:dashboard'
    return redirect(next_url)


@login_required
def busqueda_global(request):
    q = request.GET.get('q', '').strip()
    empresa = request.empresa_activa

    if not q or not empresa:
        return JsonResponse({'resultados': []})

    resultados = []

    # Buscar en Clientes
    clientes = Tercero.objects.filter(
        empresa=empresa,
        activo=True
    ).filter(
        Q(razon_social__icontains=q) | Q(numero_documento__icontains=q)
    )[:4]
    for c in clientes:
        resultados.append({
            'tipo': 'Cliente / Proveedor',
            'titulo': c.razon_social,
            'subtitulo': f"Doc: {c.documento_completo}",
            'url': f"/terceros/{c.id}/",
            'icono': 'fa-user'
        })

    # Buscar en Productos
    productos = Producto.objects.filter(
        empresa=empresa,
        activo=True
    ).filter(
        Q(nombre__icontains=q) | Q(codigo__icontains=q)
    )[:4]
    for p in productos:
        resultados.append({
            'tipo': 'Producto',
            'titulo': p.nombre,
            'subtitulo': f"SKU: {p.codigo} - ${p.precio_venta:,.0f}",
            'url': f"/inventario/productos/{p.id}/editar/",
            'icono': 'fa-box'
        })

    # Buscar en Facturas
    facturas = Factura.objects.filter(
        empresa=empresa
    ).filter(
        Q(consecutivo__icontains=q) | Q(tercero__razon_social__icontains=q)
    )[:4]
    for f in facturas:
        resultados.append({
            'tipo': f.get_tipo_documento_display(),
            'titulo': f.numero_completo,
            'subtitulo': f"{f.tercero.razon_social} - ${f.total:,.0f}",
            'url': f"/facturacion/{f.id}/",
            'icono': 'fa-file-invoice'
        })

    return JsonResponse({'resultados': resultados})


@login_required
@requiere_admin
def mi_plan_view(request):
    """Panel para que el administrador de empresa consulte consumo mensual y renueve/mejore su plan vía Wompi Plataforma."""
    if request.user.es_superadmin_user():
        return redirect('backoffice:planes_lista')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    planes = Plan.objects.filter(activo=True).prefetch_related('modulos').order_by('precio_mensual')
    wompi_platform = get_platform_wompi_config()
    docs_mes = empresa.documentos_mes_actual()
    limite_docs = empresa.plan.limite_facturas_mes if empresa.plan else 0
    porcentaje_docs = min(100, int((docs_mes / limite_docs) * 100)) if limite_docs else 0
    usuarios_activos = empresa.usuarios_asignados.filter(activo=True).count()
    solicitud_pendiente = (
        SolicitudCambioPlan.objects.filter(empresa=empresa, estado='PENDIENTE')
        .select_related('plan_solicitado')
        .first()
    )

    return render(request, 'core/mi_plan.html', {
        'empresa': empresa,
        'planes': planes,
        'wompi_platform': wompi_platform,
        'docs_mes': docs_mes,
        'limite_docs': limite_docs,
        'porcentaje_docs': porcentaje_docs,
        'usuarios_activos': usuarios_activos,
        'solicitud_pendiente': solicitud_pendiente,
    })


@login_required
@requiere_admin
@require_POST
def solicitar_cambio_plan(request):
    """Permite activar un plan sin costo o solicitar cambio manual cuando Wompi Plataforma aún no está habilitado."""
    if request.user.es_superadmin_user():
        return redirect('backoffice:planes_lista')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    plan_id = request.POST.get('plan_id')
    plan = get_object_or_404(Plan, id=plan_id, activo=True)

    if empresa.plan_id == plan.id:
        messages.info(request, f"Tu empresa ya tiene activo el plan '{plan.nombre}'.")
        return redirect('core:mi_plan')

    if plan.precio_mensual <= 0:
        empresa.plan = plan
        empresa.save(update_fields=['plan'])
        empresa.sincronizar_modulos_con_plan(plan)
        registrar_actividad(request, 'planes', 'CAMBIAR_PLAN_GRATUITO', f"Cambió plan de {empresa.display_name} a '{plan.nombre}'.", empresa=empresa, objeto=plan)
        messages.success(request, f"Plan actualizado correctamente a '{plan.nombre}'.")
        return redirect('core:mi_plan')

    wompi_platform = get_platform_wompi_config()
    if wompi_platform['enabled']:
        messages.info(request, "Usa el botón de pago Wompi en la tarjeta del plan para activar el cambio de inmediato.")
        return redirect('core:mi_plan')

    SolicitudCambioPlan.objects.filter(empresa=empresa, estado='PENDIENTE').update(estado='RECHAZADA')
    sol = SolicitudCambioPlan.objects.create(
        empresa=empresa,
        plan_actual=empresa.plan,
        plan_solicitado=plan,
        solicitado_por=request.user,
        estado='PENDIENTE',
    )
    registrar_actividad(request, 'planes', 'SOLICITAR_PLAN', f"Solicitó cambiar plan de {empresa.display_name} a '{plan.nombre}'.", empresa=empresa, objeto=sol)

    superadmins = list(
        User.objects.filter(Q(is_superadmin=True) | Q(is_superuser=True), is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )
    mail_cfg = get_mail_runtime_config()
    destinatarios = sorted({e.strip().lower() for e in superadmins if e} | ({mail_cfg['from_address'].strip().lower()} if mail_cfg.get('from_address') else set()))
    plan_actual = empresa.plan.nombre if empresa.plan else "Sin plan"
    cuerpo = (
        f"<h3>Solicitud de cambio de plan SaaS</h3>"
        f"<p><b>Empresa:</b> {empresa.display_name} (NIT {empresa.nit_completo})</p>"
        f"<p><b>Plan actual:</b> {plan_actual}<br>"
        f"<b>Plan solicitado:</b> {plan.nombre} (${plan.precio_mensual:,.0f} COP/mes)</p>"
        f"<p><b>Solicitado por:</b> {request.user.get_full_name() or request.user.username} ({request.user.email})</p>"
    )
    enviado = False
    for dest in destinatarios:
        if enviar_correo_sistema(
            dest,
            f"FacCol — Solicitud de cambio a {plan.nombre} ({empresa.display_name})",
            cuerpo,
            fail_silently=True,
        ):
            enviado = True

    aviso_envio = (
        "Notificamos al administrador por correo y quedó registrada en Backoffice → Planes & Suscripciones para activación."
        if enviado
        else "Quedó registrada en Backoffice → Planes & Suscripciones para que el SuperAdministrador la apruebe en un clic."
    )
    messages.success(
        request,
        f"Solicitud registrada para cambiar de '{plan_actual}' a '{plan.nombre}'. {aviso_envio}",
    )
    return redirect('core:mi_plan')
