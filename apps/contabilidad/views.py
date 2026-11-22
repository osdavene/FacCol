import csv
from decimal import Decimal
from datetime import date, datetime
from io import BytesIO
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.views.decorators.http import require_POST
import openpyxl
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from apps.contabilidad.models import (
    AsientoContable,
    AsientoLinea,
    ComprobanteEgreso,
    GastoOperativo,
    PeriodoContableCerrado,
    PlanCuenta,
)
from apps.contabilidad.services import (
    ejecutar_cierre_anual_resultados,
    periodo_esta_cerrado,
    registrar_asiento_egreso,
    registrar_asiento_gasto,
)
from apps.core.audit import registrar_actividad
from apps.core.importers import leer_filas_archivo
from apps.inventario.models import CompraProveedor
from apps.terceros.models import Tercero
from apps.usuarios.decorators import requiere_contador


@login_required
@requiere_contador
def asientos_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    asientos = AsientoContable.objects.filter(empresa=empresa).prefetch_related('lineas__cuenta').order_by('-fecha', '-numero_comprobante')[:50]
    return render(request, 'contabilidad/asientos_lista.html', {'asientos': asientos})


@login_required
@requiere_contador
def asiento_crear(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    cuentas = PlanCuenta.objects.filter(empresa=empresa, permite_movimiento=True, activa=True)
    terceros = Tercero.objects.filter(empresa=empresa, activo=True)

    if request.method == 'POST':
        numero = request.POST.get('numero_comprobante', '').strip()
        fecha = request.POST.get('fecha') or date.today()
        tipo = request.POST.get('tipo', 'GENERAL')
        concepto = request.POST.get('concepto', '').strip()
        ref = request.POST.get('referencia_origen', '').strip()

        cuentas_ids = request.POST.getlist('cuenta[]')
        terceros_ids = request.POST.getlist('tercero[]')
        debitos = request.POST.getlist('debito[]')
        creditos = request.POST.getlist('credito[]')
        descripciones = request.POST.getlist('descripcion[]')

        lineas_validas = []
        total_deb = Decimal('0.00')
        total_cred = Decimal('0.00')
        for c_id, t_id, deb, cred, desc in zip(cuentas_ids, terceros_ids, debitos, creditos, descripciones):
            if not c_id:
                continue
            deb_val = Decimal(deb or '0')
            cred_val = Decimal(cred or '0')
            if deb_val > 0 or cred_val > 0:
                total_deb += deb_val
                total_cred += cred_val
                lineas_validas.append((c_id, t_id, deb_val, cred_val, desc))

        if not numero or not concepto or not lineas_validas:
            messages.error(request, "Debe indicar número de comprobante, concepto y al menos una línea con valor mayor a cero.")
            return render(request, 'contabilidad/asiento_formulario.html', {'cuentas': cuentas, 'terceros': terceros})

        if periodo_esta_cerrado(empresa, fecha):
            messages.error(request, f"El período contable correspondiente a {fecha} está CERRADO y no admite nuevos asientos.")
            return render(request, 'contabilidad/asiento_formulario.html', {'cuentas': cuentas, 'terceros': terceros})

        if abs(total_deb - total_cred) > Decimal('0.01'):
            messages.error(request, f"El asiento contable no está cuadrado. Débito: ${total_deb:,.2f} vs Crédito: ${total_cred:,.2f}")
            return render(request, 'contabilidad/asiento_formulario.html', {'cuentas': cuentas, 'terceros': terceros})

        with transaction.atomic():
            asiento = AsientoContable.objects.create(
                empresa=empresa,
                numero_comprobante=numero,
                fecha=fecha,
                tipo=tipo,
                concepto=concepto,
                referencia_origen=ref,
            )

            for c_id, t_id, deb_val, cred_val, desc in lineas_validas:
                c_obj = get_object_or_404(PlanCuenta, id=c_id, empresa=empresa)
                t_obj = Tercero.objects.filter(id=t_id, empresa=empresa).first() if t_id else None
                AsientoLinea.objects.create(
                    asiento=asiento,
                    cuenta=c_obj,
                    tercero=t_obj,
                    descripcion=desc,
                    debito=deb_val,
                    credito=cred_val,
                )

        registrar_actividad(request, 'contabilidad', 'CREAR_ASIENTO', f"Registró asiento {asiento.numero_comprobante} ({asiento.concepto[:80]}).", empresa=empresa, objeto=asiento)
        messages.success(request, f"Asiento contable {asiento.numero_comprobante} registrado con éxito.")
        return redirect('contabilidad:asientos_lista')

    return render(request, 'contabilidad/asiento_formulario.html', {'cuentas': cuentas, 'terceros': terceros})


@login_required
@requiere_contador
def plan_cuentas_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    busqueda = request.GET.get('q', '').strip()
    cuentas = PlanCuenta.objects.filter(empresa=empresa, activa=True).order_by('codigo')

    if busqueda:
        cuentas = cuentas.filter(codigo__icontains=busqueda) | cuentas.filter(nombre__icontains=busqueda)

    return render(request, 'contabilidad/plan_cuentas.html', {'cuentas': cuentas, 'busqueda': busqueda})


@login_required
@requiere_contador
def estados_financieros_view(request):
    """
    Balance de Prueba, Estado de Resultados (P&G) y Balance General NIIF PyMEs,
    con exportación directa a CSV para Excel.
    """
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    desde = request.GET.get('desde', '').strip()
    hasta = request.GET.get('hasta', '').strip()

    lineas = AsientoLinea.objects.filter(asiento__empresa=empresa)
    if desde:
        lineas = lineas.filter(asiento__fecha__gte=desde)
    if hasta:
        lineas = lineas.filter(asiento__fecha__lte=hasta)

    agregados = (
        lineas.values('cuenta__codigo', 'cuenta__nombre', 'cuenta__naturaleza')
        .annotate(total_deb=Sum('debito'), total_cred=Sum('credito'))
        .order_by('cuenta__codigo')
    )

    cuentas_balance = []
    totales = {
        'debito': Decimal('0.00'),
        'credito': Decimal('0.00'),
        'activos': Decimal('0.00'),
        'pasivos': Decimal('0.00'),
        'patrimonio': Decimal('0.00'),
        'ingresos': Decimal('0.00'),
        'gastos': Decimal('0.00'),
        'costos': Decimal('0.00'),
    }

    for row in agregados:
        cod = row['cuenta__codigo']
        deb = row['total_deb'] or Decimal('0.00')
        cred = row['total_cred'] or Decimal('0.00')
        totales['debito'] += deb
        totales['credito'] += cred

        clase = cod[0] if cod else ''
        if clase in ('1', '5', '6', '7'):
            saldo = deb - cred
        else:
            saldo = cred - deb

        if clase == '1':
            totales['activos'] += saldo
        elif clase == '2':
            totales['pasivos'] += saldo
        elif clase == '3':
            totales['patrimonio'] += saldo
        elif clase == '4':
            totales['ingresos'] += saldo
        elif clase == '5':
            totales['gastos'] += saldo
        elif clase in ('6', '7'):
            totales['costos'] += saldo

        cuentas_balance.append({
            'codigo': cod,
            'nombre': row['cuenta__nombre'],
            'debito': deb,
            'credito': cred,
            'saldo': saldo,
            'clase': clase,
        })

    utilidad = totales['ingresos'] - totales['gastos'] - totales['costos']
    totales['utilidad'] = utilidad
    totales['pasivo_mas_patrimonio'] = totales['pasivos'] + totales['patrimonio'] + utilidad

    if request.GET.get('export') == 'xlsx':
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Estados Financieros"
        ws.append([f"{empresa.razon_social} — NIT {empresa.nit_completo}"])
        ws.append(["Balance de Prueba y Estados Financieros"])
        ws.append([])
        ws.append(['Código PUC', 'Cuenta', 'Débitos', 'Créditos', 'Saldo'])
        for c in cuentas_balance:
            ws.append([c['codigo'], c['nombre'], float(c['debito']), float(c['credito']), float(c['saldo'])])
        ws.append([])
        ws.append(['RESUMEN', 'Activos', float(totales['activos']), 'Pasivos', float(totales['pasivos'])])
        ws.append(['RESUMEN', 'Ingresos', float(totales['ingresos']), 'Gastos+Costos', float(totales['gastos'] + totales['costos'])])
        ws.append(['RESUMEN', 'Utilidad Neta', float(utilidad), '', ''])
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(
            buf.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp['Content-Disposition'] = f'attachment; filename="Estados_Financieros_{empresa.nit}.xlsx"'
        return resp

    if request.GET.get('export') == 'csv':
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="Balance_FacCol_{empresa.nit}.csv"'
        resp.write('\ufeff')
        writer = csv.writer(resp)
        writer.writerow(['Código PUC', 'Cuenta', 'Débitos', 'Créditos', 'Saldo'])
        for c in cuentas_balance:
            writer.writerow([c['codigo'], c['nombre'], f"{c['debito']:.2f}", f"{c['credito']:.2f}", f"{c['saldo']:.2f}"])
        writer.writerow([])
        writer.writerow(['RESUMEN', 'Activos', f"{totales['activos']:.2f}", 'Pasivos', f"{totales['pasivos']:.2f}"])
        writer.writerow(['RESUMEN', 'Ingresos', f"{totales['ingresos']:.2f}", 'Gastos+Costos', f"{(totales['gastos'] + totales['costos']):.2f}"])
        writer.writerow(['RESUMEN', 'Utilidad Neta', f"{utilidad:.2f}", '', ''])
        return resp

    return render(request, 'contabilidad/estados_financieros.html', {
        'cuentas_balance': cuentas_balance,
        'totales': totales,
        'desde': desde,
        'hasta': hasta,
    })


@login_required
@requiere_contador
def auxiliar_terceros_view(request):
    """Libro Auxiliar Contable filtrado por Tercero (Cliente / Proveedor / Empleado)."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    terceros = Tercero.objects.filter(empresa=empresa, activo=True).order_by('razon_social')
    tercero_id = request.GET.get('tercero')
    desde = request.GET.get('desde', '').strip()
    hasta = request.GET.get('hasta', '').strip()

    tercero_sel = Tercero.objects.filter(id=tercero_id, empresa=empresa).first() if tercero_id else None
    movimientos = AsientoLinea.objects.filter(asiento__empresa=empresa).select_related('asiento', 'cuenta', 'tercero')
    if tercero_sel:
        movimientos = movimientos.filter(tercero=tercero_sel)
    if desde:
        movimientos = movimientos.filter(asiento__fecha__gte=desde)
    if hasta:
        movimientos = movimientos.filter(asiento__fecha__lte=hasta)

    movimientos = movimientos.order_by('asiento__fecha', 'asiento_id', 'id')[:100]
    total_deb = sum((m.debito for m in movimientos), Decimal('0.00'))
    total_cred = sum((m.credito for m in movimientos), Decimal('0.00'))

    return render(request, 'contabilidad/auxiliar_terceros.html', {
        'terceros': terceros,
        'tercero_sel': tercero_sel,
        'movimientos': movimientos,
        'total_deb': total_deb,
        'total_cred': total_cred,
        'saldo_neto': total_deb - total_cred,
        'desde': desde,
        'hasta': hasta,
    })


@login_required
@requiere_contador
def egresos_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    egresos = ComprobanteEgreso.objects.filter(empresa=empresa).select_related('tercero', 'compra').order_by('-fecha', '-consecutivo')[:80]
    compras_pendientes = CompraProveedor.objects.filter(
        empresa=empresa, saldo_pendiente__gt=Decimal('0.00')
    ).select_related('proveedor').order_by('fecha', 'consecutivo')
    return render(request, 'contabilidad/egresos_lista.html', {
        'egresos': egresos,
        'compras_pendientes': compras_pendientes,
    })


@login_required
@requiere_contador
def egreso_crear(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    compra_sel = CompraProveedor.objects.filter(id=request.GET.get('compra'), empresa=empresa).first() if request.GET.get('compra') else None
    compras_pendientes = CompraProveedor.objects.filter(empresa=empresa, saldo_pendiente__gt=Decimal('0.00')).select_related('proveedor')
    proveedores = Tercero.objects.filter(empresa=empresa, activo=True).order_by('-es_proveedor', 'razon_social')

    if request.method == 'POST':
        c_id = request.POST.get('compra')
        compra = CompraProveedor.objects.filter(id=c_id, empresa=empresa).first() if c_id else None
        tercero = compra.proveedor if compra else get_object_or_404(Tercero, id=request.POST.get('tercero'), empresa=empresa)
        fecha_str = request.POST.get('fecha') or date.today().isoformat()
        fecha_pago = datetime.strptime(fecha_str[:10], '%Y-%m-%d').date()
        valor = Decimal(request.POST.get('valor', '0') or '0')
        metodo = request.POST.get('metodo_pago', 'TRANSFERENCIA')
        ref = request.POST.get('referencia', '').strip()
        obs = request.POST.get('observaciones', '').strip()

        if valor <= Decimal('0.00'):
            messages.error(request, "El valor del comprobante de egreso debe ser mayor a cero.")
            return redirect('contabilidad:egreso_crear')
        if periodo_esta_cerrado(empresa, fecha_pago):
            messages.error(request, f"El período {fecha_pago.strftime('%Y-%m')} está cerrado contablemente.")
            return redirect('contabilidad:egreso_crear')

        with transaction.atomic():
            ultimo = ComprobanteEgreso.objects.filter(empresa=empresa).order_by('-consecutivo').first()
            consec = (ultimo.consecutivo + 1) if ultimo else 1
            egreso = ComprobanteEgreso.objects.create(
                empresa=empresa,
                consecutivo=consec,
                tercero=tercero,
                compra=compra,
                fecha=fecha_pago,
                valor=valor,
                metodo_pago=metodo,
                referencia=ref,
                observaciones=obs,
            )
            if compra:
                compra.saldo_pendiente = max(Decimal('0.00'), compra.saldo_pendiente - valor)
                compra.save(update_fields=['saldo_pendiente'])
            registrar_asiento_egreso(egreso)

        registrar_actividad(request, 'contabilidad', 'COMPROBANTE_EGRESO', f"Registró {egreso.numero_completo} por ${valor:,.0f} COP a {tercero.razon_social}.", empresa=empresa, objeto=egreso)
        messages.success(request, f"Comprobante {egreso.numero_completo} registrado por ${valor:,.0f} COP.")
        return redirect('contabilidad:egresos_lista')

    return render(request, 'contabilidad/egreso_formulario.html', {
        'compra_sel': compra_sel,
        'compras_pendientes': compras_pendientes,
        'proveedores': proveedores,
    })


@login_required
@requiere_contador
def gastos_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    gastos = GastoOperativo.objects.filter(empresa=empresa).select_related('tercero', 'cuenta_gasto').order_by('-fecha', '-consecutivo')[:80]
    return render(request, 'contabilidad/gastos_lista.html', {'gastos': gastos})


@login_required
@requiere_contador
def gasto_crear(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    terceros = Tercero.objects.filter(empresa=empresa, activo=True).order_by('-es_proveedor', 'razon_social')
    cuentas_gasto = PlanCuenta.objects.filter(empresa=empresa, permite_movimiento=True, activa=True, codigo__regex=r'^[56]').order_by('codigo')

    if request.method == 'POST':
        tercero = get_object_or_404(Tercero, id=request.POST.get('tercero'), empresa=empresa)
        cta = get_object_or_404(PlanCuenta, id=request.POST.get('cuenta_gasto'), empresa=empresa)
        fecha_str = request.POST.get('fecha') or date.today().isoformat()
        fecha_g = datetime.strptime(fecha_str[:10], '%Y-%m-%d').date()
        concepto = request.POST.get('concepto', '').strip()
        num_sop = request.POST.get('numero_soporte', '').strip()
        subtotal = Decimal(request.POST.get('subtotal', '0') or '0')
        iva = Decimal(request.POST.get('iva', '0') or '0')
        rf = Decimal(request.POST.get('retencion_fuente', '0') or '0')
        rica = Decimal(request.POST.get('retencion_ica', '0') or '0')
        metodo = request.POST.get('metodo_pago', 'CONTADO')

        if not concepto or subtotal <= 0:
            messages.error(request, "Concepto y subtotal mayor a cero son obligatorios.")
            return redirect('contabilidad:gasto_crear')
        if periodo_esta_cerrado(empresa, fecha_g):
            messages.error(request, f"El período {fecha_g.strftime('%Y-%m')} está cerrado contablemente.")
            return redirect('contabilidad:gasto_crear')

        total_pagar = max(Decimal('0.00'), subtotal + iva - rf - rica)
        with transaction.atomic():
            ultimo = GastoOperativo.objects.filter(empresa=empresa).order_by('-consecutivo').first()
            consec = (ultimo.consecutivo + 1) if ultimo else 1
            gasto = GastoOperativo.objects.create(
                empresa=empresa,
                consecutivo=consec,
                tercero=tercero,
                cuenta_gasto=cta,
                fecha=fecha_g,
                numero_soporte=num_sop,
                concepto=concepto,
                subtotal=subtotal,
                iva=iva,
                retencion_fuente=rf,
                retencion_ica=rica,
                total_pagar=total_pagar,
                metodo_pago=metodo,
            )
            registrar_asiento_gasto(gasto)

        registrar_actividad(request, 'contabilidad', 'REGISTRAR_GASTO', f"Causó gasto {gasto.numero_completo} ({concepto}) por ${total_pagar:,.0f} COP.", empresa=empresa, objeto=gasto)
        messages.success(request, f"Gasto {gasto.numero_completo} contabilizado por ${total_pagar:,.0f} COP.")
        return redirect('contabilidad:gastos_lista')

    return render(request, 'contabilidad/gasto_formulario.html', {
        'terceros': terceros,
        'cuentas_gasto': cuentas_gasto,
    })


@login_required
@requiere_contador
def cierres_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    if request.method == 'POST':
        accion = request.POST.get('accion', 'toggle_mes')
        ano = int(request.POST.get('ano') or date.today().year)
        if accion == 'cierre_anual':
            asiento, msg = ejecutar_cierre_anual_resultados(empresa, ano, request.user)
            if asiento or 'ejecutado' in msg:
                registrar_actividad(request, 'contabilidad', 'CIERRE_ANUAL', msg, empresa=empresa, objeto=asiento or empresa)
                messages.success(request, msg)
            else:
                messages.info(request, msg)
        else:
            mes = int(request.POST.get('mes') or date.today().month)
            obj, created = PeriodoContableCerrado.objects.get_or_create(
                empresa=empresa,
                ano=ano,
                mes=mes,
                defaults={'cerrado': True, 'cerrado_por': request.user},
            )
            if not created:
                obj.cerrado = not obj.cerrado
                obj.cerrado_por = request.user
                obj.save(update_fields=['cerrado', 'cerrado_por'])
            estado = "cerrado" if obj.cerrado else "reabierto"
            registrar_actividad(request, 'contabilidad', 'CIERRE_PERIODO', f"Período {ano}-{mes:02d} {estado}.", empresa=empresa, objeto=obj)
            messages.success(request, f"Período {ano}-{mes:02d} {estado} correctamente.")
        return redirect('contabilidad:cierres_lista')

    cierres = PeriodoContableCerrado.objects.filter(empresa=empresa).select_related('cerrado_por').order_by('-ano', '-mes')
    return render(request, 'contabilidad/cierres_lista.html', {
        'cierres': cierres,
        'ano_actual': date.today().year,
        'mes_actual': date.today().month,
    })


@login_required
@requiere_contador
def certificados_retencion_view(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    ano = int(request.GET.get('ano') or date.today().year)
    terceros = Tercero.objects.filter(empresa=empresa, activo=True).order_by('razon_social')
    tercero_id = request.GET.get('tercero')
    tercero_sel = Tercero.objects.filter(id=tercero_id, empresa=empresa).first() if tercero_id else None

    resumen = None
    if tercero_sel:
        gastos_qs = GastoOperativo.objects.filter(empresa=empresa, tercero=tercero_sel, fecha__year=ano)
        base_g = gastos_qs.aggregate(t=Sum('subtotal'))['t'] or Decimal('0.00')
        rf_g = gastos_qs.aggregate(t=Sum('retencion_fuente'))['t'] or Decimal('0.00')
        rica_g = gastos_qs.aggregate(t=Sum('retencion_ica'))['t'] or Decimal('0.00')

        rf_asiento = AsientoLinea.objects.filter(
            asiento__empresa=empresa, tercero=tercero_sel, asiento__fecha__year=ano, cuenta__codigo__startswith='2365'
        ).aggregate(t=Sum('credito'))['t'] or Decimal('0.00')
        rica_asiento = AsientoLinea.objects.filter(
            asiento__empresa=empresa, tercero=tercero_sel, asiento__fecha__year=ano, cuenta__codigo__startswith='2368'
        ).aggregate(t=Sum('credito'))['t'] or Decimal('0.00')

        resumen = {
            'base': base_g,
            'retefuente': max(rf_g, rf_asiento),
            'reteica': max(rica_g, rica_asiento),
        }

        if request.GET.get('formato') == 'pdf':
            buf = BytesIO()
            c = canvas.Canvas(buf, pagesize=letter)
            c.setFont("Helvetica-Bold", 14)
            c.drawString(60, 740, f"CERTIFICADO DE RETENCIONES TRIBUTARIAS — AÑO GRAVABLE {ano}")
            c.setFont("Helvetica", 10)
            c.drawString(60, 710, f"Agente Retenedor: {empresa.razon_social} (NIT {empresa.nit_completo})")
            c.drawString(60, 692, f"Dirección: {empresa.direccion or 'Colombia'}")
            c.line(60, 680, 550, 680)
            c.drawString(60, 655, f"Sujeto Pasivo (Tercero): {tercero_sel.razon_social}")
            c.drawString(60, 637, f"Identificación: {tercero_sel.documento_completo}")
            c.line(60, 622, 550, 622)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(60, 595, f"Base sujeta a retención acumulada: ${resumen['base']:,.2f} COP")
            c.drawString(60, 575, f"Total Retención en la Fuente practicada: ${resumen['retefuente']:,.2f} COP")
            c.drawString(60, 555, f"Total Retención de ICA practicada: ${resumen['reteica']:,.2f} COP")
            c.setFont("Helvetica", 9)
            c.drawString(60, 500, f"Expedido en sistema FacCol el {date.today().strftime('%d/%m/%Y')}.")
            c.showPage()
            c.save()
            resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
            resp['Content-Disposition'] = f'attachment; filename="Certificado_Retencion_{ano}_{tercero_sel.numero_documento}.pdf"'
            return resp

    return render(request, 'contabilidad/certificados_retencion.html', {
        'terceros': terceros,
        'tercero_sel': tercero_sel,
        'ano': ano,
        'resumen': resumen,
    })


@login_required
@requiere_contador
def exogena_resumen_view(request):
    """Resumen DIAN Medios Magnéticos (Formato 1001 Pagos/Abonos y Formato 1007 Ingresos Recibidos)."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    ano = int(request.GET.get('ano') or date.today().year)
    from apps.facturacion.models import Factura

    ingresos_1007 = list(
        Factura.objects.filter(
            empresa=empresa,
            tipo_documento__in=('FACTURA', 'POS_ELECTRONICO'),
            fecha_emision__year=ano,
        )
        .exclude(estado='ANULADA')
        .values('tercero__numero_documento', 'tercero__razon_social')
        .annotate(total_bruto=Sum('subtotal'), total_iva=Sum('iva'))
        .order_by('-total_bruto')
    )
    pagos_1001 = list(
        CompraProveedor.objects.filter(empresa=empresa, fecha__year=ano)
        .values('proveedor__numero_documento', 'proveedor__razon_social')
        .annotate(total_pago=Sum('total'))
        .order_by('-total_pago')
    )

    if request.GET.get('export') == 'xlsx':
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = f"1007 Ingresos {ano}"
        ws1.append(['Formato 1007 — Ingresos Recibidos', f"Año {ano}", empresa.razon_social])
        ws1.append(['Documento', 'Tercero', 'Ingreso Neto', 'IVA'])
        for r in ingresos_1007:
            ws1.append([r['tercero__numero_documento'], r['tercero__razon_social'], float(r['total_bruto'] or 0), float(r['total_iva'] or 0)])
        ws2 = wb.create_sheet(title=f"1001 Pagos {ano}")
        ws2.append(['Formato 1001 — Pagos y Abonos en Cuenta', f"Año {ano}", empresa.razon_social])
        ws2.append(['Documento', 'Proveedor', 'Valor Pagado/Causado'])
        for r in pagos_1001:
            ws2.append([r['proveedor__numero_documento'], r['proveedor__razon_social'], float(r['total_pago'] or 0)])
        buf = BytesIO()
        wb.save(buf)
        resp = HttpResponse(buf.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="Exogena_DIAN_{ano}_{empresa.nit}.xlsx"'
        return resp

    return render(request, 'contabilidad/exogena_resumen.html', {
        'ano': ano,
        'ingresos_1007': ingresos_1007,
        'pagos_1001': pagos_1001,
    })


@login_required
@requiere_contador
def puc_importar(request):
    """Importa Plan de Cuentas (PUC) y opcionalmente genera asiento de Saldos Iniciales."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    if request.method == 'POST' and request.FILES.get('archivo'):
        try:
            _headers, filas = leer_filas_archivo(request.FILES['archivo'])
            creadas = 0
            lineas_saldo = []
            for fila in filas:
                cod = (fila.get('codigo') or fila.get('cuenta') or '').strip()
                nom = (fila.get('nombre') or '').strip()
                if not cod or not nom:
                    continue
                nat = (fila.get('naturaleza') or ('DEBITO' if cod[0] in ('1', '5', '6', '7') else 'CREDITO')).upper()
                if nat not in ('DEBITO', 'CREDITO'):
                    nat = 'DEBITO'
                cta, created = PlanCuenta.objects.update_or_create(
                    empresa=empresa,
                    codigo=cod[:20],
                    defaults={
                        'nombre': nom[:150],
                        'naturaleza': nat,
                        'nivel': len(cod),
                        'permite_movimiento': len(cod) >= 4,
                        'activa': True,
                    },
                )
                if created:
                    creadas += 1
                deb = Decimal(fila.get('debito') or '0')
                cred = Decimal(fila.get('credito') or '0')
                if deb > 0 or cred > 0:
                    lineas_saldo.append((cta, deb, cred))

            if lineas_saldo:
                tot_d = sum((d for _, d, _ in lineas_saldo), Decimal('0.00'))
                tot_c = sum((c for _, _, c in lineas_saldo), Decimal('0.00'))
                if abs(tot_d - tot_c) < Decimal('0.01'):
                    asiento = AsientoContable.objects.create(
                        empresa=empresa,
                        numero_comprobante=f"SI-{date.today().strftime('%Y%m%d')}",
                        fecha=date.today(),
                        tipo='GENERAL',
                        concepto="Saldos Iniciales importados desde Excel/CSV",
                        referencia_origen="SALDOS-INICIALES",
                    )
                    for cta, d, c in lineas_saldo:
                        AsientoLinea.objects.create(
                            asiento=asiento, cuenta=cta, descripcion="Saldo inicial importado", debito=d, credito=c
                        )
            registrar_actividad(request, 'contabilidad', 'IMPORTAR_PUC', f"Importó PUC: {creadas} cuentas creadas.", empresa=empresa)
            messages.success(request, f"Importación PUC finalizada: {creadas} cuentas nuevas registradas.")
            return redirect('contabilidad:plan_cuentas_lista')
        except Exception as exc:
            messages.error(request, f"Error importando PUC: {exc}")

    return render(request, 'contabilidad/puc_importar.html')
