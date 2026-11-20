import json
from decimal import Decimal
from datetime import date, datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db import transaction

from django.urls import reverse
from apps.pos.models import Caja, CajaTurno, MovimientoCaja
from apps.inventario.models import Producto, MovimientoInventario
from apps.terceros.models import Tercero
from apps.facturacion.models import Factura, FacturaItem, ReciboCaja
from apps.facturacion.dian_service import desglosar_impuestos_item
from apps.contabilidad.services import registrar_asiento_venta
from apps.usuarios.decorators import requiere_facturacion


@login_required
@requiere_facturacion
def pos_terminal(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    # Verificar si el usuario tiene turno de caja abierto
    turno = CajaTurno.objects.filter(
        empresa=empresa,
        usuario=request.user,
        estado='ABIERTA'
    ).first()

    if not turno:
        messages.warning(request, "Debe abrir un turno de caja antes de realizar ventas en el Punto de Venta.")
        return redirect('pos:caja_abrir_turno')

    # Asegurar Consumidor Final (222222222222) requerido por normativa DIAN
    consumidor_final, _ = Tercero.objects.get_or_create(
        empresa=empresa,
        numero_documento='222222222222',
        defaults={
            'razon_social': 'Consumidor Final',
            'tipo_persona': 'NATURAL',
            'tipo_documento': 'CC',
            'es_cliente': True,
            'regimen': 'NO_RESPONSABLE_IVA',
        }
    )

    productos = Producto.objects.filter(empresa=empresa, activo=True).select_related('categoria').order_by('-es_favorito_pos', 'nombre')
    clientes = Tercero.objects.filter(empresa=empresa, es_cliente=True, activo=True).order_by('razon_social')

    return render(request, 'pos/terminal.html', {
        'turno': turno,
        'productos': productos,
        'clientes': clientes,
        'cliente_por_defecto': consumidor_final,
        'tiene_favoritos': productos.filter(es_favorito_pos=True).exists(),
    })


@login_required
@requiere_facturacion
def pos_procesar_venta(request):
    empresa = request.empresa_activa
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    turno = CajaTurno.objects.filter(empresa=empresa, usuario=request.user, estado='ABIERTA').first()
    if not turno:
        return JsonResponse({'error': 'No hay turno abierto'}, status=400)

    if not empresa.puede_emitir_documento():
        return JsonResponse({
            'error': f'Alcanzaste el límite de documentos mensuales de tu plan ({empresa.plan.limite_facturas_mes}/mes).'
        }, status=403)

    try:
        data = json.loads(request.body)
        tercero_id = data.get('tercero_id')
        metodo_pago = data.get('metodo_pago', 'EFECTIVO')
        tipo_documento = data.get('tipo_documento', 'POS_ELECTRONICO')
        if tipo_documento not in ('POS_ELECTRONICO', 'FACTURA'):
            tipo_documento = 'POS_ELECTRONICO'
        items = data.get('items', [])

        if not items:
            return JsonResponse({'error': 'El carrito está vacío'}, status=400)

        tercero = Tercero.objects.filter(id=tercero_id, empresa=empresa).first()
        if not tercero:
            tercero, _ = Tercero.objects.get_or_create(
                empresa=empresa,
                numero_documento='222222222222',
                defaults={'razon_social': 'Consumidor Final', 'es_cliente': True}
            )

        es_fiado = (metodo_pago == 'FIADO')
        if es_fiado and tercero.numero_documento == '222222222222':
            return JsonResponse({
                'error': 'Para anotar un fiado debes seleccionar al cliente/vecino específico, no Consumidor Final.'
            }, status=400)

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
                fecha_emision=date.today(),
                fecha_vencimiento=date.today() if not es_fiado else date.fromordinal(date.today().toordinal() + 15),
                estado='EMITIDA' if es_fiado else 'PAGADA',
                metodo_pago='CREDITO' if es_fiado else 'CONTADO',
                es_fiado=es_fiado,
                observaciones='Fiado registrado en Caja Express.' if es_fiado else 'Venta en Caja Express POS.',
            )

            for it in items:
                prod = Producto.objects.get(id=it['producto_id'], empresa=empresa)
                cant = Decimal(str(it['cantidad']))
                precio_base, iva_pct, otro_pct = desglosar_impuestos_item(prod, it['precio'], empresa)

                FacturaItem.objects.create(
                    factura=factura,
                    producto=prod,
                    descripcion=prod.nombre,
                    cantidad=cant,
                    precio_unitario=precio_base,
                    porcentaje_iva=iva_pct,
                    porcentaje_otro_impuesto=otro_pct,
                )

                if prod.maneja_inventario:
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
                        motivo=f"Venta POS {factura.numero_completo}",
                        referencia_documento=factura.numero_completo,
                    )

            factura.recalcular_totales()
            factura.generar_cufe()
            registrar_asiento_venta(factura, metodo_pago_real=metodo_pago)

            if not es_fiado:
                ultimo_rc = ReciboCaja.objects.filter(empresa=empresa).order_by('-consecutivo').first()
                consec_rc = (ultimo_rc.consecutivo + 1) if ultimo_rc else 1

                ReciboCaja.objects.create(
                    empresa=empresa,
                    consecutivo=consec_rc,
                    factura=factura,
                    tercero=tercero,
                    fecha=date.today(),
                    valor=factura.total,
                    metodo_pago=metodo_pago,
                    referencia_pago=f"POS Turno #{turno.id}",
                    observaciones="Cobro automático en Caja Express",
                )
                factura.recalcular_totales()

                if metodo_pago == 'EFECTIVO':
                    turno.monto_ventas_efectivo += factura.total
                else:
                    turno.monto_ventas_otros += factura.total

                turno.monto_total_esperado = turno.monto_inicial + turno.monto_ventas_efectivo
                turno.save()

        return JsonResponse({
            'success': True,
            'factura_id': factura.id,
            'numero': factura.numero_completo,
            'total': float(factura.total),
            'es_fiado': es_fiado,
            'estado_dian': factura.get_estado_dian_display(),
            'tirilla_url': reverse('facturacion:factura_tirilla', args=[factura.id]),
            'pdf_url': reverse('facturacion:factura_pdf', args=[factura.id]),
        })

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@requiere_facturacion
def cajas_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    cajas = Caja.objects.filter(empresa=empresa)
    turnos_recientes = CajaTurno.objects.filter(empresa=empresa).select_related('caja', 'usuario')[:10]

    return render(request, 'pos/cajas_lista.html', {
        'cajas': cajas,
        'turnos_recientes': turnos_recientes,
    })


@login_required
@requiere_facturacion
def caja_abrir_turno(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    # Verificar si ya tiene turno
    turno_abierto = CajaTurno.objects.filter(empresa=empresa, usuario=request.user, estado='ABIERTA').first()
    if turno_abierto:
        messages.info(request, "Ya tiene un turno de caja abierto actualmente.")
        return redirect('pos:pos_terminal')

    cajas = Caja.objects.filter(empresa=empresa, activa=True)
    if not cajas.exists():
        # Crear caja inicial por defecto
        caja_def = Caja.objects.create(empresa=empresa, nombre="Caja Principal Mostrador", codigo="CAJA-01")
        cajas = [caja_def]

    if request.method == 'POST':
        caja_id = request.POST.get('caja')
        monto_inicial = Decimal(request.POST.get('monto_inicial', '0') or '0')

        caja = get_object_or_404(Caja, id=caja_id, empresa=empresa)
        CajaTurno.objects.create(
            empresa=empresa,
            caja=caja,
            usuario=request.user,
            monto_inicial=monto_inicial,
            monto_total_esperado=monto_inicial,
            estado='ABIERTA',
        )

        messages.success(request, f"Turno abierto exitosamente en {caja.nombre} con base inicial de ${monto_inicial:,.0f} COP.")
        return redirect('pos:pos_terminal')

    return render(request, 'pos/abrir_turno.html', {'cajas': cajas})


@login_required
@requiere_facturacion
def caja_cerrar_turno(request, turno_id):
    empresa = request.empresa_activa
    turno = get_object_or_404(CajaTurno, id=turno_id, empresa=empresa, estado='ABIERTA')

    if request.method == 'POST':
        monto_real = Decimal(request.POST.get('monto_cierre_real', '0') or '0')
        obs = request.POST.get('observaciones', '').strip()

        turno.monto_cierre_real = monto_real
        turno.diferencia = monto_real - turno.monto_total_esperado
        turno.fecha_cierre = datetime.now()
        turno.estado = 'CERRADA'
        turno.observaciones = obs
        turno.save()

        messages.success(request, f"Turno de caja cerrado. Total en caja: ${monto_real:,.0f} COP (Diferencia: ${turno.diferencia:,.0f} COP).")
        return redirect('pos:cajas_lista')

    return render(request, 'pos/cerrar_turno.html', {'turno': turno})
