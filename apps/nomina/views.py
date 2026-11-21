import calendar
from decimal import Decimal
from datetime import date
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.views.decorators.http import require_POST

from apps.contabilidad.services import registrar_asiento_nomina
from apps.core.mail_service import enviar_correo_sistema
from apps.nomina.dian_nomina_service import calcular_cune_nomina, generar_xml_nomina_individual
from apps.nomina.models import Empleado, Nomina, NominaEmpleado
from apps.usuarios.decorators import requiere_nomina


@login_required
@requiere_nomina
def empleados_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    empleados = Empleado.objects.filter(empresa=empresa, activo=True)
    return render(request, 'nomina/empleados_lista.html', {'empleados': empleados})


@login_required
@requiere_nomina
def empleado_crear(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    if request.method == 'POST':
        numero_documento = request.POST.get('numero_documento', '').strip()
        nombres = request.POST.get('nombres', '').strip()
        apellidos = request.POST.get('apellidos', '').strip()
        cargo = request.POST.get('cargo', '').strip()
        salario = Decimal(request.POST.get('salario_base', '1423500') or '1423500')
        tipo_contrato = request.POST.get('tipo_contrato', 'TERMINO_INDEFINIDO')
        riesgo_arl = request.POST.get('nivel_riesgo_arl', '1')
        auxilio = 'tiene_auxilio_transporte' in request.POST
        fecha_ing = request.POST.get('fecha_ingreso') or date.today()

        emp = Empleado.objects.create(
            empresa=empresa,
            numero_documento=numero_documento,
            nombres=nombres,
            apellidos=apellidos,
            email=request.POST.get('email', '').strip(),
            telefono=request.POST.get('telefono', '').strip(),
            direccion=request.POST.get('direccion', '').strip(),
            cargo=cargo,
            salario_base=salario,
            tiene_auxilio_transporte=auxilio,
            tipo_contrato=tipo_contrato,
            nivel_riesgo_arl=riesgo_arl,
            fecha_ingreso=fecha_ing,
            eps=request.POST.get('eps', '').strip(),
            fondo_pension=request.POST.get('fondo_pension', '').strip(),
            caja_compensacion=request.POST.get('caja_compensacion', '').strip(),
        )

        messages.success(request, f"Empleado {emp.nombre_completo} registrado con éxito.")
        return redirect('nomina:empleados_lista')

    return render(request, 'nomina/empleado_formulario.html', {'accion': 'Registrar'})


@login_required
@requiere_nomina
def nominas_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    nominas = Nomina.objects.filter(empresa=empresa).order_by('-periodo_ano', '-periodo_mes')
    return render(request, 'nomina/nominas_lista.html', {'nominas': nominas})


@login_required
@requiere_nomina
def nomina_liquidar(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    if request.method == 'POST':
        mes = int(request.POST.get('periodo_mes', date.today().month))
        ano = int(request.POST.get('periodo_ano', date.today().year))

        # Tasas de riesgo ARL Colombia
        tasas_arl = {
            '1': Decimal('0.00522'),
            '2': Decimal('0.01044'),
            '3': Decimal('0.02436'),
            '4': Decimal('0.04350'),
            '5': Decimal('0.06960'),
        }

        smmlv = Decimal(request.POST.get('smmlv', '1423500.00') or '1423500.00')
        aux_trans = Decimal(request.POST.get('auxilio_transporte', '202050.00') or '202050.00')
        ultimo_dia = calendar.monthrange(ano, mes)[1]

        with transaction.atomic():
            nomina, _ = Nomina.objects.update_or_create(
                empresa=empresa,
                periodo_mes=mes,
                periodo_ano=ano,
                defaults={
                    'fecha_inicio': date(ano, mes, 1),
                    'fecha_fin': date(ano, mes, ultimo_dia),
                    'smmlv': smmlv,
                    'auxilio_transporte_legal': aux_trans,
                    'estado': 'LIQUIDADA',
                }
            )

            # Eliminar liquidaciones anteriores de ese período para recalcular limpiamente
            nomina.detalles.all().delete()

            total_dev = Decimal('0.00')
            total_ded = Decimal('0.00')
            total_net = Decimal('0.00')

            empleados = Empleado.objects.filter(empresa=empresa, activo=True)
            for emp in empleados:
                dias = 30
                salario_mes = (emp.salario_base * Decimal(dias) / Decimal(30)).quantize(Decimal('0.01'))
                
                # Auxilio de transporte: aplica si el salario es <= 2 SMMLV
                aplica_aux = emp.tiene_auxilio_transporte and (emp.salario_base <= (smmlv * Decimal(2)))
                val_aux = aux_trans if aplica_aux else Decimal('0.00')

                devengado = salario_mes + val_aux

                # Deducciones empleado (Base IBC = Salario sin auxilio de transporte)
                ibc = salario_mes
                salud_4 = (ibc * Decimal('0.04')).quantize(Decimal('0.01'))
                pension_4 = (ibc * Decimal('0.04')).quantize(Decimal('0.01'))
                # Fondo de Solidaridad Pensional (1% obligatorio si salario >= 4 SMMLV)
                fsp_1 = (ibc * Decimal('0.01')).quantize(Decimal('0.01')) if ibc >= (smmlv * Decimal(4)) else Decimal('0.00')
                deducciones = salud_4 + pension_4 + fsp_1
                neto = devengado - deducciones

                # Aportes empleador
                salud_8_5 = (ibc * Decimal('0.085')).quantize(Decimal('0.01'))
                pension_12 = (ibc * Decimal('0.12')).quantize(Decimal('0.01'))
                tasa_arl = tasas_arl.get(emp.nivel_riesgo_arl, Decimal('0.00522'))
                arl = (ibc * tasa_arl).quantize(Decimal('0.01'))
                caja_4 = (ibc * Decimal('0.04')).quantize(Decimal('0.01'))

                # Provisiones prestaciones sociales (base = salario + auxilio de transporte)
                base_prestaciones = devengado
                prima = (base_prestaciones * Decimal('0.0833')).quantize(Decimal('0.01'))
                cesantias = (base_prestaciones * Decimal('0.0833')).quantize(Decimal('0.01'))
                intereses = (cesantias * Decimal('0.12')).quantize(Decimal('0.01'))
                vacaciones = (salario_mes * Decimal('0.0417')).quantize(Decimal('0.01'))

                NominaEmpleado.objects.create(
                    nomina=nomina,
                    empleado=emp,
                    dias_trabajados=dias,
                    sueldo_basico_devengado=salario_mes,
                    auxilio_transporte=val_aux,
                    total_devengado=devengado,
                    salud_empleado_4=salud_4,
                    pension_empleado_4=pension_4,
                    otras_deducciones=fsp_1,
                    total_deducciones=deducciones,
                    neto_a_pagar=neto,
                    salud_empleador_8_5=salud_8_5,
                    pension_empleador_12=pension_12,
                    arl_empleador=arl,
                    caja_compensacion_4=caja_4,
                    provision_prima=prima,
                    provision_cesantias=cesantias,
                    provision_intereses=intereses,
                    provision_vacaciones=vacaciones,
                )

                total_dev += devengado
                total_ded += deducciones
                total_net += neto

            nomina.total_devengado = total_dev
            nomina.total_deducciones = total_ded
            nomina.total_neto = total_net
            nomina.save()
            registrar_asiento_nomina(nomina)

        messages.success(request, f"Nómina de {mes}/{ano} liquidada exitosamente para {empleados.count()} empleados. Total neto: ${total_net:,.0f} COP.")
        return redirect('nomina:nomina_detalle', nomina_id=nomina.id)

    return render(request, 'nomina/liquidar_formulario.html')


@login_required
@requiere_nomina
def nomina_detalle(request, nomina_id):
    empresa = request.empresa_activa
    nomina = get_object_or_404(
        Nomina.objects.prefetch_related('detalles__empleado'),
        id=nomina_id,
        empresa=empresa
    )
    return render(request, 'nomina/nomina_detalle.html', {'nomina': nomina})


@login_required
@requiere_nomina
def nomina_xml_empleado(request, detalle_id):
    """Descarga el XML de Nómina Electrónica Individual (DIAN Resolución 000013 / CUNE)."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    detalle = get_object_or_404(
        NominaEmpleado.objects.select_related('nomina', 'empleado'),
        id=detalle_id,
        nomina__empresa=empresa,
    )
    xml_str = generar_xml_nomina_individual(detalle)
    num_ne, _, _ = calcular_cune_nomina(detalle)
    resp = HttpResponse(xml_str, content_type='application/xml; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{num_ne}_{detalle.empleado.numero_documento}.xml"'
    return resp


@login_required
@requiere_nomina
def nomina_desprendible(request, detalle_id):
    """Desprendible de pago imprimible con CUNE de Nómina Electrónica."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    detalle = get_object_or_404(
        NominaEmpleado.objects.select_related('nomina__empresa', 'empleado'),
        id=detalle_id,
        nomina__empresa=empresa,
    )
    num_ne, cune, qr_url = calcular_cune_nomina(detalle)
    return render(request, 'nomina/desprendible.html', {
        'detalle': detalle,
        'nomina': detalle.nomina,
        'num_ne': num_ne,
        'cune': cune,
        'qr_url': qr_url,
    })


@login_required
@requiere_nomina
@require_POST
def nomina_enviar_colilla(request, detalle_id):
    """Envía el desprendible de nómina electrónica por email al trabajador."""
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    detalle = get_object_or_404(
        NominaEmpleado.objects.select_related('nomina', 'empleado'),
        id=detalle_id,
        nomina__empresa=empresa,
    )
    correo = (detalle.empleado.email or '').strip()
    if not correo:
        messages.error(request, f"El empleado {detalle.empleado.nombre_completo} no tiene correo registrado.")
        return redirect('nomina:nomina_detalle', nomina_id=detalle.nomina_id)

    num_ne, cune, _ = calcular_cune_nomina(detalle)
    xml_str = generar_xml_nomina_individual(detalle)
    html = (
        f"<h2>Desprendible de Nómina Electrónica {num_ne}</h2>"
        f"<p><b>Empresa:</b> {empresa.razon_social} (NIT {empresa.nit_completo})</p>"
        f"<p><b>Empleado:</b> {detalle.empleado.nombre_completo} ({detalle.empleado.numero_documento})</p>"
        f"<p><b>Total Devengado:</b> ${detalle.total_devengado:,.0f} COP<br>"
        f"<b>Deducciones:</b> -${detalle.total_deducciones:,.0f} COP<br>"
        f"<b>Neto Pagado:</b> <b>${detalle.neto_a_pagar:,.0f} COP</b></p>"
        f"<p style='font-size:11px;color:#64748b'>CUNE DIAN: {cune}</p>"
    )
    try:
        enviar_correo_sistema(
            correo,
            f"Desprendible Nómina {detalle.nomina.periodo_mes}/{detalle.nomina.periodo_ano} - {empresa.display_name}",
            html,
            attachments=[(f"{num_ne}.xml", xml_str.encode('utf-8'), 'application/xml')],
            empresa=empresa,
            fail_silently=False,
        )
        messages.success(request, f"Colilla {num_ne} enviada a {correo}.")
    except Exception as exc:
        messages.error(request, f"No se pudo enviar la colilla: {exc}")

    return redirect('nomina:nomina_detalle', nomina_id=detalle.nomina_id)
