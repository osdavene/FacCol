from decimal import Decimal
from datetime import date
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import models
from django.db.models import Sum, Count, Q
from django.http import JsonResponse

from apps.empresas.models import Empresa
from apps.facturacion.models import Factura, ReciboCaja
from apps.inventario.models import Producto
from apps.terceros.models import Tercero
from apps.nomina.models import Empleado


@login_required
def dashboard(request):
    empresa = request.empresa_activa
    if not empresa:
        messages.info(request, "Por favor seleccione o cree una empresa para comenzar.")
        if request.user.is_superadmin:
            return redirect('backoffice:empresas_lista')
        return render(request, 'core/sin_empresa.html')

    # Métricas principales
    hoy = date.today()
    primer_dia_mes = hoy.replace(day=1)

    # Ventas del mes
    ventas_mes = Factura.objects.filter(
        empresa=empresa,
        tipo_documento='FACTURA',
        fecha_emision__gte=primer_dia_mes
    ).exclude(estado='ANULADA').aggregate(total=Sum('total'))['total'] or Decimal('0.00')

    # Facturas pendientes / Cartera por cobrar
    cartera_pendiente = Factura.objects.filter(
        empresa=empresa,
        tipo_documento='FACTURA',
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
    )[:5]

    # Últimas 5 facturas
    ultimas_facturas = Factura.objects.filter(
        empresa=empresa,
        tipo_documento='FACTURA'
    ).select_related('tercero').order_by('-fecha_emision', '-consecutivo')[:5]

    context = {
        'ventas_mes': ventas_mes,
        'cartera_pendiente': cartera_pendiente,
        'recaudos_mes': recaudos_mes,
        'total_clientes': total_clientes,
        'productos_stock_bajo': productos_stock_bajo,
        'ultimas_facturas': ultimas_facturas,
    }
    return render(request, 'core/dashboard.html', context)


@login_required
def cambiar_empresa(request, empresa_id):
    if request.user.is_superadmin:
        empresa = get_object_or_404(Empresa, id=empresa_id, activa=True)
    else:
        asignacion = get_object_or_404(
            request.user.empresas_asignadas,
            empresa_id=empresa_id,
            activo=True,
            empresa__activa=True
        )
        empresa = asignacion.empresa

    request.session['empresa_activa_id'] = empresa.id
    messages.success(request, f"Contexto cambiado a: {empresa.display_name}")
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
