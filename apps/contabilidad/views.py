from decimal import Decimal
from datetime import date
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction

from apps.contabilidad.models import PlanCuenta, AsientoContable, AsientoLinea
from apps.terceros.models import Tercero


@login_required
def asientos_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    asientos = AsientoContable.objects.filter(empresa=empresa).prefetch_related('lineas__cuenta').order_by('-fecha', '-numero_comprobante')[:50]
    return render(request, 'contabilidad/asientos_lista.html', {'asientos': asientos})


@login_required
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

        total_deb = sum(Decimal(d or '0') for d in debitos)
        total_cred = sum(Decimal(c or '0') for c in creditos)

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

            for c_id, t_id, deb, cred, desc in zip(cuentas_ids, terceros_ids, debitos, creditos, descripciones):
                c_obj = PlanCuenta.objects.get(id=c_id, empresa=empresa)
                t_obj = Tercero.objects.filter(id=t_id, empresa=empresa).first() if t_id else None
                deb_val = Decimal(deb or '0')
                cred_val = Decimal(cred or '0')

                if deb_val > 0 or cred_val > 0:
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
def plan_cuentas_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    busqueda = request.GET.get('q', '').strip()
    cuentas = PlanCuenta.objects.filter(empresa=empresa, activa=True).order_by('codigo')

    if busqueda:
        cuentas = cuentas.filter(codigo__icontains=busqueda) | cuentas.filter(nombre__icontains=busqueda)

    return render(request, 'contabilidad/plan_cuentas.html', {'cuentas': cuentas, 'busqueda': busqueda})
