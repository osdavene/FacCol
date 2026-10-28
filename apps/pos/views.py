import json
from decimal import Decimal
from datetime import date, datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db import transaction

from apps.pos.models import Caja, CajaTurno, MovimientoCaja
from apps.inventario.models import Producto, MovimientoInventario
from apps.terceros.models import Tercero
from apps.facturacion.models import Factura, FacturaItem, ReciboCaja


@login_required
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

    productos = Producto.objects.filter(empresa=empresa, activo=True).select_related('categoria')
    clientes = Tercero.objects.filter(empresa=empresa, es_cliente=True, activo=True)
    cliente_por_defecto = clientes.filter(numero_documento='222222222222').first() or clientes.first()

    return render(request, 'pos/terminal.html', {
        'turno': turno,
        'productos': productos,
        'clientes': clientes,
        'cliente_por_defecto': cliente_por_defecto,
    })


@login_required
def pos_procesar_venta(request):
    empresa = request.empresa_activa
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    turno = CajaTurno.objects.filter(empresa=empresa, usuario=request.user, estado='ABIERTA').first()
    if not turno:
        return JsonResponse({'error': 'No hay turno abierto'}, status=400)

    try:
        data = json.loads(request.body)
        tercero_id = data.get('tercero_id')
        metodo_pago = data.get('metodo_pago', 'EFECTIVO')
        items = data.get('items', [])

        if not items:
            return JsonResponse({'error': 'El carrito está vacío'}, status=400)

        tercero = Tercero.objects.filter(id=tercero_id, empresa=empresa).first() or Tercero.objects.filter(empresa=empresa).first()
        consecutivo = empresa.obtener_siguiente_consecutivo()

        with transaction.atomic():
            factura = Factura.objects.create(
                empresa=empresa,
                tipo_documento='FACTURA',
                prefijo=empresa.prefijo_factura,
                consecutivo=consecutivo,
                tercero=tercero,
                fecha_emision=date.today(),
                fecha_vencimiento=date.today(),
                estado='PAGADA',
                metodo_pago='CONTADO',
                observaciones='Venta POS mostrador.',
            )

            for it in items:
                prod = Producto.objects.get(id=it['producto_id'], empresa=empresa)
                cant = Decimal(str(it['cantidad']))
                precio = Decimal(str(it['precio']))

                FacturaItem.objects.create(
                    factura=factura,
                    producto=prod,
                    descripcion=prod.nombre,
                    cantidad=cant,
                    precio_unitario=precio,
                    porcentaje_iva=prod.tarifa_iva,
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

            # Registrar Recibo de Caja automático por pago completo
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
                observaciones="Cobro automático en terminal POS",
            )
            factura.recalcular_totales()

            # Actualizar totales del turno de caja
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
        })

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
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
