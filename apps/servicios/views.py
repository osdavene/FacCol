import json
from datetime import date, timedelta
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.servicios.models import OrdenServicio, ServicioEquipo, EvidenciaServicio
from apps.facturacion.models import Factura, FacturaItem
from apps.contabilidad.services import registrar_asiento_venta
from apps.terceros.models import Tercero
from apps.usuarios.decorators import requiere_servicios


@login_required
@requiere_servicios
def servicios_panel(request):
    if request.user.es_superadmin_user():
        messages.warning(request, "Acceso restringido: El SuperAdmin administra la plataforma SaaS y no gestiona el taller de inquilinos.")
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    ordenes = OrdenServicio.objects.filter(empresa=empresa).select_related('cliente', 'equipo', 'tecnico_asignado')
    total_activas = ordenes.exclude(estado__in=['ENTREGADO', 'CANCELADO']).count()
    listas_entrega = ordenes.filter(estado='LISTO').count()
    en_taller = ordenes.filter(estado__in=['RECIBIDO', 'EN_DIAGNOSTICO', 'EN_REPARACION']).count()

    return render(request, 'servicios/panel.html', {
        'total_activas': total_activas,
        'listas_entrega': listas_entrega,
        'en_taller': en_taller,
        'ordenes_recientes': ordenes[:8],
    })


@login_required
@requiere_servicios
def kanban_view(request):
    if request.user.es_superadmin_user():
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    ordenes = OrdenServicio.objects.filter(empresa=empresa).select_related('cliente', 'equipo')

    columnas = [
        ('RECIBIDO', 'Recibido', 'border-amber-500/50', ordenes.filter(estado='RECIBIDO')),
        ('EN_DIAGNOSTICO', 'En Diagnóstico', 'border-sky-500/50', ordenes.filter(estado='EN_DIAGNOSTICO')),
        ('PRESUPUESTADO', 'Presupuestado', 'border-purple-500/50', ordenes.filter(estado='PRESUPUESTADO')),
        ('EN_REPARACION', 'En Reparación', 'border-blue-500/50', ordenes.filter(estado='EN_REPARACION')),
        ('LISTO', 'Listo para Entrega', 'border-emerald-500/50', ordenes.filter(estado='LISTO')),
        ('ENTREGADO', 'Entregado', 'border-slate-500/50', ordenes.filter(estado='ENTREGADO')),
    ]

    return render(request, 'servicios/kanban.html', {'columnas': columnas})


@login_required
def kanban_cambiar_estado(request):
    if request.user.es_superadmin_user():
        return JsonResponse({'error': 'No autorizado'}, status=403)
    empresa = request.empresa_activa
    if not empresa or not empresa.tiene_modulo('servicios'):
        return JsonResponse({'error': 'Módulo de Servicios no habilitado en tu plan.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST requerido'}, status=405)

    try:
        data = json.loads(request.body)
        orden_id = data.get('orden_id')
        nuevo_estado = data.get('nuevo_estado')

        orden = get_object_or_404(OrdenServicio, id=orden_id, empresa=empresa)
        orden.estado = nuevo_estado
        orden.save(update_fields=['estado'])
        return JsonResponse({'success': True, 'nuevo_estado': nuevo_estado})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@requiere_servicios
def ordenes_lista(request):
    if request.user.es_superadmin_user():
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    ordenes = OrdenServicio.objects.filter(empresa=empresa).select_related('cliente', 'equipo').order_by('-consecutivo')
    return render(request, 'servicios/ordenes_lista.html', {'ordenes': ordenes})


@login_required
@requiere_servicios
def orden_crear(request):
    if request.user.es_superadmin_user():
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    clientes = Tercero.objects.filter(empresa=empresa, es_cliente=True, activo=True)
    equipos = ServicioEquipo.objects.filter(empresa=empresa)

    if request.method == 'POST':
        cliente_id = request.POST.get('cliente')
        equipo_id = request.POST.get('equipo')
        falla = request.POST.get('falla_reportada', '').strip()
        prioridad = request.POST.get('prioridad', 'NORMAL')

        cliente = get_object_or_404(Tercero, id=cliente_id, empresa=empresa)

        # Si el equipo no existe en la lista, registrarlo rápidamente
        if not equipo_id:
            tipo = request.POST.get('tipo_equipo', 'Dispositivo').strip()
            marca = request.POST.get('marca', '').strip()
            modelo = request.POST.get('modelo', '').strip()
            serial = request.POST.get('serial', 'S/N').strip()
            equipo = ServicioEquipo.objects.create(
                empresa=empresa,
                cliente=cliente,
                tipo_equipo=tipo,
                marca=marca,
                modelo=modelo,
                serial=serial,
                accesorios=request.POST.get('accesorios', '').strip()
            )
        else:
            equipo = get_object_or_404(ServicioEquipo, id=equipo_id, empresa=empresa)

        ultima_ord = OrdenServicio.objects.filter(empresa=empresa).order_by('-consecutivo').first()
        consecutivo = (ultima_ord.consecutivo + 1) if ultima_ord else 1

        orden = OrdenServicio.objects.create(
            empresa=empresa,
            consecutivo=consecutivo,
            cliente=cliente,
            equipo=equipo,
            tecnico_asignado=request.user,
            falla_reportada=falla,
            prioridad=prioridad,
            estado='RECIBIDO',
        )

        messages.success(request, f"Orden de Servicio OS-{orden.consecutivo} creada con éxito.")
        return redirect('servicios:orden_detalle', orden_id=orden.id)

    return render(request, 'servicios/orden_formulario.html', {
        'clientes': clientes,
        'equipos': equipos,
    })


@login_required
@requiere_servicios
def orden_detalle(request, orden_id):
    if request.user.es_superadmin_user():
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    orden = get_object_or_404(
        OrdenServicio.objects.select_related('cliente', 'equipo', 'tecnico_asignado', 'factura').prefetch_related('evidencias'),
        id=orden_id,
        empresa=empresa
    )

    if request.method == 'POST':
        # Actualización técnica
        orden.diagnostico_tecnico = request.POST.get('diagnostico_tecnico', orden.diagnostico_tecnico)
        orden.solucion_aplicada = request.POST.get('solucion_aplicada', orden.solucion_aplicada)
        orden.costo_repuestos = Decimal(request.POST.get('costo_repuestos', '0') or '0')
        orden.costo_mano_obra = Decimal(request.POST.get('costo_mano_obra', '0') or '0')
        orden.estado = request.POST.get('estado', orden.estado)

        if 'archivo_evidencia' in request.FILES:
            EvidenciaServicio.objects.create(
                orden=orden,
                tipo=request.POST.get('tipo_evidencia', 'FOTO_PROCESO'),
                archivo=request.FILES['archivo_evidencia'],
                descripcion=request.POST.get('descripcion_evidencia', '')
            )

        orden.save()
        messages.success(request, f"Orden OS-{orden.consecutivo} actualizada.")
        return redirect('servicios:orden_detalle', orden_id=orden.id)

    return render(request, 'servicios/orden_detalle.html', {'orden': orden})


@login_required
@requiere_servicios
@require_POST
def orden_facturar(request, orden_id):
    """Convierte en 1 clic una Orden de Servicio técnico en Factura Electrónica."""
    if request.user.es_superadmin_user():
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    orden = get_object_or_404(
        OrdenServicio.objects.select_related('cliente', 'equipo', 'factura'),
        id=orden_id,
        empresa=empresa,
    )

    if orden.factura:
        messages.info(request, f"La orden OS-{orden.consecutivo} ya fue facturada ({orden.factura.numero_completo}).")
        return redirect('facturacion:factura_detalle', factura_id=orden.factura.id)

    if orden.estado == 'CANCELADO':
        messages.error(request, "No se puede facturar una orden cancelada.")
        return redirect('servicios:orden_detalle', orden_id=orden.id)

    total_orden = (orden.costo_repuestos or Decimal('0.00')) + (orden.costo_mano_obra or Decimal('0.00'))
    if total_orden <= Decimal('0.00'):
        messages.error(request, "Registra primero el costo de repuestos o mano de obra antes de facturar.")
        return redirect('servicios:orden_detalle', orden_id=orden.id)

    if not empresa.puede_emitir_documento():
        messages.error(
            request,
            f"Alcanzaste el límite mensual de documentos electrónicos de tu plan ({empresa.plan.limite_facturas_mes}/mes)."
        )
        return redirect('servicios:orden_detalle', orden_id=orden.id)

    with transaction.atomic():
        dias_venc = empresa.dias_vencimiento or 30
        factura = Factura.objects.create(
            empresa=empresa,
            tipo_documento='FACTURA',
            prefijo=empresa.prefijo_factura,
            consecutivo=empresa.obtener_siguiente_consecutivo(),
            tercero=orden.cliente,
            fecha_emision=date.today(),
            fecha_vencimiento=date.today() + timedelta(days=dias_venc),
            estado='EMITIDA',
            metodo_pago='CONTADO',
            observaciones=(
                f"Facturación de Orden de Servicio OS-{orden.consecutivo} "
                f"({orden.equipo.marca} {orden.equipo.modelo} S/N: {orden.equipo.serial})"
            ),
        )

        if (orden.costo_repuestos or Decimal('0.00')) > Decimal('0.00'):
            FacturaItem.objects.create(
                factura=factura,
                descripcion=f"Repuestos e insumos - OS-{orden.consecutivo} ({orden.equipo.marca} {orden.equipo.modelo})",
                cantidad=Decimal('1.000'),
                precio_unitario=orden.costo_repuestos,
                porcentaje_iva=Decimal('0.00'),
                porcentaje_otro_impuesto=Decimal('0.00'),
            )

        if (orden.costo_mano_obra or Decimal('0.00')) > Decimal('0.00'):
            FacturaItem.objects.create(
                factura=factura,
                descripcion=f"Mano de obra servicio técnico - OS-{orden.consecutivo} ({orden.equipo.marca} {orden.equipo.modelo})",
                cantidad=Decimal('1.000'),
                precio_unitario=orden.costo_mano_obra,
                porcentaje_iva=Decimal('0.00'),
                porcentaje_otro_impuesto=Decimal('0.00'),
            )

        factura.recalcular_totales()
        factura.generar_cufe()
        registrar_asiento_venta(factura)

        orden.factura = factura
        orden.estado = 'ENTREGADO'
        orden.fecha_entrega_real = timezone.now()
        orden.save(update_fields=['factura', 'estado', 'fecha_entrega_real'])

    messages.success(
        request,
        f"Orden OS-{orden.consecutivo} facturada como {factura.numero_completo} por ${factura.total:,.0f} COP."
    )
    return redirect('facturacion:factura_detalle', factura_id=factura.id)
