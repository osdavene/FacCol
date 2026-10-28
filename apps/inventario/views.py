from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q

from apps.inventario.models import Producto, Categoria, UnidadMedida, MovimientoInventario
from apps.usuarios.decorators import requiere_inventario


@login_required
@requiere_inventario
def productos_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    busqueda = request.GET.get('q', '').strip()
    categoria_id = request.GET.get('categoria')

    productos = Producto.objects.filter(empresa=empresa, activo=True).select_related('categoria', 'unidad_medida')

    if categoria_id:
        productos = productos.filter(categoria_id=categoria_id)

    if busqueda:
        productos = productos.filter(
            Q(nombre__icontains=busqueda) |
            Q(codigo__icontains=busqueda) |
            Q(descripcion__icontains=busqueda)
        )

    categorias = Categoria.objects.filter(empresa=empresa, activa=True)

    context = {
        'productos': productos,
        'categorias': categorias,
        'busqueda': busqueda,
        'categoria_sel': categoria_id,
    }
    return render(request, 'inventario/productos_lista.html', context)


@login_required
@requiere_inventario
def producto_crear(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    categorias = Categoria.objects.filter(empresa=empresa, activa=True)
    unidades = UnidadMedida.objects.filter(empresa=empresa, activa=True)

    if request.method == 'POST':
        codigo = request.POST.get('codigo', '').strip()
        nombre = request.POST.get('nombre', '').strip()
        precio_venta = Decimal(request.POST.get('precio_venta', '0') or '0')
        costo_unitario = Decimal(request.POST.get('costo_unitario', '0') or '0')
        tarifa_iva = Decimal(request.POST.get('tarifa_iva', '19') or '19')
        stock_actual = Decimal(request.POST.get('stock_actual', '0') or '0')
        stock_minimo = Decimal(request.POST.get('stock_minimo', '0') or '0')
        maneja_inventario = 'maneja_inventario' in request.POST

        if not codigo or not nombre:
            messages.error(request, "El código y el nombre del producto son obligatorios.")
            return render(request, 'inventario/producto_formulario.html', {
                'categorias': categorias, 'unidades': unidades, 'accion': 'Crear'
            })

        cat_id = request.POST.get('categoria')
        uni_id = request.POST.get('unidad_medida')

        prod = Producto.objects.create(
            empresa=empresa,
            codigo=codigo,
            nombre=nombre,
            descripcion=request.POST.get('descripcion', '').strip(),
            categoria_id=cat_id if cat_id else None,
            unidad_medida_id=uni_id if uni_id else None,
            precio_venta=precio_venta,
            costo_unitario=costo_unitario,
            tarifa_iva=tarifa_iva,
            stock_actual=stock_actual,
            stock_minimo=stock_minimo,
            maneja_inventario=maneja_inventario,
        )

        if 'imagen' in request.FILES:
            prod.imagen = request.FILES['imagen']
            prod.save(update_fields=['imagen'])

        # Si inicia con stock, registrar movimiento inicial
        if stock_actual > 0:
            MovimientoInventario.objects.create(
                empresa=empresa,
                producto=prod,
                tipo='ENTRADA',
                cantidad=stock_actual,
                costo_unitario=costo_unitario,
                saldo_anterior=Decimal('0.00'),
                saldo_nuevo=stock_actual,
                motivo="Saldo inicial de inventario al registrar producto",
            )

        messages.success(request, f"Producto [{prod.codigo}] {prod.nombre} creado exitosamente.")
        return redirect('inventario:productos_lista')

    return render(request, 'inventario/producto_formulario.html', {
        'categorias': categorias,
        'unidades': unidades,
        'accion': 'Crear',
    })


@login_required
def producto_editar(request, producto_id):
    empresa = request.empresa_activa
    prod = get_object_or_404(Producto, id=producto_id, empresa=empresa)
    categorias = Categoria.objects.filter(empresa=empresa, activa=True)
    unidades = UnidadMedida.objects.filter(empresa=empresa, activa=True)

    if request.method == 'POST':
        prod.codigo = request.POST.get('codigo', prod.codigo).strip()
        prod.nombre = request.POST.get('nombre', prod.nombre).strip()
        prod.descripcion = request.POST.get('descripcion', '').strip()
        prod.precio_venta = Decimal(request.POST.get('precio_venta', '0') or '0')
        prod.costo_unitario = Decimal(request.POST.get('costo_unitario', '0') or '0')
        prod.tarifa_iva = Decimal(request.POST.get('tarifa_iva', '19') or '19')
        prod.stock_minimo = Decimal(request.POST.get('stock_minimo', '0') or '0')
        prod.maneja_inventario = 'maneja_inventario' in request.POST

        cat_id = request.POST.get('categoria')
        uni_id = request.POST.get('unidad_medida')
        prod.categoria_id = cat_id if cat_id else None
        prod.unidad_medida_id = uni_id if uni_id else None

        if 'imagen' in request.FILES:
            prod.imagen = request.FILES['imagen']

        prod.save()
        messages.success(request, f"Producto [{prod.codigo}] {prod.nombre} actualizado.")
        return redirect('inventario:productos_lista')

    return render(request, 'inventario/producto_formulario.html', {
        'producto': prod,
        'categorias': categorias,
        'unidades': unidades,
        'accion': 'Editar',
    })


@login_required
@requiere_inventario
def kardex_view(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    producto_id = request.GET.get('producto')
    movimientos = MovimientoInventario.objects.filter(empresa=empresa).select_related('producto')

    if producto_id:
        movimientos = movimientos.filter(producto_id=producto_id)

    productos = Producto.objects.filter(empresa=empresa, activo=True)

    return render(request, 'inventario/kardex.html', {
        'movimientos': movimientos[:50],
        'productos': productos,
        'producto_sel': producto_id,
    })


@login_required
def categorias_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        if nombre:
            Categoria.objects.create(
                empresa=empresa,
                nombre=nombre,
                descripcion=request.POST.get('descripcion', '').strip()
            )
            messages.success(request, f"Categoría '{nombre}' creada.")
            return redirect('inventario:categorias_lista')

    categorias = Categoria.objects.filter(empresa=empresa, activa=True)
    return render(request, 'inventario/categorias.html', {'categorias': categorias})


@login_required
def unidades_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    if request.method == 'POST':
        codigo = request.POST.get('codigo', '').strip().upper()
        nombre = request.POST.get('nombre', '').strip()
        if codigo and nombre:
            UnidadMedida.objects.create(
                empresa=empresa,
                codigo=codigo,
                nombre=nombre
            )
            messages.success(request, f"Unidad '{nombre}' creada.")
            return redirect('inventario:unidades_lista')

    unidades = UnidadMedida.objects.filter(empresa=empresa, activa=True)
    return render(request, 'inventario/unidades.html', {'unidades': unidades})
