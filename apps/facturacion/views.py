import json
import secrets
import time
from decimal import Decimal
from datetime import date, timedelta
from urllib.parse import quote, urlencode
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.db import transaction
from django.db.models import Sum
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import escape, strip_tags
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.facturacion.models import Factura, FacturaItem, ReciboCaja, TransaccionWompi
from apps.facturacion.pdf_service import generar_pdf_factura, generar_pdf_tirilla, generar_qr_base64
from apps.facturacion.dian_service import (
    desglosar_impuestos_item,
    generar_xml_ubl,
    procesar_cola_dian,
    transmitir_documento_dian,
)
from apps.facturacion.wompi_service import (
    activate_wompi_transaction,
    build_integrity_signature,
    get_platform_wompi_config,
    get_wompi_runtime_config,
    sync_wompi_transaction_by_id,
    verify_wompi_event_checksum,
)
from apps.core.audit import registrar_actividad
from apps.core.mail import is_dummy_or_invalid_email
from apps.core.mail_service import enviar_correo_sistema
from apps.core.notifications import (
    destinatarios_propietarios_empresa,
    destinatarios_superadmins,
    notificar_evento_seguridad,
    notificar_stock_critico_si_aplica,
    notificar_usuarios_app,
    verificar_alertas_cupo_plan,
)
from apps.core.mail_service import enviar_correo_sistema
from apps.contabilidad.services import (
    registrar_asiento_nota_credito,
    registrar_asiento_recibo,
    registrar_asiento_venta,
)
from apps.terceros.models import Tercero
from apps.inventario.models import Producto, MovimientoInventario
from apps.usuarios.decorators import requiere_facturacion


def _obtener_prefijo_y_consecutivo(empresa, tipo_documento):
    """Obtiene el prefijo y siguiente consecutivo según el tipo de documento."""
    if tipo_documento == 'POS_ELECTRONICO':
        return empresa.prefijo_pos_electronico, empresa.obtener_siguiente_consecutivo_pos()
    if tipo_documento == 'FACTURA':
        return empresa.prefijo_factura, empresa.obtener_siguiente_consecutivo()

    prefijos = {
        'COTIZACION': 'COT',
        'REMISION': 'REM',
        'NOTA_CREDITO': 'NC',
    }
    prefijo = prefijos.get(tipo_documento, 'DOC')
    ultimo = (
        Factura.objects.filter(empresa=empresa, tipo_documento=tipo_documento, prefijo=prefijo)
        .order_by('-consecutivo')
        .first()
    )
    return prefijo, (ultimo.consecutivo + 1) if ultimo else 1


def _mover_stock_documento(empresa, producto, cantidad, tipo_mov, motivo, referencia):
    """Registra una salida o reversión de inventario de forma uniforme."""
    if not producto or not producto.maneja_inventario:
        return
    saldo_anterior = producto.stock_actual
    if tipo_mov == 'SALIDA':
        saldo_nuevo = max(Decimal('0.00'), saldo_anterior - cantidad)
    else:
        saldo_nuevo = saldo_anterior + cantidad
    producto.stock_actual = saldo_nuevo
    producto.save(update_fields=['stock_actual'])
    MovimientoInventario.objects.create(
        empresa=empresa,
        producto=producto,
        tipo=tipo_mov,
        cantidad=cantidad,
        costo_unitario=producto.costo_unitario,
        saldo_anterior=saldo_anterior,
        saldo_nuevo=saldo_nuevo,
        motivo=motivo,
        referencia_documento=referencia,
    )
    if tipo_mov == 'SALIDA':
        notificar_stock_critico_si_aplica(empresa, producto)


@login_required
@requiere_facturacion
def facturas_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    estado = request.GET.get('estado')
    busqueda = request.GET.get('q', '').strip()

    facturas = Factura.objects.filter(
        empresa=empresa,
        tipo_documento__in=('FACTURA', 'POS_ELECTRONICO')
    ).select_related('tercero').order_by('-fecha_emision', '-id')

    if estado:
        facturas = facturas.filter(estado=estado)

    if busqueda:
        facturas = facturas.filter(
            tercero__razon_social__icontains=busqueda
        )

    return render(request, 'facturacion/facturas_lista.html', {
        'facturas': facturas,
        'estado_sel': estado,
        'busqueda': busqueda,
    })


@login_required
@requiere_facturacion
def factura_crear(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    terceros = Tercero.objects.filter(empresa=empresa, es_cliente=True, activo=True)
    productos = Producto.objects.filter(empresa=empresa, activo=True)
    tipos_validos = ('FACTURA', 'POS_ELECTRONICO', 'COTIZACION', 'REMISION')
    tipo_sel = (request.GET.get('tipo') or 'FACTURA').upper()
    if tipo_sel not in tipos_validos:
        tipo_sel = 'FACTURA'

    if request.method == 'POST':
        tercero_id = request.POST.get('tercero')
        tipo_documento = (request.POST.get('tipo_documento') or 'FACTURA').upper()
        if tipo_documento not in tipos_validos:
            tipo_documento = 'FACTURA'
        metodo_pago = request.POST.get('metodo_pago', 'CONTADO')
        fecha_emision = request.POST.get('fecha_emision') or date.today()
        dias_venc = int(request.POST.get('dias_vencimiento', empresa.dias_vencimiento or 30))
        observaciones = request.POST.get('observaciones', '').strip()

        if tipo_documento in ('FACTURA', 'POS_ELECTRONICO') and not empresa.puede_emitir_documento():
            messages.error(
                request,
                f"Alcanzaste el límite mensual de documentos electrónicos de tu plan ({empresa.plan.limite_facturas_mes}/mes). "
                "Solicita ampliación de plan al administrador."
            )
            return redirect('facturacion:facturas_lista')

        tercero = get_object_or_404(Tercero, id=tercero_id, empresa=empresa)

        with transaction.atomic():
            prefijo, consecutivo = _obtener_prefijo_y_consecutivo(empresa, tipo_documento)
            es_fiscal = tipo_documento in ('FACTURA', 'POS_ELECTRONICO')
            factura = Factura.objects.create(
                empresa=empresa,
                tipo_documento=tipo_documento,
                prefijo=prefijo,
                consecutivo=consecutivo,
                tercero=tercero,
                fecha_emision=fecha_emision,
                fecha_vencimiento=date.today() + timedelta(days=dias_venc),
                estado='EMITIDA',
                metodo_pago=metodo_pago,
                es_fiado=(metodo_pago == 'CREDITO' and es_fiscal),
                estado_dian='PENDIENTE' if es_fiscal else 'NO_APLICA',
                observaciones=observaciones,
            )

            # Procesar items enviados desde el formulario (inputs tipo array o JSON)
            items_json = request.POST.get('items_json')
            if items_json:
                items_data = json.loads(items_json)
            else:
                # Items desde inputs tradicionales
                prod_ids = request.POST.getlist('item_producto[]')
                cantidades = request.POST.getlist('item_cantidad[]')
                precios = request.POST.getlist('item_precio[]')
                items_data = []
                for p_id, cant, pr in zip(prod_ids, cantidades, precios):
                    items_data.append({
                        'producto_id': p_id,
                        'cantidad': float(cant),
                        'precio_unitario': float(pr),
                        'descuento': 0,
                    })

            for it in items_data:
                prod = Producto.objects.filter(id=it.get('producto_id'), empresa=empresa).first()
                desc = it.get('descripcion') or (prod.nombre if prod else "Producto / Servicio")
                cant = Decimal(str(it.get('cantidad', 1)))
                precio_base, iva, otro_imp = desglosar_impuestos_item(prod, it.get('precio_unitario', 0), empresa)
                porc_desc = Decimal(str(it.get('descuento', 0)))

                FacturaItem.objects.create(
                    factura=factura,
                    producto=prod,
                    descripcion=desc,
                    cantidad=cant,
                    precio_unitario=precio_base,
                    porcentaje_descuento=porc_desc,
                    porcentaje_iva=iva,
                    porcentaje_otro_impuesto=otro_imp,
                )

                # Descontar stock si es factura, DEE POS o remisión y controla inventario
                if tipo_documento in ('FACTURA', 'POS_ELECTRONICO', 'REMISION'):
                    _mover_stock_documento(
                        empresa,
                        prod,
                        cant,
                        'SALIDA',
                        f"Venta con {factura.numero_completo}",
                        factura.numero_completo,
                    )

            factura.recalcular_totales()
            if es_fiscal:
                ter = factura.tercero
                if ter.aplica_retefuente or ter.aplica_reteica:
                    base_ret = (factura.subtotal or Decimal('0.00')) - (factura.descuento or Decimal('0.00'))
                    retef = (base_ret * (ter.tarifa_retefuente / Decimal('100.00'))).quantize(Decimal('0.01')) if ter.aplica_retefuente else Decimal('0.00')
                    retei = (base_ret * (ter.tarifa_reteica / Decimal('1000.00'))).quantize(Decimal('0.01')) if ter.aplica_reteica else Decimal('0.00')
                    factura.retenciones = retef + retei
                    factura.save(update_fields=['retenciones'])
                    factura.recalcular_totales()
                factura.generar_cufe()
                registrar_asiento_venta(factura)
            else:
                factura.saldo_pendiente = Decimal('0.00')
                factura.save(update_fields=['saldo_pendiente'])

        registrar_actividad(
            request,
            'facturacion',
            'EMITIR_DOCUMENTO',
            f"Emitió {factura.get_tipo_documento_display()} {factura.numero_completo} por ${factura.total:,.0f} COP.",
            empresa=empresa,
            objeto=factura,
        )
        if es_fiscal:
            verificar_alertas_cupo_plan(empresa, request)
        extra_envio = ""
        if 'enviar_email_cliente' in request.POST and factura.tercero and factura.tercero.email:
            if not is_dummy_or_invalid_email(factura.tercero.email):
                try:
                    _enviar_factura_pdf_cliente(empresa, factura, factura.tercero.email)
                    registrar_actividad(
                        request,
                        'facturacion',
                        'ENVIAR_FACTURA_EMAIL',
                        f"Envió automáticamente {factura.numero_completo} al emitir a {factura.tercero.email}.",
                        empresa=empresa,
                        objeto=factura,
                    )
                    extra_envio = f" y enviada por correo a {factura.tercero.email}"
                except Exception as exc:
                    messages.warning(request, f"Documento emitido, pero falló el envío automático del PDF ({exc}).")
        messages.success(
            request,
            f"{factura.get_tipo_documento_display()} {factura.numero_completo} emitida exitosamente por ${factura.total:,.0f} COP{extra_envio}."
        )
        return redirect('facturacion:factura_detalle', factura_id=factura.id)

    preview_pref = {
        'FACTURA': f"{empresa.prefijo_factura}{empresa.consecutivo_actual}",
        'POS_ELECTRONICO': f"{empresa.prefijo_pos_electronico}{empresa.consecutivo_pos_actual}",
        'COTIZACION': "COT-Auto",
        'REMISION': "REM-Auto",
    }.get(tipo_sel, f"{empresa.prefijo_factura}{empresa.consecutivo_actual}")

    return render(request, 'facturacion/factura_formulario.html', {
        'terceros': terceros,
        'productos': productos,
        'proximo_consecutivo': preview_pref,
        'tipo_sel': tipo_sel,
    })


@login_required
@requiere_facturacion
def factura_detalle(request, factura_id):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    factura = get_object_or_404(
        Factura.objects.select_related('tercero', 'empresa').prefetch_related('items', 'recibos'),
        id=factura_id,
        empresa=empresa
    )
    wompi_cfg = get_wompi_runtime_config(empresa)
    return render(request, 'facturacion/factura_detalle.html', {
        'factura': factura,
        'wompi_enabled': wompi_cfg['enabled'],
    })


@login_required
@requiere_facturacion
def factura_pdf(request, factura_id):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    factura = get_object_or_404(Factura, id=factura_id, empresa=empresa)
    pdf_bytes = generar_pdf_factura(factura)

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    filename = f"{factura.numero_completo}_{factura.tercero.numero_documento}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


@login_required
@requiere_facturacion
def factura_tirilla(request, factura_id):
    """
    Vista imprimible y PDF en formato tirilla térmica (58mm u 80mm) con QR DIAN
    y enlace de envío rápido por WhatsApp para tiendas de barrio.
    """
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    factura = get_object_or_404(
        Factura.objects.select_related('tercero', 'empresa').prefetch_related('items'),
        id=factura_id,
        empresa=empresa
    )
    ancho = 58 if request.GET.get('ancho') == '58' else 80

    if request.GET.get('format') == 'pdf':
        pdf_bytes = generar_pdf_tirilla(factura, ancho_mm=ancho)
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="Tirilla_{factura.numero_completo}_{ancho}mm.pdf"'
        return response

    qr_base64 = generar_qr_base64(factura)
    tel_cliente = ''.join(ch for ch in (factura.tercero.telefono or '') if ch.isdigit())
    if tel_cliente and len(tel_cliente) == 10:
        tel_cliente = '57' + tel_cliente
    resumen_wa = (
        f"Hola {factura.tercero.razon_social}, gracias por tu compra en {empresa.display_name}. "
        f"Documento {factura.numero_completo} por ${factura.total:,.0f} COP. "
        f"{'Saldo fiado: $' + format(factura.saldo_pendiente, ',.0f') + ' COP. ' if factura.saldo_pendiente > 0 else ''}"
        f"CUFE/CUDE: {factura.cufe[:24]}..."
    )
    wa_url = f"https://wa.me/{tel_cliente}?text={quote(resumen_wa)}" if tel_cliente else f"https://wa.me/?text={quote(resumen_wa)}"

    return render(request, 'facturacion/tirilla_termica.html', {
        'factura': factura,
        'ancho': ancho,
        'qr_base64': qr_base64,
        'wa_url': wa_url,
    })


@login_required
@requiere_facturacion
def factura_xml(request, factura_id):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    factura = get_object_or_404(Factura.objects.select_related('tercero', 'empresa').prefetch_related('items'), id=factura_id, empresa=empresa)
    if not factura.cufe:
        factura.generar_cufe()
    xml_str = generar_xml_ubl(factura)
    response = HttpResponse(xml_str, content_type='application/xml; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{factura.numero_completo}.xml"'
    return response


@login_required
@requiere_facturacion
def fiados_lista(request):
    """
    Libreta digital de fiados / cuentas por cobrar de vecinos para tiendas de barrio.
    """
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    pendientes = Factura.objects.filter(
        empresa=empresa,
        saldo_pendiente__gt=Decimal('0.00')
    ).exclude(estado='ANULADA').select_related('tercero').order_by('-es_fiado', 'fecha_vencimiento', '-id')

    total_fiado = pendientes.aggregate(t=Sum('saldo_pendiente'))['t'] or Decimal('0.00')
    return render(request, 'facturacion/fiados_lista.html', {
        'pendientes': pendientes,
        'total_fiado': total_fiado,
    })


def _enviar_factura_pdf_cliente(empresa, factura, destinatario: str, mensaje_personalizado: str = ""):
    """Renderiza PDF + plantilla corporativa y envía la factura por el correo de la empresa/plataforma."""
    pdf_bytes = generar_pdf_factura(factura)
    filename = f"Factura_{factura.numero_completo}.pdf"
    asunto = f"Factura Electrónica No. {factura.numero_completo} — {empresa.razon_social}"
    html_content = render_to_string('facturacion/emails/factura_email.html', {
        'factura': factura,
        'empresa': empresa,
        'mensaje_personalizado': mensaje_personalizado,
    })
    text_content = strip_tags(html_content)
    reply_to = [empresa.email] if (empresa and empresa.email) else None
    enviar_correo_sistema(
        destinatario,
        asunto,
        html_content,
        text_body=text_content,
        reply_to=reply_to,
        attachments=[(filename, pdf_bytes, 'application/pdf')],
        empresa=empresa,
        fail_silently=False,
    )


def _enviar_recordatorio_factura_cliente(empresa, factura) -> str:
    """Envía recordatorio de saldo pendiente al correo del tercero y retorna su correo."""
    correo = (factura.tercero.email or '').strip().lower()
    if not correo or is_dummy_or_invalid_email(correo):
        raise ValueError(f"El cliente {factura.tercero.razon_social} no tiene un correo válido registrado.")

    vence_txt = (
        factura.fecha_vencimiento.strftime('%d/%m/%Y')
        if factura.fecha_vencimiento
        else factura.fecha_emision.strftime('%d/%m/%Y')
    )
    html = (
        f"<h3>Recordatorio de saldo pendiente</h3>"
        f"<p>Hola <b>{escape(factura.tercero.razon_social)}</b>,</p>"
        f"<p>Te recordamos amablemente que tienes un saldo pendiente de "
        f"<b>${factura.saldo_pendiente:,.0f} COP</b> correspondiente al documento "
        f"<b>{escape(factura.numero_completo)}</b> en <b>{escape(empresa.display_name)}</b> "
        f"(vencimiento: {vence_txt}).</p>"
        f"<p>Si ya realizaste tu pago, por favor haz caso omiso a este aviso o responde con tu comprobante.</p>"
    )
    enviar_correo_sistema(
        correo,
        f"Recordatorio de pago {factura.numero_completo} — {empresa.display_name}",
        html,
        reply_to=[empresa.email] if empresa.email else None,
        empresa=empresa,
        fail_silently=False,
    )
    return correo


@login_required
@requiere_facturacion
@require_POST
def fiado_enviar_recordatorio(request, factura_id):
    """Envía recordatorio amistoso de cobro por correo al cliente de un fiado o factura pendiente."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    factura = get_object_or_404(
        Factura.objects.select_related('tercero'),
        id=factura_id,
        empresa=empresa,
    )
    if factura.estado == 'ANULADA' or factura.saldo_pendiente <= Decimal('0.00'):
        messages.info(request, f"El documento {factura.numero_completo} no tiene saldo pendiente.")
        return redirect('facturacion:fiados_lista')

    try:
        correo = _enviar_recordatorio_factura_cliente(empresa, factura)
        registrar_actividad(
            request,
            'facturacion',
            'RECORDATORIO_CARTERA',
            f"Envió recordatorio de cobro por {factura.numero_completo} (${factura.saldo_pendiente:,.0f} COP) a {correo}.",
            empresa=empresa,
            objeto=factura,
        )
        messages.success(request, f"Recordatorio enviado a {correo} por saldo de ${factura.saldo_pendiente:,.0f} COP.")
    except Exception as exc:
        messages.error(request, f"No se pudo enviar el recordatorio: {exc}")
    return redirect('facturacion:fiados_lista')


@login_required
@requiere_facturacion
@require_POST
def fiados_recordar_lote(request):
    """Envía recordatorios de cartera en lote con pausa anti-ráfaga para clientes con correo válido."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    qs = (
        Factura.objects.filter(empresa=empresa, saldo_pendiente__gt=Decimal('0.00'))
        .exclude(estado='ANULADA')
        .select_related('tercero')
        .order_by('fecha_vencimiento', 'id')
    )
    if request.POST.get('solo_vencidos') == '1':
        qs = qs.filter(fecha_vencimiento__lt=date.today())

    pendientes = list(qs)
    is_locmem = 'locmem' in getattr(settings, 'EMAIL_BACKEND', '').lower()
    enviados = 0
    omitidos = 0
    errores = 0
    vistos_terceros = set()

    for idx, fac in enumerate(pendientes):
        correo = (getattr(fac.tercero, 'email', '') or '').strip().lower()
        if not correo or is_dummy_or_invalid_email(correo) or correo in vistos_terceros:
            omitidos += 1
            continue
        vistos_terceros.add(correo)
        try:
            _enviar_recordatorio_factura_cliente(empresa, fac)
            enviados += 1
            if not is_locmem and idx < len(pendientes) - 1:
                time.sleep(0.52)
        except Exception:
            errores += 1

    registrar_actividad(
        request,
        'facturacion',
        'RECORDATORIO_CARTERA_LOTE',
        f"Recordatorio masivo de cartera: {enviados} enviados, {omitidos} omitidos/duplicados, {errores} errores.",
        empresa=empresa,
    )
    if enviados:
        messages.success(request, f"Se enviaron {enviados} recordatorios de cobro ({omitidos} sin correo o repetidos).")
    else:
        messages.info(request, "No se encontraron facturas vencidas con correo válido pendiente de recordar.")
    return redirect('facturacion:fiados_lista')


@login_required
@requiere_facturacion
def factura_enviar_email(request, factura_id):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    factura = get_object_or_404(
        Factura.objects.select_related('tercero', 'empresa'),
        id=factura_id,
        empresa=empresa
    )

    if request.method == 'POST':
        destinatario = request.POST.get('email', '').strip()
        mensaje_personalizado = request.POST.get('mensaje', '').strip()

        if not destinatario and factura.tercero:
            destinatario = factura.tercero.email or ''

        if not destinatario:
            messages.error(request, "Debe especificar una dirección de correo electrónico válida.")
            return redirect('facturacion:factura_detalle', factura_id=factura.id)

        try:
            _enviar_factura_pdf_cliente(empresa, factura, destinatario, mensaje_personalizado)

            registrar_actividad(
                request,
                'facturacion',
                'ENVIAR_FACTURA_EMAIL',
                f"Envió {factura.numero_completo} por correo a {destinatario}.",
                empresa=empresa,
                objeto=factura,
            )
            messages.success(request, f"¡Factura {factura.numero_completo} enviada exitosamente a {destinatario}!")
        except Exception as e:
            messages.error(request, f"No se pudo enviar el correo: {str(e)}")

    return redirect('facturacion:factura_detalle', factura_id=factura.id)


@login_required
@requiere_facturacion
@require_POST
def wompi_iniciar(request, factura_id):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    factura = get_object_or_404(Factura.objects.select_related('tercero'), id=factura_id, empresa=empresa)
    if factura.estado == 'ANULADA':
        messages.error(request, "No se puede cobrar por Wompi una factura anulada.")
        return redirect('facturacion:factura_detalle', factura_id=factura.id)
    if factura.saldo_pendiente <= 0:
        messages.info(request, "Esta factura no tiene saldo pendiente por pagar.")
        return redirect('facturacion:factura_detalle', factura_id=factura.id)

    wompi_cfg = get_wompi_runtime_config(empresa)
    if not wompi_cfg['enabled']:
        messages.error(
            request,
            "Tu empresa aún no tiene configurada su propia pasarela Wompi. "
            "Ingrésala en Configuración de Empresa para recibir pagos directamente en tu cuenta."
        )
        return redirect('facturacion:factura_detalle', factura_id=factura.id)

    amount_in_cents = int(factura.saldo_pendiente * 100)
    currency = "COP"
    reference = f"FC-{empresa.id}-{factura.id}-{int(time.time())}-{secrets.token_hex(3).upper()}"

    TransaccionWompi.objects.create(
        empresa=empresa,
        reference=reference,
        factura=factura,
        usuario=request.user,
        amount_in_cents=amount_in_cents,
        currency=currency,
        status='PENDING',
        customer_email=factura.tercero.email or empresa.email or request.user.email,
    )

    sig = build_integrity_signature(reference, amount_in_cents, currency, wompi_cfg['integrity_secret'])
    redirect_url = request.build_absolute_uri(reverse('facturacion:wompi_retorno'))
    params = {
        "public-key": wompi_cfg["public_key"],
        "currency": currency,
        "amount-in-cents": str(amount_in_cents),
        "reference": reference,
        "signature:integrity": sig,
        "redirect-url": redirect_url,
        "customer-data:email": factura.tercero.email or empresa.email or request.user.email,
        "customer-data:full-name": factura.tercero.razon_social,
    }
    return redirect(f"https://checkout.wompi.co/p/?{urlencode(params)}")


@login_required
def wompi_retorno(request):
    empresa = request.empresa_activa
    wompi_tx_id = request.GET.get('id', '').strip()
    tx = None
    error_msg = None

    if empresa and wompi_tx_id:
        wompi_cfg = get_wompi_runtime_config(empresa)
        if wompi_cfg['has_required_keys']:
            try:
                tx = sync_wompi_transaction_by_id(wompi_tx_id, wompi_cfg, expected_empresa=empresa)
                if tx and tx.status == 'AMOUNT_MISMATCH':
                    error_msg = "El monto reportado por Wompi no coincide con el saldo de la factura."
            except Exception:
                error_msg = "Estamos verificando la transacción con Wompi."

    if not tx and empresa:
        tx = TransaccionWompi.objects.filter(empresa=empresa).order_by('-created_at').first()

    return render(request, 'facturacion/wompi_retorno.html', {
        'tx': tx,
        'wompi_tx_id': wompi_tx_id,
        'error_msg': error_msg,
    })


@csrf_exempt
@require_POST
def wompi_webhook(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'error': 'Invalid JSON payload'}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({'error': 'Invalid payload'}, status=400)

    data = payload.get('data') or {}
    tx_data = data.get('transaction') if isinstance(data, dict) else {}
    if not isinstance(tx_data, dict):
        return JsonResponse({'status': 'invalid_transaction'}, status=400)

    reference = str(tx_data.get('reference') or '').strip()
    if not reference:
        return JsonResponse({'status': 'missing_reference'}, status=200)

    tx = TransaccionWompi.objects.select_related('empresa', 'factura').filter(reference=reference).first()
    if not tx:
        return JsonResponse({'status': 'reference_not_found'}, status=200)

    wompi_cfg = get_wompi_runtime_config(tx.empresa)
    events_secret = wompi_cfg.get('events_secret', '')
    if not events_secret:
        return JsonResponse({'error': 'Webhook secret not configured'}, status=503)

    header_checksum = request.headers.get('X-Event-Checksum', '')
    if not verify_wompi_event_checksum(payload, header_checksum, events_secret):
        return JsonResponse({'error': 'Invalid event signature'}, status=401)

    ev_env = str(payload.get('environment') or '').strip().lower()
    if ev_env:
        expected_env = 'prod' if wompi_cfg.get('is_production') else 'test'
        if ev_env != expected_env:
            return JsonResponse({'error': 'Environment mismatch'}, status=400)

    if payload.get('event') != 'transaction.updated':
        return JsonResponse({'status': 'ignored'}, status=200)

    wompi_status = str(tx_data.get('status') or 'PENDING').upper()
    paid_cents = int(tx_data.get('amount_in_cents') or 0)
    paid_currency = str(tx_data.get('currency') or '').upper()
    real_id = str(tx_data.get('id') or tx.wompi_transaction_id or '').strip()

    if tx.activated_at is not None:
        if wompi_status == 'VOIDED' and tx.status != 'VOIDED':
            tx.status = 'VOIDED'
            tx.save(update_fields=['status', 'updated_at'])
            emp_notif = tx.empresa or tx.empresa_creada
            dests = destinatarios_propietarios_empresa(emp_notif) if emp_notif else destinatarios_superadmins()
            resumen_void = f"La transacción Wompi {tx.reference} cambió a estado ANULADA (VOIDED)."
            notificar_usuarios_app(
                f"Transacción Wompi anulada: {tx.reference}",
                resumen_void,
                empresa=emp_notif,
                roles=('propietario', 'admin', 'contador') if emp_notif else (),
                superadmins=not bool(emp_notif),
                nivel='warning',
                categoria='wompi',
                url=reverse('facturacion:factura_detalle', args=[tx.factura_id]) if tx.factura_id else reverse('backoffice:empresas_lista'),
            )
            notificar_evento_seguridad(
                request,
                f"Pago Wompi anulado ({tx.reference})",
                resumen_void,
                destinatarios=dests,
                empresa=emp_notif,
                categoria='wompi',
                nivel='warning',
                registrar_in_app=False,
            )
        return JsonResponse({'status': 'ok'}, status=200)

    if real_id and TransaccionWompi.objects.filter(wompi_transaction_id=real_id, activated_at__isnull=False).exclude(id=tx.id).exists():
        return JsonResponse({'status': 'duplicate_tx_ignored'}, status=200)

    tx.wompi_transaction_id = real_id
    tx.payment_method_type = str(tx_data.get('payment_method_type') or tx.payment_method_type or '')
    tx.customer_email = str(tx_data.get('customer_email') or tx.customer_email or '')
    tx.raw_payload = tx_data

    if wompi_status == 'APPROVED':
        if tx.amount_in_cents > 0 and paid_cents >= tx.amount_in_cents and paid_currency == tx.currency:
            tx.status = 'APPROVED'
            tx.save()
            activate_wompi_transaction(tx)
        else:
            tx.status = 'AMOUNT_MISMATCH'
            tx.save()
            emp_notif = tx.empresa or tx.empresa_creada
            dests = destinatarios_propietarios_empresa(emp_notif) if emp_notif else destinatarios_superadmins()
            resumen_mm = (
                f"Anomalía en pago Wompi {tx.reference}: esperado {tx.amount_in_cents} {tx.currency}, "
                f"recibido {paid_cents} {paid_currency}."
            )
            notificar_usuarios_app(
                f"Anomalía en pago Wompi: {tx.reference}",
                resumen_mm,
                empresa=emp_notif,
                roles=('propietario', 'admin') if emp_notif else (),
                superadmins=not bool(emp_notif),
                nivel='danger',
                categoria='wompi',
                url=reverse('facturacion:factura_detalle', args=[tx.factura_id]) if tx.factura_id else reverse('backoffice:empresas_lista'),
            )
            notificar_evento_seguridad(
                request,
                f"Alerta pago Wompi con monto inconsistente ({tx.reference})",
                resumen_mm,
                destinatarios=dests,
                empresa=emp_notif,
                categoria='wompi',
                nivel='danger',
                registrar_in_app=False,
            )
    else:
        tx.status = wompi_status
        tx.save()

    return JsonResponse({'status': 'ok'}, status=200)


@login_required
@requiere_facturacion
def recibos_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    recibos = ReciboCaja.objects.filter(empresa=empresa).select_related('factura', 'tercero').order_by('-consecutivo')
    return render(request, 'facturacion/recibos_lista.html', {'recibos': recibos})


@login_required
@requiere_facturacion
def recibo_crear(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    factura_id = request.GET.get('factura')
    factura_sel = None
    if factura_id:
        factura_sel = Factura.objects.filter(id=factura_id, empresa=empresa).first()

    facturas_pendientes = Factura.objects.filter(
        empresa=empresa,
        tipo_documento__in=('FACTURA', 'POS_ELECTRONICO'),
        saldo_pendiente__gt=0,
    ).select_related('tercero')

    terceros = Tercero.objects.filter(empresa=empresa, es_cliente=True, activo=True)

    if request.method == 'POST':
        f_id = request.POST.get('factura')
        fac = Factura.objects.filter(id=f_id, empresa=empresa).first() if f_id else None
        tercero_id = request.POST.get('tercero')
        tercero = fac.tercero if fac else get_object_or_404(Tercero, id=tercero_id, empresa=empresa)

        valor = Decimal(request.POST.get('valor', '0') or '0')
        metodo = request.POST.get('metodo_pago', 'EFECTIVO')
        referencia = request.POST.get('referencia_pago', '').strip()
        observaciones = request.POST.get('observaciones', '').strip()

        with transaction.atomic():
            ultimo_recibo = ReciboCaja.objects.filter(empresa=empresa).order_by('-consecutivo').first()
            nuevo_consecutivo = (ultimo_recibo.consecutivo + 1) if ultimo_recibo else 1

            recibo = ReciboCaja.objects.create(
                empresa=empresa,
                consecutivo=nuevo_consecutivo,
                factura=fac,
                tercero=tercero,
                fecha=date.today(),
                valor=valor,
                metodo_pago=metodo,
                referencia_pago=referencia,
                observaciones=observaciones,
            )

            if fac:
                fac.recalcular_totales()

            registrar_asiento_recibo(recibo)

        registrar_actividad(
            request,
            'facturacion',
            'RECIBO_CAJA',
            f"Registró Recibo RC-{recibo.consecutivo} por ${recibo.valor:,.0f} COP.",
            empresa=empresa,
            objeto=recibo,
        )
        doc_ref = f" aplicado a {fac.numero_completo} (saldo restante: ${fac.saldo_pendiente:,.0f} COP)" if fac else ""
        notificar_usuarios_app(
            f"Recibo de Caja RC-{recibo.consecutivo}",
            f"Ingreso de ${recibo.valor:,.0f} COP ({metodo}) de {tercero.razon_social}{doc_ref}.",
            empresa=empresa,
            roles=('propietario', 'admin', 'contador'),
            nivel='success',
            categoria='facturacion',
            url=reverse('facturacion:recibos_lista'),
        )
        correo_cli = (getattr(tercero, 'email', '') or '').strip().lower()
        if correo_cli and not is_dummy_or_invalid_email(correo_cli):
            enviar_correo_sistema(
                correo_cli,
                f"Comprobante de pago RC-{recibo.consecutivo} — {empresa.display_name}",
                (
                    f"<h3>Comprobante de Recibo de Caja</h3>"
                    f"<p>Hola <b>{escape(tercero.razon_social)}</b>,</p>"
                    f"<p>Confirmamos tu pago por <b>${recibo.valor:,.0f} COP</b> mediante <b>{escape(metodo)}</b> "
                    f"(Recibo <b>RC-{recibo.consecutivo}</b>{escape(doc_ref)}).</p>"
                ),
                reply_to=[empresa.email] if empresa.email else None,
                empresa=empresa,
                fail_silently=True,
            )
        messages.success(request, f"Recibo RC-{recibo.consecutivo} registrado exitosamente por ${recibo.valor:,.0f} COP.")
        return redirect('facturacion:recibos_lista')

    return render(request, 'facturacion/recibo_formulario.html', {
        'factura_sel': factura_sel,
        'facturas_pendientes': facturas_pendientes,
        'terceros': terceros,
    })


@login_required
@requiere_facturacion
def cotizaciones_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    cotizaciones = Factura.objects.filter(
        empresa=empresa, tipo_documento='COTIZACION'
    ).select_related('tercero').order_by('-consecutivo')
    return render(request, 'facturacion/cotizaciones_lista.html', {'cotizaciones': cotizaciones})


@login_required
@requiere_facturacion
def remisiones_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    remisiones = Factura.objects.filter(
        empresa=empresa, tipo_documento='REMISION'
    ).select_related('tercero').order_by('-consecutivo')
    return render(request, 'facturacion/remisiones_lista.html', {'remisiones': remisiones})


@login_required
@requiere_facturacion
def notas_credito_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    notas = Factura.objects.filter(
        empresa=empresa, tipo_documento='NOTA_CREDITO'
    ).select_related('tercero', 'factura_referencia').order_by('-consecutivo')
    return render(request, 'facturacion/notas_credito_lista.html', {'notas': notas})


@login_required
@requiere_facturacion
@require_POST
def cotizacion_convertir_factura(request, factura_id):
    """Convierte en 1 clic una Cotización en Factura Electrónica con descuento de inventario y asiento."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    cotizacion = get_object_or_404(
        Factura.objects.select_related('tercero', 'factura_referencia').prefetch_related('items__producto'),
        id=factura_id,
        empresa=empresa,
        tipo_documento='COTIZACION',
    )

    if cotizacion.factura_referencia:
        messages.info(request, f"La cotización {cotizacion.numero_completo} ya fue convertida en {cotizacion.factura_referencia.numero_completo}.")
        return redirect('facturacion:factura_detalle', factura_id=cotizacion.factura_referencia.id)

    if cotizacion.estado == 'ANULADA':
        messages.error(request, "No es posible convertir una cotización anulada.")
        return redirect('facturacion:factura_detalle', factura_id=cotizacion.id)

    if not empresa.puede_emitir_documento():
        messages.error(
            request,
            f"Alcanzaste el límite mensual de documentos electrónicos de tu plan ({empresa.plan.limite_facturas_mes}/mes)."
        )
        return redirect('facturacion:factura_detalle', factura_id=cotizacion.id)

    with transaction.atomic():
        prefijo, consecutivo = _obtener_prefijo_y_consecutivo(empresa, 'FACTURA')
        dias_venc = empresa.dias_vencimiento or 30
        factura = Factura.objects.create(
            empresa=empresa,
            tipo_documento='FACTURA',
            prefijo=prefijo,
            consecutivo=consecutivo,
            tercero=cotizacion.tercero,
            fecha_emision=date.today(),
            fecha_vencimiento=date.today() + timedelta(days=dias_venc),
            estado='EMITIDA',
            metodo_pago=cotizacion.metodo_pago,
            es_fiado=(cotizacion.metodo_pago == 'CREDITO'),
            observaciones=f"Generada desde Cotización {cotizacion.numero_completo}. {cotizacion.observaciones}".strip(),
        )

        for it in cotizacion.items.all():
            FacturaItem.objects.create(
                factura=factura,
                producto=it.producto,
                descripcion=it.descripcion,
                cantidad=it.cantidad,
                precio_unitario=it.precio_unitario,
                porcentaje_descuento=it.porcentaje_descuento,
                porcentaje_iva=it.porcentaje_iva,
                porcentaje_otro_impuesto=it.porcentaje_otro_impuesto,
            )
            _mover_stock_documento(
                empresa,
                it.producto,
                it.cantidad,
                'SALIDA',
                f"Conversión de cotización {cotizacion.numero_completo} a {factura.numero_completo}",
                factura.numero_completo,
            )

        factura.recalcular_totales()
        ter = factura.tercero
        if ter.aplica_retefuente or ter.aplica_reteica:
            base_ret = (factura.subtotal or Decimal('0.00')) - (factura.descuento or Decimal('0.00'))
            retef = (base_ret * (ter.tarifa_retefuente / Decimal('100.00'))).quantize(Decimal('0.01')) if ter.aplica_retefuente else Decimal('0.00')
            retei = (base_ret * (ter.tarifa_reteica / Decimal('1000.00'))).quantize(Decimal('0.01')) if ter.aplica_reteica else Decimal('0.00')
            factura.retenciones = retef + retei
            factura.save(update_fields=['retenciones'])
            factura.recalcular_totales()
        factura.generar_cufe()
        registrar_asiento_venta(factura)

        cotizacion.factura_referencia = factura
        cotizacion.estado = 'PAGADA'
        cotizacion.save(update_fields=['factura_referencia', 'estado'])

    messages.success(
        request,
        f"Cotización {cotizacion.numero_completo} convertida en Factura {factura.numero_completo} por ${factura.total:,.0f} COP."
    )
    return redirect('facturacion:factura_detalle', factura_id=factura.id)


@login_required
@requiere_facturacion
@require_POST
def factura_emitir_nota_credito(request, factura_id):
    """Emite Nota de Crédito por anulación total, reversa stock de inventario y genera asiento contable."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    origen = get_object_or_404(
        Factura.objects.select_related('tercero').prefetch_related('items__producto', 'recibos'),
        id=factura_id,
        empresa=empresa,
    )

    if origen.tipo_documento not in ('FACTURA', 'POS_ELECTRONICO'):
        messages.error(request, "Solo se pueden emitir Notas Crédito sobre Facturas o DEE POS.")
        return redirect('facturacion:factura_detalle', factura_id=origen.id)

    if origen.estado == 'ANULADA':
        messages.info(request, f"El documento {origen.numero_completo} ya se encuentra anulado.")
        return redirect('facturacion:factura_detalle', factura_id=origen.id)

    motivo = request.POST.get('motivo', '').strip() or f"Anulación total de {origen.numero_completo}"

    with transaction.atomic():
        pref_nc, cons_nc = _obtener_prefijo_y_consecutivo(empresa, 'NOTA_CREDITO')
        nc = Factura.objects.create(
            empresa=empresa,
            tipo_documento='NOTA_CREDITO',
            prefijo=pref_nc,
            consecutivo=cons_nc,
            tercero=origen.tercero,
            fecha_emision=date.today(),
            fecha_vencimiento=date.today(),
            estado='EMITIDA',
            metodo_pago=origen.metodo_pago,
            es_fiado=False,
            observaciones=motivo,
            factura_referencia=origen,
        )

        for it in origen.items.all():
            FacturaItem.objects.create(
                factura=nc,
                producto=it.producto,
                descripcion=it.descripcion,
                cantidad=it.cantidad,
                precio_unitario=it.precio_unitario,
                porcentaje_descuento=it.porcentaje_descuento,
                porcentaje_iva=it.porcentaje_iva,
                porcentaje_otro_impuesto=it.porcentaje_otro_impuesto,
            )
            _mover_stock_documento(
                empresa,
                it.producto,
                it.cantidad,
                'ENTRADA',
                f"Devolución por Nota Crédito {nc.numero_completo} ({origen.numero_completo})",
                nc.numero_completo,
            )

        nc.recalcular_totales()
        nc.saldo_pendiente = Decimal('0.00')
        nc.save(update_fields=['saldo_pendiente'])
        nc.generar_cufe()

        origen.estado = 'ANULADA'
        origen.saldo_pendiente = Decimal('0.00')
        origen.save(update_fields=['estado', 'saldo_pendiente'])

        registrar_asiento_nota_credito(nc, factura_origen=origen)

    registrar_actividad(
        request,
        'facturacion',
        'NOTA_CREDITO',
        f"Emitió Nota Crédito {nc.numero_completo} anulando {origen.numero_completo} por ${nc.total:,.0f} COP.",
        empresa=empresa,
        objeto=nc,
    )
    nc_url = reverse('facturacion:factura_detalle', args=[nc.id])
    resumen_nc = (
        f"Se emitió Nota Crédito {nc.numero_completo} por ${nc.total:,.0f} COP "
        f"anulando {origen.numero_completo}. Motivo: {motivo}"
    )
    notificar_usuarios_app(
        f"Nota Crédito {nc.numero_completo} (anulación)",
        resumen_nc,
        empresa=empresa,
        roles=('propietario', 'admin', 'contador'),
        nivel='warning',
        categoria='facturacion',
        url=nc_url,
    )
    notificar_evento_seguridad(
        request,
        f"Nota Crédito emitida: {nc.numero_completo}",
        resumen_nc,
        destinatarios=destinatarios_propietarios_empresa(
            empresa, incluir_roles=('propietario', 'contador'), excluir_emails=[request.user.email]
        ),
        empresa=empresa,
        action_url=request.build_absolute_uri(nc_url),
        action_text="Ver Nota Crédito",
        categoria='facturacion',
        nivel='warning',
        registrar_in_app=False,
    )
    correo_cli = (getattr(origen.tercero, 'email', '') or '').strip().lower()
    if correo_cli and not is_dummy_or_invalid_email(correo_cli):
        enviar_correo_sistema(
            correo_cli,
            f"Nota Crédito {nc.numero_completo} — {empresa.display_name}",
            (
                f"<h3>Notificación de Nota Crédito</h3>"
                f"<p>Hola <b>{escape(origen.tercero.razon_social)}</b>,</p>"
                f"<p>Informamos que se emitió la Nota Crédito <b>{escape(nc.numero_completo)}</b> "
                f"anulando el documento <b>{escape(origen.numero_completo)}</b> por valor de <b>${nc.total:,.0f} COP</b>.</p>"
                f"<p><b>Motivo:</b> {escape(motivo)}</p>"
            ),
            empresa=empresa,
            fail_silently=True,
        )
    messages.success(
        request,
        f"Nota Crédito {nc.numero_completo} emitida por ${nc.total:,.0f} COP. Documento {origen.numero_completo} anulado e inventario reversado."
    )
    return redirect('facturacion:factura_detalle', factura_id=nc.id)


@login_required
@requiere_facturacion
def dian_cola_view(request):
    """Panel de monitoreo y envío asíncrono DIAN (Habilitación y Producción)."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    documentos = Factura.objects.filter(
        empresa=empresa,
        tipo_documento__in=('FACTURA', 'POS_ELECTRONICO', 'NOTA_CREDITO'),
    ).select_related('tercero').order_by('-fecha_emision', '-id')[:60]

    resumen = {
        'pendientes': Factura.objects.filter(empresa=empresa, estado_dian='PENDIENTE').count(),
        'contingencia': Factura.objects.filter(empresa=empresa, estado_dian='CONTINGENCIA').count(),
        'enviadas': Factura.objects.filter(empresa=empresa, estado_dian='ENVIADA').count(),
        'rechazadas': Factura.objects.filter(empresa=empresa, estado_dian='RECHAZADA').count(),
    }
    return render(request, 'facturacion/dian_cola.html', {
        'documentos': documentos,
        'resumen': resumen,
    })


@login_required
@requiere_facturacion
@require_POST
def dian_transmitir_documento(request, factura_id):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    factura = get_object_or_404(Factura, id=factura_id, empresa=empresa)
    ok, msg = transmitir_documento_dian(factura)
    if ok:
        messages.success(request, f"{factura.numero_completo}: {msg}")
    else:
        messages.warning(request, f"{factura.numero_completo}: {msg}")
    return redirect('facturacion:dian_cola')


@login_required
@requiere_facturacion
@require_POST
def dian_procesar_lote(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    resultado = procesar_cola_dian(empresa)
    messages.info(
        request,
        f"Lote DIAN procesado: {resultado['enviados']} aceptado(s), "
        f"{resultado['contingencia']} en contingencia."
    )
    return redirect('facturacion:dian_cola')
