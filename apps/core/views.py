from decimal import Decimal
from datetime import date
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import models
from django.db.models import Sum, Count, Q
from django.http import JsonResponse

from apps.empresas.models import Empresa
from apps.usuarios.models import User, EmpresaUsuario
from apps.facturacion.models import Factura, ReciboCaja
from apps.inventario.models import Producto, MovimientoInventario
from apps.terceros.models import Tercero
from apps.contabilidad.models import AsientoContable, PlanCuenta
from apps.servicios.models import OrdenServicio
from apps.nomina.models import Empleado


@login_required
def dashboard(request):
    is_superadmin = bool(request.user.is_superadmin or request.user.is_superuser)
    empresa = request.empresa_activa
    rol_actual = request.user.rol_actual or ('superadmin' if is_superadmin else 'usuario')

    # Métricas Globales SaaS para SuperAdmin
    context_sa = {}
    if is_superadmin:
        total_empresas = Empresa.objects.count()
        empresas_activas = Empresa.objects.filter(activa=True).count()
        empresas_inactivas = Empresa.objects.filter(activa=False).count()
        total_usuarios = User.objects.count()
        total_facturas_global = Factura.objects.count()
        ventas_global = Factura.objects.filter(
            tipo_documento='FACTURA'
        ).exclude(estado='ANULADA').aggregate(total=Sum('total'))['total'] or Decimal('0.00')
        empresas_recientes = Empresa.objects.select_related('plan').prefetch_related(
            'usuarios_asignados__user'
        ).order_by('-created_at')[:10]

        context_sa = {
            'is_superadmin': True,
            'total_empresas': total_empresas,
            'empresas_activas': empresas_activas,
            'empresas_inactivas': empresas_inactivas,
            'total_usuarios': total_usuarios,
            'total_facturas_global': total_facturas_global,
            'ventas_global': ventas_global,
            'empresas_recientes': empresas_recientes,
        }

    # Si no hay empresa activa
    if not empresa:
        if is_superadmin:
            # SuperAdmin sin empresa activa seleccionada: Renderiza el hub SuperAdmin unificado
            context = {
                **context_sa,
                'rol_actual': 'superadmin',
                'ventas_mes': Decimal('0.00'),
                'cartera_pendiente': Decimal('0.00'),
                'recaudos_mes': Decimal('0.00'),
                'total_clientes': 0,
                'productos_stock_bajo': [],
                'ultimas_facturas': [],
                'sin_empresa_activa': True,
                'modo_plataforma': True,
            }
            return render(request, 'core/dashboard.html', context)
        return render(request, 'core/sin_empresa.html')

    # Métricas principales de la empresa activa
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
    )[:6]

    # Últimas 6 facturas emitidas
    ultimas_facturas = Factura.objects.filter(
        empresa=empresa,
        tipo_documento='FACTURA'
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
    ordenes_activas = 0
    if is_superadmin or rol_actual in ('propietario', 'admin', 'operador'):
        total_productos = Producto.objects.filter(empresa=empresa, activo=True).count()
        ultimos_movimientos = MovimientoInventario.objects.filter(
            empresa=empresa
        ).select_related('producto').order_by('-created_at')[:5]
        ordenes_activas = OrdenServicio.objects.filter(
            empresa=empresa
        ).exclude(estado__in=['ENTREGADO', 'CANCELADO']).count()

    context = {
        **context_sa,
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
        'ordenes_activas': ordenes_activas,
    }
    return render(request, 'core/dashboard.html', context)


@login_required
def cambiar_empresa(request, empresa_id):
    # Aislamiento Estricto SaaS:
    # Si empresa_id == 0, el SuperAdmin regresa al Modo Plataforma SaaS Global
    if empresa_id == 0 and (request.user.is_superadmin or request.user.is_superuser):
        request.session['empresa_activa_id'] = None
        messages.info(request, "Modo Plataforma SuperAdmin activado.")
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
