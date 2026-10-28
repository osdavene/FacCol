import json
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse

from apps.servicios.models import OrdenServicio, ServicioEquipo, EvidenciaServicio
from apps.terceros.models import Tercero


@login_required
def servicios_panel(request):
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
def kanban_view(request):
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
    empresa = request.empresa_activa
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
def ordenes_lista(request):
    empresa = request.empresa_activa
    ordenes = OrdenServicio.objects.filter(empresa=empresa).select_related('cliente', 'equipo').order_by('-consecutivo')
    return render(request, 'servicios/ordenes_lista.html', {'ordenes': ordenes})


@login_required
def orden_crear(request):
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
def orden_detalle(request, orden_id):
    empresa = request.empresa_activa
    orden = get_object_or_404(
        OrdenServicio.objects.select_related('cliente', 'equipo', 'tecnico_asignado').prefetch_related('evidencias'),
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
