import json
import secrets
import time
from decimal import Decimal
from datetime import date, timedelta
from urllib.parse import quote, urlencode
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.db import transaction
from django.db.models import Sum
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.facturacion.models import Factura, FacturaItem, ReciboCaja, TransaccionWompi
from apps.facturacion.pdf_service import generar_pdf_factura, generar_pdf_tirilla, generar_qr_base64
from apps.facturacion.dian_service import desglosar_impuestos_item, generar_xml_ubl
from apps.facturacion.wompi_service import (
    activate_wompi_transaction,
    build_integrity_signature,
    get_wompi_runtime_config,
    sync_wompi_transaction_by_id,
    verify_wompi_event_checksum,
)
from apps.core.mail_service import enviar_correo_sistema
from apps.terceros.models import Tercero
from apps.inventario.models import Producto, MovimientoInventario
from apps.usuarios.decorators import requiere_facturacion


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

    if request.method == 'POST':
        tercero_id = request.POST.get('tercero')
        tipo_documento = request.POST.get('tipo_documento', 'FACTURA')
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
        if tipo_documento == 'POS_ELECTRONICO':
            prefijo = empresa.prefijo_pos_electronico
            consecutivo = empresa.obtener_siguiente_consecutivo_pos()
        else:
            prefijo = empresa.prefijo_factura
            consecutivo = empresa.obtener_siguiente_consecutivo()

        with transaction.atomic():
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
                es_fiado=(metodo_pago == 'CREDITO'),
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
                if prod and prod.maneja_inventario and tipo_documento in ('FACTURA', 'POS_ELECTRONICO', 'REMISION'):
                    saldo_anterior = prod.stock_actual
                    saldo_nuevo = max(Decimal('0.00'), saldo_anterior - cant)
                    prod.stock_actual = saldo_nuevo
                    prod.save(update_fields=['stock_actual'])

                    MovimientoInventario.objects.create(
                        empresa=empresa,
                        producto=prod,
                        tipo='SALIDA',
                        cantidad=cant,
                        costo_unitario=prod.costo_unitario,
                        saldo_anterior=saldo_anterior,
                        saldo_nuevo=saldo_nuevo,
                        motivo=f"Venta con {factura.numero_completo}",
                        referencia_documento=factura.numero_completo,
                    )

            factura.recalcular_totales()
            factura.generar_cufe()

        messages.success(request, f"Factura {factura.numero_completo} emitida exitosamente por ${factura.total:,.0f} COP.")
        return redirect('facturacion:factura_detalle', factura_id=factura.id)

    return render(request, 'facturacion/factura_formulario.html', {
        'terceros': terceros,
        'productos': productos,
        'proximo_consecutivo': f"{empresa.prefijo_factura}{empresa.consecutivo_actual}",
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
            # 1. Generar el PDF dinámico oficial
            pdf_bytes = generar_pdf_factura(factura)
            filename = f"Factura_{factura.numero_completo}.pdf"

            # 2. Asunto y cuerpo del correo
            asunto = f"Factura Electrónica No. {factura.numero_completo} — {empresa.razon_social}"
            contexto = {
                'factura': factura,
                'empresa': empresa,
                'mensaje_personalizado': mensaje_personalizado,
            }
            html_content = render_to_string('facturacion/emails/factura_email.html', contexto)
            text_content = strip_tags(html_content)

            # 3. Enviar usando SMTP de empresa (si tiene) o correo unificado de plataforma
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
    if factura.saldo_pendiente <= 0:
        messages.info(request, "Esta factura no tiene saldo pendiente por pagar.")
        return redirect('facturacion:factura_detalle', factura_id=factura.id)

    wompi_cfg = get_wompi_runtime_config(empresa)
    if not wompi_cfg['enabled']:
        messages.error(request, "La pasarela de pagos Wompi no está habilitada. Configúrala primero en Backoffice.")
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
        saldo_pendiente__gt=0
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
