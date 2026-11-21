import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse

from apps.inventario.models import (
    Producto,
    Categoria,
    UnidadMedida,
    MovimientoInventario,
    CompraProveedor,
    CompraItem,
)
from apps.contabilidad.services import registrar_asiento_compra
from apps.terceros.models import Tercero
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
            codigo_barras=request.POST.get('codigo_barras', '').strip(),
            nombre=nombre,
            descripcion=request.POST.get('descripcion', '').strip(),
            categoria_id=cat_id if cat_id else None,
            unidad_medida_id=uni_id if uni_id else None,
            precio_venta=precio_venta,
            costo_unitario=costo_unitario,
            tarifa_iva=tarifa_iva,
            impuesto_saludable_pct=Decimal(request.POST.get('impuesto_saludable_pct', '0') or '0'),
            precio_incluye_impuestos='precio_incluye_impuestos' in request.POST,
            es_favorito_pos='es_favorito_pos' in request.POST,
            permite_fraccion='permite_fraccion' in request.POST,
            factor_conversion=Decimal(request.POST.get('factor_conversion', '1') or '1'),
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
@requiere_inventario
def producto_editar(request, producto_id):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    prod = get_object_or_404(Producto, id=producto_id, empresa=empresa)
    categorias = Categoria.objects.filter(empresa=empresa, activa=True)
    unidades = UnidadMedida.objects.filter(empresa=empresa, activa=True)

    if request.method == 'POST':
        prod.codigo = request.POST.get('codigo', prod.codigo).strip()
        prod.codigo_barras = request.POST.get('codigo_barras', '').strip()
        prod.nombre = request.POST.get('nombre', prod.nombre).strip()
        prod.descripcion = request.POST.get('descripcion', '').strip()
        prod.precio_venta = Decimal(request.POST.get('precio_venta', '0') or '0')
        prod.costo_unitario = Decimal(request.POST.get('costo_unitario', '0') or '0')
        prod.tarifa_iva = Decimal(request.POST.get('tarifa_iva', '19') or '19')
        prod.impuesto_saludable_pct = Decimal(request.POST.get('impuesto_saludable_pct', '0') or '0')
        prod.precio_incluye_impuestos = 'precio_incluye_impuestos' in request.POST
        prod.es_favorito_pos = 'es_favorito_pos' in request.POST
        prod.permite_fraccion = 'permite_fraccion' in request.POST
        prod.factor_conversion = Decimal(request.POST.get('factor_conversion', '1') or '1')
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
@requiere_inventario
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
@requiere_inventario
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


@login_required
@requiere_inventario
def ajuste_inventario_crear(request):
    """Ajuste manual de Kardex (merma, rotura, conteo físico o reposición)."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    productos = Producto.objects.filter(empresa=empresa, maneja_inventario=True, activo=True).order_by('nombre')

    if request.method == 'POST':
        prod_id = request.POST.get('producto')
        tipo_mov = request.POST.get('tipo', 'ENTRADA')
        cantidad = Decimal(request.POST.get('cantidad', '0') or '0')
        motivo = request.POST.get('motivo', '').strip() or "Ajuste manual de inventario"

        if cantidad <= 0:
            messages.error(request, "La cantidad del ajuste debe ser mayor a cero.")
            return redirect('inventario:ajuste_crear')

        prod = get_object_or_404(Producto, id=prod_id, empresa=empresa)
        with transaction.atomic():
            saldo_ant = prod.stock_actual
            if tipo_mov == 'SALIDA':
                saldo_nuevo = max(Decimal('0.00'), saldo_ant - cantidad)
            else:
                saldo_nuevo = saldo_ant + cantidad
                tipo_mov = 'ENTRADA'
            prod.stock_actual = saldo_nuevo
            prod.save(update_fields=['stock_actual'])

            MovimientoInventario.objects.create(
                empresa=empresa,
                producto=prod,
                tipo=tipo_mov,
                cantidad=cantidad,
                costo_unitario=prod.costo_unitario,
                saldo_anterior=saldo_ant,
                saldo_nuevo=saldo_nuevo,
                motivo=motivo,
                referencia_documento="AJUSTE-MANUAL",
            )
        messages.success(request, f"Kardex de '{prod.nombre}' ajustado: saldo actual {prod.stock_actual}.")
        return redirect('inventario:kardex')

    return render(request, 'inventario/ajuste_formulario.html', {'productos': productos})


@login_required
@requiere_inventario
def compras_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    compras = CompraProveedor.objects.filter(empresa=empresa).select_related('proveedor')
    return render(request, 'inventario/compras_lista.html', {'compras': compras})


@login_required
@requiere_inventario
def compra_crear(request):
    """
    Registra compra a proveedor o Documento Soporte Electrónico (DSE - Res. 000167),
    convirtiendo pacas/cajas a unidades (factor_conversion) y causando contablemente.
    """
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    proveedores = Tercero.objects.filter(empresa=empresa, activo=True).order_by('-es_proveedor', 'razon_social')
    productos = Producto.objects.filter(empresa=empresa, activo=True).order_by('nombre')

    if request.method == 'POST':
        prov_id = request.POST.get('proveedor')
        proveedor = get_object_or_404(Tercero, id=prov_id, empresa=empresa)
        num_fac_prov = request.POST.get('numero_factura_proveedor', '').strip()
        fecha_compra = request.POST.get('fecha') or date.today()
        metodo_pago = request.POST.get('metodo_pago', 'CONTADO')
        es_dse = 'es_documento_soporte' in request.POST
        observaciones = request.POST.get('observaciones', '').strip()

        items_json = request.POST.get('items_json')
        if items_json:
            items_data = json.loads(items_json)
        else:
            p_ids = request.POST.getlist('item_producto[]')
            cants = request.POST.getlist('item_cantidad[]')
            factores = request.POST.getlist('item_factor[]')
            costos = request.POST.getlist('item_costo[]')
            items_data = []
            for p_id, cant, fac, cost in zip(p_ids, cants, factores, costos):
                if p_id:
                    items_data.append({
                        'producto_id': p_id,
                        'cantidad_empaque': cant,
                        'unidades_por_empaque': fac,
                        'costo_unitario_empaque': cost,
                    })

        if not items_data:
            messages.error(request, "Debes agregar al menos un producto a la compra.")
            return redirect('inventario:compra_crear')

        with transaction.atomic():
            ultima = CompraProveedor.objects.filter(empresa=empresa).order_by('-consecutivo').first()
            consecutivo = (ultima.consecutivo + 1) if ultima else 1
            compra = CompraProveedor.objects.create(
                empresa=empresa,
                consecutivo=consecutivo,
                proveedor=proveedor,
                numero_factura_proveedor=num_fac_prov,
                fecha=fecha_compra,
                metodo_pago=metodo_pago,
                es_documento_soporte=es_dse,
                estado_dian='PENDIENTE' if es_dse else 'NO_APLICA',
                observaciones=observaciones,
            )

            total_compra = Decimal('0.00')
            for it in items_data:
                prod = get_object_or_404(Producto, id=it.get('producto_id'), empresa=empresa)
                cant_emp = Decimal(str(it.get('cantidad_empaque') or '1'))
                un_por_emp = Decimal(str(it.get('unidades_por_empaque') or prod.factor_conversion or '1'))
                costo_emp = Decimal(str(it.get('costo_unitario_empaque') or '0'))

                ci = CompraItem.objects.create(
                    compra=compra,
                    producto=prod,
                    cantidad_empaque=cant_emp,
                    unidades_por_empaque=un_por_emp,
                    costo_unitario_empaque=costo_emp,
                )
                total_compra += ci.subtotal

                if prod.maneja_inventario:
                    saldo_ant = prod.stock_actual
                    saldo_nuevo = saldo_ant + ci.unidades_totales
                    prod.stock_actual = saldo_nuevo
                    if un_por_emp > 0 and costo_emp > 0:
                        prod.costo_unitario = (costo_emp / un_por_emp).quantize(Decimal('0.01'))
                    prod.save(update_fields=['stock_actual', 'costo_unitario'])

                    MovimientoInventario.objects.create(
                        empresa=empresa,
                        producto=prod,
                        tipo='ENTRADA',
                        cantidad=ci.unidades_totales,
                        costo_unitario=prod.costo_unitario,
                        saldo_anterior=saldo_ant,
                        saldo_nuevo=saldo_nuevo,
                        motivo=f"Compra {compra.numero_completo} ({cant_emp} x {un_por_emp})",
                        referencia_documento=compra.numero_completo,
                    )

            compra.total = total_compra
            if es_dse:
                raw_cuds = f"{compra.numero_completo}{compra.fecha}{compra.total}{empresa.nit}{proveedor.numero_documento}"
                compra.cuds = hashlib.sha384(raw_cuds.encode('utf-8')).hexdigest()
                compra.estado_dian = 'ENVIADA' if empresa.dian_software_id else 'CONTINGENCIA'
            compra.save(update_fields=['total', 'cuds', 'estado_dian'])
            registrar_asiento_compra(compra)

        messages.success(request, f"{compra.numero_completo} registrada por ${compra.total:,.0f} COP e inventario actualizado.")
        return redirect('inventario:compra_detalle', compra_id=compra.id)

    return render(request, 'inventario/compra_formulario.html', {
        'proveedores': proveedores,
        'productos': productos,
    })


@login_required
@requiere_inventario
def compra_detalle(request, compra_id):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    compra = get_object_or_404(
        CompraProveedor.objects.select_related('proveedor', 'empresa').prefetch_related('items__producto'),
        id=compra_id,
        empresa=empresa,
    )
    return render(request, 'inventario/compra_detalle.html', {'compra': compra})


@login_required
@requiere_inventario
def compra_xml_dse(request, compra_id):
    """Genera XML UBL de Documento Soporte en Adquisiciones con No Obligados (DIAN Res. 000167)."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    compra = get_object_or_404(
        CompraProveedor.objects.select_related('proveedor', 'empresa').prefetch_related('items__producto'),
        id=compra_id,
        empresa=empresa,
    )
    ns_inv = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    ns_cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
    ET.register_namespace("", ns_inv)
    ET.register_namespace("cbc", ns_cbc)
    root = ET.Element(f"{{{ns_inv}}}Invoice")
    for tag, val in (
        ("UBLVersionID", "UBL 2.1"),
        ("ProfileID", "DIAN 2.1: Documento Soporte en Adquisiciones a No Obligados"),
        ("ID", compra.numero_completo),
        ("UUID", compra.cuds or compra.numero_completo),
        ("IssueDate", compra.fecha.strftime("%Y-%m-%d")),
        ("InvoiceTypeCode", "05"),
        ("DocumentCurrencyCode", "COP"),
    ):
        el = ET.Element(f"{{{ns_cbc}}}{tag}")
        el.text = str(val)
        root.append(el)

    xml_str = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    resp = HttpResponse(xml_str, content_type="application/xml; charset=utf-8")
    resp['Content-Disposition'] = f'attachment; filename="{compra.numero_completo}.xml"'
    return resp
