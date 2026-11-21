import csv
from decimal import Decimal
from datetime import date
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse

from apps.contabilidad.models import PlanCuenta, AsientoContable, AsientoLinea
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
