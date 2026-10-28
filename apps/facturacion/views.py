import json
from decimal import Decimal
from datetime import date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.db import transaction
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings

from apps.facturacion.models import Factura, FacturaItem, ReciboCaja
from apps.facturacion.pdf_service import generar_pdf_factura
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
        tipo_documento='FACTURA'
    ).select_related('tercero').order_by('-consecutivo')

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

        tercero = get_object_or_404(Tercero, id=tercero_id, empresa=empresa)
        consecutivo = empresa.obtener_siguiente_consecutivo()

        with transaction.atomic():
            factura = Factura.objects.create(
                empresa=empresa,
                tipo_documento=tipo_documento,
                prefijo=empresa.prefijo_factura,
                consecutivo=consecutivo,
                tercero=tercero,
                fecha_emision=fecha_emision,
                fecha_vencimiento=date.today() + timedelta(days=dias_venc),
                estado='EMITIDA',
                metodo_pago=metodo_pago,
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
                precio = Decimal(str(it.get('precio_unitario', 0)))
                porc_desc = Decimal(str(it.get('descuento', 0)))
                iva = prod.tarifa_iva if prod else Decimal('19.00')

                FacturaItem.objects.create(
                    factura=factura,
                    producto=prod,
                    descripcion=desc,
                    cantidad=cant,
                    precio_unitario=precio,
                    porcentaje_descuento=porc_desc,
                    porcentaje_iva=iva,
                )

                # Descontar stock si es factura o remisión y controla inventario
                if prod and prod.maneja_inventario and tipo_documento in ('FACTURA', 'REMISION'):
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
def factura_detalle(request, factura_id):
    empresa = request.empresa_activa
    factura = get_object_or_404(
        Factura.objects.select_related('tercero', 'empresa').prefetch_related('items', 'recibos'),
        id=factura_id,
        empresa=empresa
    )
    return render(request, 'facturacion/factura_detalle.html', {'factura': factura})


@login_required
def factura_pdf(request, factura_id):
    empresa = request.empresa_activa
    factura = get_object_or_404(Factura, id=factura_id, empresa=empresa)
    pdf_bytes = generar_pdf_factura(factura)

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    filename = f"{factura.numero_completo}_{factura.tercero.numero_documento}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


@login_required
def factura_enviar_email(request, factura_id):
    empresa = request.empresa_activa
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

            # 3. Construir el mensaje multipart
            from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'notificaciones@servigest.online')
            reply_to = [empresa.email] if (empresa and empresa.email) else [from_email]

            email = EmailMultiAlternatives(
                subject=asunto,
                body=text_content,
                from_email=from_email,
                to=[destinatario],
                reply_to=reply_to,
            )
            email.attach_alternative(html_content, "text/html")
            email.attach(filename, pdf_bytes, 'application/pdf')

            # 4. Enviar mediante Amazon SES
            email.send(fail_silently=False)

            messages.success(request, f"¡Factura {factura.numero_completo} enviada exitosamente a {destinatario}!")
        except Exception as e:
            messages.error(request, f"No se pudo enviar el correo: {str(e)}")

    return redirect('facturacion:factura_detalle', factura_id=factura.id)


@login_required
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
def cotizaciones_lista(request):
    empresa = request.empresa_activa
    cotizaciones = Factura.objects.filter(
        empresa=empresa, tipo_documento='COTIZACION'
    ).select_related('tercero').order_by('-consecutivo')
    return render(request, 'facturacion/cotizaciones_lista.html', {'cotizaciones': cotizaciones})


@login_required
def remisiones_lista(request):
    empresa = request.empresa_activa
    remisiones = Factura.objects.filter(
        empresa=empresa, tipo_documento='REMISION'
    ).select_related('tercero').order_by('-consecutivo')
    return render(request, 'facturacion/remisiones_lista.html', {'remisiones': remisiones})


@login_required
def notas_credito_lista(request):
    empresa = request.empresa_activa
    notas = Factura.objects.filter(
        empresa=empresa, tipo_documento='NOTA_CREDITO'
    ).select_related('tercero', 'factura_referencia').order_by('-consecutivo')
    return render(request, 'facturacion/notas_credito_lista.html', {'notas': notas})
