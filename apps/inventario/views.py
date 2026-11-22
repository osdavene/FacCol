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
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.importers import leer_filas_archivo
from apps.facturacion.dian_service import (
    generar_application_response_radian,
    parsear_attached_document_o_invoice,
    transmitir_dse_dian,
)
from apps.inventario.models import (
    Bodega,
    Categoria,
    CompraItem,
    CompraProveedor,
    ListaPrecio,
    LoteProducto,
    MovimientoInventario,
    OrdenCompra,
    OrdenCompraItem,
    PrecioProducto,
    Producto,
    StockBodega,
    UnidadMedida,
)
from apps.contabilidad.services import registrar_asiento_compra
from apps.core.audit import registrar_actividad
from apps.core.notifications import (
    destinatarios_propietarios_empresa,
    notificar_evento_seguridad,
    notificar_stock_critico_si_aplica,
    notificar_usuarios_app,
)
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

        registrar_actividad(request, 'inventario', 'CREAR_PRODUCTO', f"Creó producto [{prod.codigo}] {prod.nombre}.", empresa=empresa, objeto=prod)
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

        with transaction.atomic():
            prod = get_object_or_404(Producto.objects.select_for_update(), id=prod_id, empresa=empresa)
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
        registrar_actividad(request, 'inventario', 'AJUSTE_KARDEX', f"Ajustó Kardex de '{prod.nombre}' ({tipo_mov} {cantidad}): saldo {prod.stock_actual}.", empresa=empresa, objeto=prod)
        if tipo_mov == 'SALIDA':
            url_kardex = reverse('inventario:kardex')
            resumen_aj = (
                f"Ajuste manual de SALIDA sobre '{prod.nombre}' ({cantidad} uds): "
                f"saldo pasó de {saldo_ant} a {saldo_nuevo}. Motivo: {motivo}"
            )
            notificar_usuarios_app(
                f"Ajuste manual de salida: {prod.nombre}",
                resumen_aj,
                empresa=empresa,
                roles=('propietario', 'admin'),
                nivel='warning',
                categoria='inventario',
                url=url_kardex,
            )
            notificar_evento_seguridad(
                request,
                f"Ajuste de salida en inventario: {prod.nombre}",
                resumen_aj,
                destinatarios=destinatarios_propietarios_empresa(
                    empresa, excluir_emails=[request.user.email]
                ),
                empresa=empresa,
                action_url=request.build_absolute_uri(url_kardex),
                action_text="Revisar Kardex",
                categoria='inventario',
                nivel='warning',
                registrar_in_app=False,
            )
            notificar_stock_critico_si_aplica(empresa, prod, request)
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
                prod = get_object_or_404(Producto.objects.select_for_update(), id=it.get('producto_id'), empresa=empresa)
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
            compra.saldo_pendiente = total_compra if metodo_pago == 'CREDITO' else Decimal('0.00')
            if es_dse:
                raw_cuds = f"{compra.numero_completo}{compra.fecha}{compra.total}{empresa.nit}{proveedor.numero_documento}"
                compra.cuds = hashlib.sha384(raw_cuds.encode('utf-8')).hexdigest()
                compra.estado_dian = 'ENVIADA' if empresa.dian_software_id else 'CONTINGENCIA'
            compra.save(update_fields=['total', 'saldo_pendiente', 'cuds', 'estado_dian'])
            registrar_asiento_compra(compra)

        registrar_actividad(request, 'inventario', 'COMPRA_PROVEEDOR', f"Registró compra {compra.numero_completo} por ${compra.total:,.0f} COP.", empresa=empresa, objeto=compra)
        if es_dse:
            url_comp = reverse('inventario:compra_detalle', args=[compra.id])
            resumen_dse = (
                f"Se registró Documento Soporte Electrónico {compra.numero_completo} al proveedor "
                f"{proveedor.razon_social} por ${compra.total:,.0f} COP (estado DIAN: {compra.estado_dian})."
            )
            notificar_usuarios_app(
                f"Documento Soporte registrado: {compra.numero_completo}",
                resumen_dse,
                empresa=empresa,
                roles=('propietario', 'admin', 'contador'),
                nivel='warning' if compra.estado_dian == 'CONTINGENCIA' else 'info',
                categoria='dian',
                url=url_comp,
            )
            notificar_evento_seguridad(
                request,
                f"Documento Soporte Electrónico {compra.numero_completo}",
                resumen_dse,
                destinatarios=destinatarios_propietarios_empresa(
                    empresa, incluir_roles=('propietario', 'contador')
                ),
                empresa=empresa,
                action_url=request.build_absolute_uri(url_comp),
                action_text="Ver Documento Soporte",
                categoria='dian',
                nivel='warning' if compra.estado_dian == 'CONTINGENCIA' else 'info',
                registrar_in_app=False,
            )
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


@login_required
@requiere_inventario
@require_POST
def compra_transmitir_dse(request, compra_id):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    compra = get_object_or_404(CompraProveedor, id=compra_id, empresa=empresa)
    ok, msg = transmitir_dse_dian(compra)
    if ok:
        registrar_actividad(request, 'inventario', 'TRANSMITIR_DSE', msg, empresa=empresa, objeto=compra)
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect('inventario:compra_detalle', compra_id=compra.id)


@login_required
@requiere_inventario
def productos_importar(request):
    """Importa catálogo de productos y stock inicial desde Excel (.xlsx) o CSV."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    if request.method == 'POST' and request.FILES.get('archivo'):
        try:
            _headers, filas = leer_filas_archivo(request.FILES['archivo'])
            creados = 0
            actualizados = 0
            for fila in filas:
                cod = (fila.get('codigo') or fila.get('sku') or '').strip()
                nom = (fila.get('nombre') or fila.get('producto') or '').strip()
                if not cod or not nom:
                    continue
                cat_nom = (fila.get('categoria') or '').strip()
                cat_obj = Categoria.objects.get_or_create(empresa=empresa, nombre=cat_nom[:100])[0] if cat_nom else None
                precio = Decimal(fila.get('precio_venta') or fila.get('precio') or '0')
                costo = Decimal(fila.get('costo_unitario') or fila.get('costo') or '0')
                iva = Decimal(fila.get('tarifa_iva') or fila.get('iva') or '19')
                stock = Decimal(fila.get('stock_actual') or fila.get('stock') or '0')
                stock_min = Decimal(fila.get('stock_minimo') or '0')
                defaults = {
                    'nombre': nom[:200],
                    'categoria': cat_obj,
                    'precio_venta': precio,
                    'costo_unitario': costo,
                    'tarifa_iva': iva,
                    'stock_actual': stock,
                    'stock_minimo': stock_min,
                    'codigo_barras': (fila.get('codigo_barras') or fila.get('ean') or '')[:60],
                    'activo': True,
                }
                _p, created = Producto.objects.update_or_create(
                    empresa=empresa, codigo=cod[:50], defaults=defaults
                )
                if created:
                    creados += 1
                else:
                    actualizados += 1
            registrar_actividad(request, 'inventario', 'IMPORTAR_PRODUCTOS', f"Importó productos: {creados} creados, {actualizados} actualizados.", empresa=empresa)
            messages.success(request, f"Catálogo importado: {creados} productos creados y {actualizados} actualizados.")
            return redirect('inventario:productos_lista')
        except Exception as exc:
            messages.error(request, f"Error importando catálogo: {exc}")

    return render(request, 'inventario/productos_importar.html')


@login_required
@requiere_inventario
def bodegas_lista(request):
    """Gestiona múltiples bodegas/sucursales de inventario y existencias por bodega."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    if request.method == 'POST':
        codigo = (request.POST.get('codigo') or '').strip().upper()
        nombre = (request.POST.get('nombre') or '').strip()
        if not codigo or not nombre:
            messages.error(request, "Código y nombre de bodega son obligatorios.")
            return redirect('inventario:bodegas_lista')
        Bodega.objects.update_or_create(
            empresa=empresa,
            codigo=codigo[:20],
            defaults={'nombre': nombre[:120], 'activa': True},
        )
        registrar_actividad(request, 'inventario', 'CREAR_BODEGA', f"Configuró bodega {codigo} ({nombre}).", empresa=empresa)
        messages.success(request, f"Bodega '{nombre}' guardada correctamente.")
        return redirect('inventario:bodegas_lista')

    bodegas = Bodega.objects.filter(empresa=empresa, activa=True).order_by('-es_principal', 'nombre')
    productos = Producto.objects.filter(empresa=empresa, maneja_inventario=True, activo=True).order_by('nombre')
    existencias = StockBodega.objects.filter(bodega__empresa=empresa).select_related('bodega', 'producto').order_by('bodega__nombre', 'producto__nombre')
    return render(request, 'inventario/bodegas_lista.html', {
        'bodegas': bodegas,
        'productos': productos,
        'existencias': existencias,
    })


@login_required
@requiere_inventario
@require_POST
def bodega_traslado(request):
    """Traslada existencias de un producto entre dos bodegas de la misma empresa."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    orig = get_object_or_404(Bodega, id=request.POST.get('bodega_origen'), empresa=empresa)
    dest = get_object_or_404(Bodega, id=request.POST.get('bodega_destino'), empresa=empresa)
    prod = get_object_or_404(Producto, id=request.POST.get('producto'), empresa=empresa)
    cant = Decimal(request.POST.get('cantidad') or '0')

    if orig.id == dest.id or cant <= Decimal('0.00'):
        messages.error(request, "Selecciona dos bodegas diferentes y una cantidad mayor a cero.")
        return redirect('inventario:bodegas_lista')

    with transaction.atomic():
        st_orig, _ = StockBodega.objects.select_for_update().get_or_create(bodega=orig, producto=prod, defaults={'cantidad': prod.stock_actual})
        if st_orig.cantidad < cant:
            messages.error(request, f"Saldo insuficiente en {orig.nombre} ({st_orig.cantidad} uds).")
            return redirect('inventario:bodegas_lista')
        st_dest, _ = StockBodega.objects.select_for_update().get_or_create(bodega=dest, producto=prod, defaults={'cantidad': Decimal('0.00')})
        st_orig.cantidad -= cant
        st_dest.cantidad += cant
        st_orig.save(update_fields=['cantidad'])
        st_dest.save(update_fields=['cantidad'])

    registrar_actividad(
        request, 'inventario', 'TRASLADO_BODEGA',
        f"Trasladó {cant} uds de '{prod.nombre}' desde {orig.nombre} hacia {dest.nombre}.",
        empresa=empresa, objeto=prod,
    )
    messages.success(request, f"Traslado completado: {cant} uds de '{prod.nombre}' hacia {dest.nombre}.")
    return redirect('inventario:bodegas_lista')


@login_required
@requiere_inventario
def ordenes_compra_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    if request.method == 'POST':
        prov = get_object_or_404(Tercero, id=request.POST.get('proveedor'), empresa=empresa)
        prod = get_object_or_404(Producto, id=request.POST.get('producto'), empresa=empresa)
        cant = Decimal(request.POST.get('cantidad') or '1')
        costo = Decimal(request.POST.get('costo_unitario') or prod.costo_unitario or '0')
        with transaction.atomic():
            ult = OrdenCompra.objects.filter(empresa=empresa).order_by('-consecutivo').first()
            cons = (ult.consecutivo + 1) if ult else 1
            oc = OrdenCompra.objects.create(
                empresa=empresa,
                consecutivo=cons,
                proveedor=prov,
                fecha=request.POST.get('fecha') or date.today(),
                observaciones=request.POST.get('observaciones', '').strip(),
                total=(cant * costo).quantize(Decimal('0.01')),
            )
            OrdenCompraItem.objects.create(orden=oc, producto=prod, cantidad=cant, costo_unitario=costo)
        messages.success(request, f"Orden de Compra {oc.numero_completo} creada.")
        return redirect('inventario:ordenes_compra_lista')

    ordenes = OrdenCompra.objects.filter(empresa=empresa).select_related('proveedor', 'compra_generada')
    proveedores = Tercero.objects.filter(empresa=empresa, activo=True).order_by('-es_proveedor', 'razon_social')
    productos = Producto.objects.filter(empresa=empresa, activo=True).order_by('nombre')
    return render(request, 'inventario/ordenes_compra_lista.html', {
        'ordenes': ordenes,
        'proveedores': proveedores,
        'productos': productos,
    })


@login_required
@requiere_inventario
@require_POST
def orden_compra_convertir(request, orden_id):
    """Convierte una Orden de Compra aprobada en CompraProveedor real con ingreso al Kardex y asiento."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    orden = get_object_or_404(OrdenCompra, id=orden_id, empresa=empresa)
    if orden.compra_generada:
        messages.warning(request, "Esta orden ya fue recibida y convertida en compra.")
        return redirect('inventario:compra_detalle', compra_id=orden.compra_generada_id)

    with transaction.atomic():
        ult = CompraProveedor.objects.filter(empresa=empresa).order_by('-consecutivo').first()
        cons = (ult.consecutivo + 1) if ult else 1
        compra = CompraProveedor.objects.create(
            empresa=empresa,
            consecutivo=cons,
            proveedor=orden.proveedor,
            numero_factura_proveedor=orden.numero_completo,
            fecha=date.today(),
            metodo_pago='CREDITO',
            total=orden.total,
            saldo_pendiente=orden.total,
            observaciones=f"Generada desde {orden.numero_completo}",
        )
        for it in orden.items.select_related('producto'):
            prod = Producto.objects.select_for_update().get(pk=it.producto_id)
            CompraItem.objects.create(
                compra=compra,
                producto=prod,
                cantidad_empaque=it.cantidad,
                unidades_por_empaque=Decimal('1.000'),
                costo_unitario_empaque=it.costo_unitario,
            )
            if prod.maneja_inventario:
                s_ant = prod.stock_actual
                prod.stock_actual = s_ant + it.cantidad
                if it.costo_unitario > 0:
                    prod.costo_unitario = it.costo_unitario
                prod.save(update_fields=['stock_actual', 'costo_unitario'])
                MovimientoInventario.objects.create(
                    empresa=empresa, producto=prod, tipo='ENTRADA', cantidad=it.cantidad,
                    costo_unitario=prod.costo_unitario, saldo_anterior=s_ant, saldo_nuevo=prod.stock_actual,
                    motivo=f"Recepción {orden.numero_completo}", referencia_documento=compra.numero_completo,
                )
        orden.estado = 'RECIBIDA'
        orden.compra_generada = compra
        orden.save(update_fields=['estado', 'compra_generada'])
        registrar_asiento_compra(compra)

    messages.success(request, f"Orden {orden.numero_completo} convertida en compra {compra.numero_completo}.")
    return redirect('inventario:compra_detalle', compra_id=compra.id)


@login_required
@requiere_inventario
def lotes_lista(request):
    from datetime import timedelta
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    if request.method == 'POST':
        prod = get_object_or_404(Producto, id=request.POST.get('producto'), empresa=empresa)
        bodega_id = request.POST.get('bodega')
        bodega = Bodega.objects.filter(id=bodega_id, empresa=empresa).first() if bodega_id else None
        LoteProducto.objects.update_or_create(
            empresa=empresa,
            producto=prod,
            codigo_lote=(request.POST.get('codigo_lote') or '').strip().upper(),
            defaults={
                'bodega': bodega,
                'fecha_vencimiento': request.POST.get('fecha_vencimiento') or date.today(),
                'cantidad': Decimal(request.POST.get('cantidad') or '0'),
            },
        )
        messages.success(request, "Lote registrado correctamente.")
        return redirect('inventario:lotes_lista')

    limite_alerta = date.today() + timedelta(days=30)
    lotes = LoteProducto.objects.filter(empresa=empresa).select_related('producto', 'bodega')
    return render(request, 'inventario/lotes_lista.html', {
        'lotes': lotes,
        'productos': Producto.objects.filter(empresa=empresa, activo=True),
        'bodegas': Bodega.objects.filter(empresa=empresa, activa=True),
        'limite_alerta': limite_alerta,
    })


@login_required
@requiere_inventario
def listas_precios_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    if request.method == 'POST':
        accion = request.POST.get('accion', 'crear_lista')
        if accion == 'asignar_precio':
            lista = get_object_or_404(ListaPrecio, id=request.POST.get('lista_id'), empresa=empresa)
            prod = get_object_or_404(Producto, id=request.POST.get('producto_id'), empresa=empresa)
            PrecioProducto.objects.update_or_create(
                lista=lista,
                producto=prod,
                defaults={'precio': Decimal(request.POST.get('precio') or '0')},
            )
            messages.success(request, f"Precio asignado en '{lista.nombre}' para {prod.nombre}.")
        else:
            nom = (request.POST.get('nombre') or '').strip()
            if nom:
                ListaPrecio.objects.get_or_create(empresa=empresa, nombre=nom[:100])
                messages.success(request, f"Lista de precios '{nom}' creada.")
        return redirect('inventario:listas_precios_lista')

    return render(request, 'inventario/listas_precios_lista.html', {
        'listas': ListaPrecio.objects.filter(empresa=empresa, activa=True).prefetch_related('precios__producto'),
        'productos': Producto.objects.filter(empresa=empresa, activo=True),
    })


@login_required
@requiere_inventario
def compra_importar_xml(request):
    """Recepción de Factura Electrónica XML / ZIP (AttachedDocument) de proveedor."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    if request.method == 'POST' and request.FILES.get('archivo_xml'):
        try:
            datos = parsear_attached_document_o_invoice(request.FILES['archivo_xml'].read())
            with transaction.atomic():
                doc_nit = datos['nit_proveedor'] or '222222222222'
                prov, _ = Tercero.objects.get_or_create(
                    empresa=empresa,
                    numero_documento=doc_nit,
                    defaults={
                        'tipo_documento': 'NIT',
                        'tipo_persona': 'JURIDICA',
                        'razon_social': datos['nombre_proveedor'],
                        'es_cliente': False,
                        'es_proveedor': True,
                    },
                )
                ult = CompraProveedor.objects.filter(empresa=empresa).order_by('-consecutivo').first()
                cons = (ult.consecutivo + 1) if ult else 1
                metodo = 'CREDITO' if request.POST.get('metodo_pago', 'CREDITO') == 'CREDITO' else 'CONTADO'
                compra = CompraProveedor.objects.create(
                    empresa=empresa,
                    consecutivo=cons,
                    proveedor=prov,
                    numero_factura_proveedor=datos['numero_factura'],
                    fecha=datos['fecha'] or date.today(),
                    metodo_pago=metodo,
                    total=datos['total'],
                    saldo_pendiente=datos['total'] if metodo == 'CREDITO' else Decimal('0.00'),
                    cufe_proveedor=datos['cufe'],
                    xml_recibido=datos['xml_raw'][:65000],
                    observaciones=f"Importada desde XML UBL 2.1 ({len(datos['lineas'])} líneas).",
                )
                registrar_asiento_compra(compra)
            registrar_actividad(request, 'inventario', 'IMPORTAR_COMPRA_XML', f"Recibió factura XML {datos['numero_factura']} de {prov.razon_social}.", empresa=empresa, objeto=compra)
            messages.success(request, f"Factura XML {datos['numero_factura']} de {prov.razon_social} recibida (${datos['total']:,.0f}).")
            return redirect('inventario:compra_detalle', compra_id=compra.id)
        except Exception as exc:
            messages.error(request, f"Error leyendo XML/ZIP de proveedor: {exc}")
    return render(request, 'inventario/compra_importar_xml.html')


@login_required
@requiere_inventario
@require_POST
def compra_evento_radian(request, compra_id):
    """Emite evento RADIAN (030 Acuse, 031 Reclamo, 032 Recibo Bien, 033 Aceptación) sobre compra recibida."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    compra = get_object_or_404(CompraProveedor, id=compra_id, empresa=empresa)
    codigo = request.POST.get('codigo_evento', '').strip()
    desc_map = {
        '030': 'Acuse de recibo',
        '031': 'Reclamo factura',
        '032': 'Recibo bien/servicio',
        '033': 'Aceptación expresa',
    }
    if codigo not in desc_map:
        messages.error(request, "Evento RADIAN no válido.")
        return redirect('inventario:compra_detalle', compra_id=compra.id)
    xml_ar = generar_application_response_radian(compra, codigo, empresa)
    eventos = list(compra.radian_eventos or [])
    eventos.append({
        'codigo': codigo,
        'descripcion': desc_map[codigo],
        'fecha': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'xml_len': len(xml_ar),
    })
    compra.radian_eventos = eventos
    compra.radian_estado = f"{codigo} - {desc_map[codigo]}"[:120]
    compra.save(update_fields=['radian_eventos', 'radian_estado'])
    registrar_actividad(request, 'inventario', 'RADIAN_COMPRA', f"Evento RADIAN {codigo} en compra {compra.numero_completo}.", empresa=empresa, objeto=compra)
    messages.success(request, f"Evento RADIAN {codigo} ({desc_map[codigo]}) registrado con XML ApplicationResponse.")
    return redirect('inventario:compra_detalle', compra_id=compra.id)
