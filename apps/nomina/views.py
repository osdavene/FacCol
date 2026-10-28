from decimal import Decimal
from datetime import date
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction

from apps.nomina.models import Empleado, Nomina, NominaEmpleado


@login_required
def empleados_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    empleados = Empleado.objects.filter(empresa=empresa, activo=True)
    return render(request, 'nomina/empleados_lista.html', {'empleados': empleados})


@login_required
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
def nominas_lista(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    nominas = Nomina.objects.filter(empresa=empresa).order_by('-periodo_ano', '-periodo_mes')
    return render(request, 'nomina/nominas_lista.html', {'nominas': nominas})


@login_required
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

        smmlv = Decimal('1423500.00')
        aux_trans = Decimal('202050.00')

        with transaction.atomic():
            nomina, _ = Nomina.objects.update_or_create(
                empresa=empresa,
                periodo_mes=mes,
                periodo_ano=ano,
                defaults={
                    'fecha_inicio': date(ano, mes, 1),
                    'fecha_fin': date(ano, mes, 28),
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
                deducciones = salud_4 + pension_4
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

        messages.success(request, f"Nómina de {mes}/{ano} liquidada exitosamente para {empleados.count()} empleados. Total neto: ${total_net:,.0f} COP.")
        return redirect('nomina:nomina_detalle', nomina_id=nomina.id)

    return render(request, 'nomina/liquidar_formulario.html')


@login_required
def nomina_detalle(request, nomina_id):
    empresa = request.empresa_activa
    nomina = get_object_or_404(
        Nomina.objects.prefetch_related('detalles__empleado'),
        id=nomina_id,
        empresa=empresa
    )
    return render(request, 'nomina/nomina_detalle.html', {'nomina': nomina})
