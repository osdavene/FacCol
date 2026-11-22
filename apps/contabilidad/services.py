from datetime import date, datetime
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum

from apps.contabilidad.models import AsientoContable, AsientoLinea, PeriodoContableCerrado, PlanCuenta


def periodo_esta_cerrado(empresa, fecha_val) -> bool:
    """Verifica si el mes o el año de una fecha ya fue cerrado contablemente."""
    if not empresa or not fecha_val:
        return False
    if isinstance(fecha_val, str):
        try:
            fecha_val = datetime.strptime(fecha_val[:10], "%Y-%m-%d").date()
        except Exception:
            return False
    return PeriodoContableCerrado.objects.filter(
        empresa=empresa,
        ano=fecha_val.year,
        mes__in=(fecha_val.month, 0),
        cerrado=True,
    ).exists()


def _calcular_costo_items_factura(factura) -> Decimal:
    costo_total = Decimal("0.00")
    for it in factura.items.select_related("producto").all():
        prod = it.producto
        if prod and prod.maneja_inventario and prod.costo_unitario and prod.costo_unitario > 0:
            costo_total += (Decimal(it.cantidad) * Decimal(prod.costo_unitario)).quantize(Decimal("0.01"))
    return costo_total.quantize(Decimal("0.01"))


def _asegurar_cuenta(empresa, codigo, nombre, naturaleza):
    cuenta, _ = PlanCuenta.objects.get_or_create(
        empresa=empresa,
        codigo=codigo,
        defaults={
            "nombre": nombre,
            "nivel": 6,
            "naturaleza": naturaleza,
            "permite_movimiento": True,
            "activa": True,
        },
    )
    return cuenta


def registrar_asiento_venta(factura, metodo_pago_real="EFECTIVO"):
    """
    Genera asiento contable automático de partida doble al emitir Factura o DEE POS,
    siempre que la empresa tenga activo el módulo de Contabilidad.
    """
    empresa = factura.empresa
    if not empresa.tiene_modulo("contabilidad"):
        return None
    if factura.tipo_documento not in ("FACTURA", "POS_ELECTRONICO") or factura.total <= 0:
        return None

    ref = factura.numero_completo
    if AsientoContable.objects.filter(empresa=empresa, referencia_origen=ref, tipo="VENTA").exists():
        return None

    with transaction.atomic():
        if factura.estado == "PAGADA":
            if metodo_pago_real in ("NEQUI", "TRANSFERENCIA", "WOMPI", "TARJETA"):
                cta_debito = _asegurar_cuenta(empresa, "111005", "Bancos Nacionales", "DEBITO")
            else:
                cta_debito = _asegurar_cuenta(empresa, "110505", "Caja General", "DEBITO")
        else:
            cta_debito = _asegurar_cuenta(empresa, "130505", "Clientes Nacionales", "DEBITO")

        cta_ventas = _asegurar_cuenta(empresa, "413505", "Comercio al por Mayor y al por Menor", "CREDITO")
        cta_iva = _asegurar_cuenta(empresa, "240801", "IVA Generado por Pagar", "CREDITO")
        cta_otros = _asegurar_cuenta(empresa, "249505", "Otros Impuestos por Pagar (Saludables/INC)", "CREDITO")

        asiento = AsientoContable.objects.create(
            empresa=empresa,
            numero_comprobante=f"VT-{factura.numero_completo}",
            fecha=factura.fecha_emision,
            tipo="VENTA",
            concepto=f"Causación automática {factura.get_tipo_documento_display()} {factura.numero_completo}",
            referencia_origen=ref,
        )

        AsientoLinea.objects.create(
            asiento=asiento,
            cuenta=cta_debito,
            tercero=factura.tercero,
            descripcion=f"Cobro / Cartera {factura.numero_completo}",
            debito=factura.total,
            credito=Decimal("0.00"),
        )

        if (factura.retenciones or Decimal("0.00")) > Decimal("0.00"):
            cta_ret = _asegurar_cuenta(empresa, "135515", "Anticipo Retención en la Fuente / ICA", "DEBITO")
            AsientoLinea.objects.create(
                asiento=asiento,
                cuenta=cta_ret,
                tercero=factura.tercero,
                descripcion=f"Retenciones aplicadas {factura.numero_completo}",
                debito=factura.retenciones,
                credito=Decimal("0.00"),
            )

        base_neta = (factura.subtotal or Decimal("0.00")) - (factura.descuento or Decimal("0.00"))
        if base_neta > 0:
            AsientoLinea.objects.create(
                asiento=asiento,
                cuenta=cta_ventas,
                tercero=factura.tercero,
                descripcion=f"Ingreso operacional {factura.numero_completo}",
                debito=Decimal("0.00"),
                credito=base_neta,
            )

        if factura.iva > 0:
            AsientoLinea.objects.create(
                asiento=asiento,
                cuenta=cta_iva,
                tercero=factura.tercero,
                descripcion=f"IVA generado {factura.numero_completo}",
                debito=Decimal("0.00"),
                credito=factura.iva,
            )

        if factura.impuestos_adicionales > 0:
            AsientoLinea.objects.create(
                asiento=asiento,
                cuenta=cta_otros,
                tercero=factura.tercero,
                descripcion=f"IBUA/ICUI/INC {factura.numero_completo}",
                debito=Decimal("0.00"),
                credito=factura.impuestos_adicionales,
            )

        costo_venta = _calcular_costo_items_factura(factura)
        if costo_venta > Decimal("0.00"):
            cta_costo = _asegurar_cuenta(empresa, "613505", "Costo de Ventas Comercio al por Mayor y Menor", "DEBITO")
            cta_inv = _asegurar_cuenta(empresa, "143505", "Mercancías No Fabricadas por la Empresa", "DEBITO")
            AsientoLinea.objects.create(
                asiento=asiento,
                cuenta=cta_costo,
                tercero=factura.tercero,
                descripcion=f"Costo de venta {factura.numero_completo}",
                debito=costo_venta,
                credito=Decimal("0.00"),
            )
            AsientoLinea.objects.create(
                asiento=asiento,
                cuenta=cta_inv,
                tercero=factura.tercero,
                descripcion=f"Descargue inventario venta {factura.numero_completo}",
                debito=Decimal("0.00"),
                credito=costo_venta,
            )

        return asiento


def registrar_asiento_recibo(recibo):
    """
    Genera asiento automático de Ingreso al registrar un Recibo de Caja (abono/pago).
    """
    empresa = recibo.empresa
    if not empresa.tiene_modulo("contabilidad") or recibo.valor <= 0:
        return None

    ref = f"RC-{recibo.consecutivo}"
    if AsientoContable.objects.filter(empresa=empresa, referencia_origen=ref, tipo="INGRESO").exists():
        return None

    with transaction.atomic():
        if recibo.metodo_pago == "EFECTIVO":
            cta_tesoreria = _asegurar_cuenta(empresa, "110505", "Caja General", "DEBITO")
        else:
            cta_tesoreria = _asegurar_cuenta(empresa, "111005", "Bancos Nacionales", "DEBITO")
        cta_clientes = _asegurar_cuenta(empresa, "130505", "Clientes Nacionales", "DEBITO")

        asiento = AsientoContable.objects.create(
            empresa=empresa,
            numero_comprobante=ref,
            fecha=recibo.fecha,
            tipo="INGRESO",
            concepto=f"Recaudo {ref} ({recibo.get_metodo_pago_display()})",
            referencia_origen=ref,
        )
        AsientoLinea.objects.create(
            asiento=asiento, cuenta=cta_tesoreria, tercero=recibo.tercero,
            descripcion=f"Ingreso {ref}", debito=recibo.valor, credito=Decimal("0.00")
        )
        AsientoLinea.objects.create(
            asiento=asiento, cuenta=cta_clientes, tercero=recibo.tercero,
            descripcion=f"Abono cartera {ref}", debito=Decimal("0.00"), credito=recibo.valor
        )
        return asiento


def registrar_asiento_nota_credito(nota_credito, factura_origen=None):
    """
    Genera asiento de reversión contable al emitir una Nota Crédito.
    """
    empresa = nota_credito.empresa
    if not empresa.tiene_modulo("contabilidad") or nota_credito.total <= 0:
        return None

    ref = nota_credito.numero_completo
    if AsientoContable.objects.filter(empresa=empresa, referencia_origen=ref).exists():
        return None

    with transaction.atomic():
        cta_dev = _asegurar_cuenta(empresa, "417505", "Devoluciones y Rebajas en Ventas", "DEBITO")
        cta_iva = _asegurar_cuenta(empresa, "240801", "IVA Generado por Pagar", "CREDITO")
        cta_otros = _asegurar_cuenta(empresa, "249505", "Otros Impuestos por Pagar (Saludables/INC)", "CREDITO")

        if factura_origen and factura_origen.recibos.exists():
            cta_contra = _asegurar_cuenta(empresa, "110505", "Caja General", "DEBITO")
        else:
            cta_contra = _asegurar_cuenta(empresa, "130505", "Clientes Nacionales", "DEBITO")

        asiento = AsientoContable.objects.create(
            empresa=empresa,
            numero_comprobante=f"NC-{nota_credito.consecutivo}",
            fecha=nota_credito.fecha_emision,
            tipo="GENERAL",
            concepto=f"Reversión por Nota Crédito {nota_credito.numero_completo}",
            referencia_origen=ref,
        )

        base_neta = (nota_credito.subtotal or Decimal("0.00")) - (nota_credito.descuento or Decimal("0.00"))
        if base_neta > 0:
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_dev, tercero=nota_credito.tercero,
                descripcion=f"Devolución venta {ref}", debito=base_neta, credito=Decimal("0.00")
            )
        if nota_credito.iva > 0:
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_iva, tercero=nota_credito.tercero,
                descripcion=f"Reversión IVA {ref}", debito=nota_credito.iva, credito=Decimal("0.00")
            )
        if nota_credito.impuestos_adicionales > 0:
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_otros, tercero=nota_credito.tercero,
                descripcion=f"Reversión otros imp. {ref}", debito=nota_credito.impuestos_adicionales, credito=Decimal("0.00")
            )
        AsientoLinea.objects.create(
            asiento=asiento, cuenta=cta_contra, tercero=nota_credito.tercero,
            descripcion=f"Crédito cliente / devolución {ref}", debito=Decimal("0.00"), credito=nota_credito.total
        )
        costo_rev = _calcular_costo_items_factura(nota_credito)
        if costo_rev > Decimal("0.00"):
            cta_inv = _asegurar_cuenta(empresa, "143505", "Mercancías No Fabricadas por la Empresa", "DEBITO")
            cta_costo = _asegurar_cuenta(empresa, "613505", "Costo de Ventas Comercio al por Mayor y Menor", "DEBITO")
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_inv, tercero=nota_credito.tercero,
                descripcion=f"Reintegro inventario {ref}", debito=costo_rev, credito=Decimal("0.00")
            )
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_costo, tercero=nota_credito.tercero,
                descripcion=f"Reversión costo venta {ref}", debito=Decimal("0.00"), credito=costo_rev
            )
        return asiento


def registrar_asiento_nomina(nomina):
    """
    Genera o actualiza el asiento contable de causación de Nómina (Gasto vs Retenciones y Salarios por Pagar).
    """
    empresa = nomina.empresa
    if not empresa.tiene_modulo("contabilidad") or nomina.total_devengado <= 0:
        return None

    ref = f"NOM-{nomina.periodo_ano}{nomina.periodo_mes:02d}"
    with transaction.atomic():
        AsientoContable.objects.filter(empresa=empresa, referencia_origen=ref, tipo="NOMINA").delete()

        cta_sueldos = _asegurar_cuenta(empresa, "510506", "Sueldos y Salarios", "DEBITO")
        cta_aux = _asegurar_cuenta(empresa, "510527", "Auxilio de Transporte", "DEBITO")
        cta_eps = _asegurar_cuenta(empresa, "237005", "Aportes a Entidades Promotoras de Salud (EPS)", "CREDITO")
        cta_pension = _asegurar_cuenta(empresa, "238030", "Fondos de Cesantías y/o Pensiones", "CREDITO")
        cta_por_pagar = _asegurar_cuenta(empresa, "250505", "Salarios por Pagar", "CREDITO")

        detalles = nomina.detalles.all()
        total_sueldos = sum((d.sueldo_basico_devengado + d.horas_extras + d.bonificaciones for d in detalles), Decimal("0.00"))
        total_aux = sum((d.auxilio_transporte for d in detalles), Decimal("0.00"))
        total_salud = sum((d.salud_empleado_4 for d in detalles), Decimal("0.00"))
        total_pension = sum((d.pension_empleado_4 for d in detalles), Decimal("0.00"))
        total_otras = sum((d.otras_deducciones for d in detalles), Decimal("0.00"))

        asiento = AsientoContable.objects.create(
            empresa=empresa,
            numero_comprobante=ref,
            fecha=nomina.fecha_fin,
            tipo="NOMINA",
            concepto=f"Causación Nómina {nomina.periodo_mes}/{nomina.periodo_ano}",
            referencia_origen=ref,
        )
        if total_sueldos > 0:
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_sueldos, descripcion=f"Sueldos {ref}",
                debito=total_sueldos, credito=Decimal("0.00")
            )
        if total_aux > 0:
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_aux, descripcion=f"Auxilio transporte {ref}",
                debito=total_aux, credito=Decimal("0.00")
            )
        if total_salud > 0:
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_eps, descripcion=f"Aporte salud empleado 4% {ref}",
                debito=Decimal("0.00"), credito=total_salud
            )
        if (total_pension + total_otras) > 0:
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_pension, descripcion=f"Aporte pensión / otras ded. {ref}",
                debito=Decimal("0.00"), credito=total_pension + total_otras
            )
        if nomina.total_neto > 0:
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_por_pagar, descripcion=f"Neto salarios por pagar {ref}",
                debito=Decimal("0.00"), credito=nomina.total_neto
            )

        # Provisiones de prestaciones sociales (Prima, Cesantías, Intereses, Vacaciones)
        tot_prima = sum((d.provision_prima for d in detalles), Decimal("0.00"))
        tot_ces = sum((d.provision_cesantias for d in detalles), Decimal("0.00"))
        tot_int = sum((d.provision_intereses for d in detalles), Decimal("0.00"))
        tot_vac = sum((d.provision_vacaciones for d in detalles), Decimal("0.00"))
        tot_prov = tot_prima + tot_ces + tot_int + tot_vac
        if tot_prov > 0:
            cta_gasto_prov = _asegurar_cuenta(empresa, "510530", "Cesantías y Prestaciones Sociales", "DEBITO")
            cta_pasivo_prov = _asegurar_cuenta(empresa, "261005", "Para Obligaciones Laborales (Provisiones)", "CREDITO")
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_gasto_prov,
                descripcion=f"Provisión prestaciones (Prima/Ces/Int/Vac) {ref}",
                debito=tot_prov, credito=Decimal("0.00"),
            )
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_pasivo_prov,
                descripcion=f"Pasivo estimado prestaciones {ref}",
                debito=Decimal("0.00"), credito=tot_prov,
            )

        # Seguridad social patronal + Parafiscales
        tot_sal_pat = sum((d.salud_empleador_8_5 for d in detalles), Decimal("0.00"))
        tot_pen_pat = sum((d.pension_empleador_12 for d in detalles), Decimal("0.00"))
        tot_arl = sum((d.arl_empleador for d in detalles), Decimal("0.00"))
        tot_caja = sum((d.caja_compensacion_4 for d in detalles), Decimal("0.00"))
        tot_sena_icbf = sum((d.sena_2 + d.icbf_3 for d in detalles), Decimal("0.00"))
        tot_patronal = tot_sal_pat + tot_pen_pat + tot_arl + tot_caja + tot_sena_icbf
        if tot_patronal > 0:
            cta_gasto_seg = _asegurar_cuenta(empresa, "510568", "Aportes Patronales Seguridad Social y Parafiscales", "DEBITO")
            cta_pasivo_par = _asegurar_cuenta(empresa, "237010", "Aportes ARL, ICBF, SENA y Caja Compensación", "CREDITO")
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_gasto_seg,
                descripcion=f"Aportes patronales seguridad social y parafiscales {ref}",
                debito=tot_patronal, credito=Decimal("0.00"),
            )
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_pasivo_par,
                descripcion=f"Aportes patronales por pagar {ref}",
                debito=Decimal("0.00"), credito=tot_patronal,
            )
        return asiento


def registrar_asiento_compra(compra):
    """
    Genera asiento contable automático al registrar una Compra a Proveedor / Documento Soporte.
    """
    empresa = compra.empresa
    if not empresa.tiene_modulo("contabilidad") or compra.total <= 0:
        return None

    ref = compra.numero_completo
    if AsientoContable.objects.filter(empresa=empresa, referencia_origen=ref, tipo="COMPRA").exists():
        return None

    with transaction.atomic():
        cta_inv = _asegurar_cuenta(empresa, "143505", "Mercancías No Fabricadas por la Empresa", "DEBITO")
        if compra.metodo_pago == "CREDITO":
            cta_contra = _asegurar_cuenta(empresa, "220505", "Proveedores Nacionales", "CREDITO")
        else:
            cta_contra = _asegurar_cuenta(empresa, "110505", "Caja General", "DEBITO")

        asiento = AsientoContable.objects.create(
            empresa=empresa,
            numero_comprobante=f"CP-{ref}",
            fecha=compra.fecha,
            tipo="COMPRA",
            concepto=f"Ingreso por compra {ref} ({compra.proveedor.razon_social})",
            referencia_origen=ref,
        )
        AsientoLinea.objects.create(
            asiento=asiento, cuenta=cta_inv, tercero=compra.proveedor,
            descripcion=f"Entrada inventario {ref}", debito=compra.total, credito=Decimal("0.00")
        )
        AsientoLinea.objects.create(
            asiento=asiento, cuenta=cta_contra, tercero=compra.proveedor,
            descripcion=f"Obligación / pago compra {ref}", debito=Decimal("0.00"), credito=compra.total
        )
        return asiento


def registrar_asiento_egreso(egreso):
    """Genera comprobante contable de Egreso / Pago a Proveedor (CxP vs Tesorería)."""
    empresa = egreso.empresa
    if not empresa.tiene_modulo("contabilidad") or egreso.valor <= 0:
        return None

    ref = egreso.numero_completo
    if AsientoContable.objects.filter(empresa=empresa, referencia_origen=ref, tipo="EGRESO").exists():
        return None

    with transaction.atomic():
        if egreso.compra_id:
            cta_debito = _asegurar_cuenta(empresa, "220505", "Proveedores Nacionales", "CREDITO")
        else:
            cta_debito = _asegurar_cuenta(empresa, "233595", "Cuentas por Pagar Otros", "CREDITO")
        if egreso.metodo_pago == "EFECTIVO":
            cta_tesoreria = _asegurar_cuenta(empresa, "110505", "Caja General", "DEBITO")
        else:
            cta_tesoreria = _asegurar_cuenta(empresa, "111005", "Bancos Nacionales", "DEBITO")

        asiento = AsientoContable.objects.create(
            empresa=empresa,
            numero_comprobante=ref,
            fecha=egreso.fecha,
            tipo="EGRESO",
            concepto=f"Comprobante de egreso {ref} ({egreso.tercero.razon_social})",
            referencia_origen=ref,
        )
        AsientoLinea.objects.create(
            asiento=asiento, cuenta=cta_debito, tercero=egreso.tercero,
            descripcion=f"Pago obligación {ref}", debito=egreso.valor, credito=Decimal("0.00")
        )
        AsientoLinea.objects.create(
            asiento=asiento, cuenta=cta_tesoreria, tercero=egreso.tercero,
            descripcion=f"Salida {egreso.get_metodo_pago_display()} {ref}", debito=Decimal("0.00"), credito=egreso.valor
        )
        return asiento


def registrar_asiento_gasto(gasto):
    """Genera asiento contable para gasto operativo (51xx/52xx + IVA desc vs Retenciones y CxP/Bancos)."""
    empresa = gasto.empresa
    if not empresa.tiene_modulo("contabilidad") or (gasto.subtotal + gasto.iva) <= 0:
        return None

    ref = gasto.numero_completo
    if AsientoContable.objects.filter(empresa=empresa, referencia_origen=ref).exists():
        return None

    with transaction.atomic():
        asiento = AsientoContable.objects.create(
            empresa=empresa,
            numero_comprobante=ref,
            fecha=gasto.fecha,
            tipo="EGRESO" if gasto.metodo_pago == "CONTADO" else "COMPRA",
            concepto=f"Gasto {ref}: {gasto.concepto}",
            referencia_origen=ref,
        )
        if gasto.subtotal > 0:
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=gasto.cuenta_gasto, tercero=gasto.tercero,
                descripcion=gasto.concepto, debito=gasto.subtotal, credito=Decimal("0.00")
            )
        if gasto.iva > 0:
            cta_iva_desc = _asegurar_cuenta(empresa, "240805", "IVA Descontable en Compras y Gastos", "DEBITO")
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_iva_desc, tercero=gasto.tercero,
                descripcion=f"IVA descontable {ref}", debito=gasto.iva, credito=Decimal("0.00")
            )
        if gasto.retencion_fuente > 0:
            cta_rf = _asegurar_cuenta(empresa, "236540", "Retención en la Fuente por Pagar (Compras/Servicios)", "CREDITO")
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_rf, tercero=gasto.tercero,
                descripcion=f"ReteFuente practicada {ref}", debito=Decimal("0.00"), credito=gasto.retencion_fuente
            )
        if gasto.retencion_ica > 0:
            cta_rica = _asegurar_cuenta(empresa, "236805", "Retención de ICA por Pagar", "CREDITO")
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_rica, tercero=gasto.tercero,
                descripcion=f"ReteICA practicada {ref}", debito=Decimal("0.00"), credito=gasto.retencion_ica
            )
        if gasto.total_pagar > 0:
            if gasto.metodo_pago == "CREDITO":
                cta_contra = _asegurar_cuenta(empresa, "233595", "Costos y Gastos por Pagar", "CREDITO")
            else:
                cta_contra = _asegurar_cuenta(empresa, "111005", "Bancos Nacionales", "DEBITO")
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_contra, tercero=gasto.tercero,
                descripcion=f"Pago / Causación gasto {ref}", debito=Decimal("0.00"), credito=gasto.total_pagar
            )
        return asiento


def ejecutar_cierre_anual_resultados(empresa, ano: int, usuario=None):
    """
    Cancela saldos del año en cuentas de resultado (clases 4, 5, 6, 7) trasladando
    la utilidad o pérdida neta a patrimonio (360505 / 361005) y bloquea el año.
    """
    if PeriodoContableCerrado.objects.filter(empresa=empresa, ano=ano, mes=0, cerrado=True).exists():
        return None, f"El cierre anual {ano} ya fue ejecutado."

    fecha_cierre = date(ano, 12, 31)
    lineas_res = (
        AsientoLinea.objects.filter(
            asiento__empresa=empresa,
            asiento__fecha__year=ano,
        )
        .filter(cuenta__codigo__regex=r"^[4567]")
        .values("cuenta_id", "cuenta__codigo", "cuenta__nombre")
        .annotate(deb=Sum("debito"), cred=Sum("credito"))
    )

    movimientos_cierre = []
    resultado_neto = Decimal("0.00")
    for row in lineas_res:
        saldo = (row["cred"] or Decimal("0.00")) - (row["deb"] or Decimal("0.00"))
        if abs(saldo) >= Decimal("0.01"):
            movimientos_cierre.append((row["cuenta_id"], saldo))
            resultado_neto += saldo

    with transaction.atomic():
        asiento = None
        if movimientos_cierre:
            asiento = AsientoContable.objects.create(
                empresa=empresa,
                numero_comprobante=f"CIERRE-{ano}",
                fecha=fecha_cierre,
                tipo="GENERAL",
                concepto=f"Cierre anual de cuentas de resultado {ano}",
                referencia_origen=f"CIERRE-{ano}",
            )
            for cta_id, saldo in movimientos_cierre:
                if saldo > 0:
                    AsientoLinea.objects.create(
                        asiento=asiento, cuenta_id=cta_id,
                        descripcion=f"Cancelación cuenta resultado {ano}",
                        debito=saldo, credito=Decimal("0.00"),
                    )
                else:
                    AsientoLinea.objects.create(
                        asiento=asiento, cuenta_id=cta_id,
                        descripcion=f"Cancelación cuenta resultado {ano}",
                        debito=Decimal("0.00"), credito=abs(saldo),
                    )
            if resultado_neto >= 0:
                cta_pat = _asegurar_cuenta(empresa, "360505", "Utilidad del Ejercicio", "CREDITO")
                AsientoLinea.objects.create(
                    asiento=asiento, cuenta=cta_pat,
                    descripcion=f"Utilidad neta ejercicio {ano}",
                    debito=Decimal("0.00"), credito=resultado_neto,
                )
            else:
                cta_pat = _asegurar_cuenta(empresa, "361005", "Pérdida del Ejercicio", "DEBITO")
                AsientoLinea.objects.create(
                    asiento=asiento, cuenta=cta_pat,
                    descripcion=f"Pérdida neta ejercicio {ano}",
                    debito=abs(resultado_neto), credito=Decimal("0.00"),
                )

        PeriodoContableCerrado.objects.update_or_create(
            empresa=empresa,
            ano=ano,
            mes=0,
            defaults={"cerrado": True, "cerrado_por": usuario, "observaciones": f"Cierre anual {ano}"},
        )
        return asiento, f"Cierre anual {ano} ejecutado correctamente (Resultado neto: ${resultado_neto:,.0f} COP)."


def ejecutar_depreciacion_activos(empresa, ano: int, mes: int):
    """Calcula y contabiliza la depreciación mensual NIIF (516005 vs 159205) de activos fijos activos."""
    import calendar
    from apps.contabilidad.models import ActivoFijo

    fecha_dep = date(ano, mes, calendar.monthrange(ano, mes)[1])
    if periodo_esta_cerrado(empresa, fecha_dep):
        return None, 0, Decimal("0.00"), f"El período {ano}-{mes:02d} está cerrado."
    ref = f"DEP-{ano}{mes:02d}"
    if AsientoContable.objects.filter(empresa=empresa, referencia_origen=ref).exists():
        return None, 0, Decimal("0.00"), f"La depreciación del período {ano}-{mes:02d} ya fue ejecutada."

    activos = list(ActivoFijo.objects.filter(empresa=empresa, activo=True, fecha_compra__lte=fecha_dep))
    total_dep = Decimal("0.00")
    procesados = []
    for act in activos:
        saldo_depreciable = max(Decimal("0.00"), (act.valor_compra - act.valor_residual) - act.depreciacion_acumulada)
        if saldo_depreciable <= Decimal("0.00"):
            continue
        cuota = min(act.cuota_depreciacion_mensual, saldo_depreciable)
        if cuota > Decimal("0.00"):
            procesados.append((act, cuota))
            total_dep += cuota

    if not procesados:
        return None, 0, Decimal("0.00"), "No hay activos pendientes por depreciar para el período."

    with transaction.atomic():
        cta_gasto = _asegurar_cuenta(empresa, "516005", "Depreciación Propiedad Planta y Equipo", "DEBITO")
        cta_acum = _asegurar_cuenta(empresa, "159205", "Depreciación Acumulada", "CREDITO")
        asiento = AsientoContable.objects.create(
            empresa=empresa,
            numero_comprobante=ref,
            fecha=fecha_dep,
            tipo="GENERAL",
            concepto=f"Depreciación mensual activos fijos {ano}-{mes:02d}",
            referencia_origen=ref,
        )
        for act, cuota in procesados:
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_gasto,
                descripcion=f"Depreciación {act.codigo} - {act.nombre}",
                debito=cuota, credito=Decimal("0.00"),
            )
            AsientoLinea.objects.create(
                asiento=asiento, cuenta=cta_acum,
                descripcion=f"Depreciación acumulada {act.codigo}",
                debito=Decimal("0.00"), credito=cuota,
            )
            act.depreciacion_acumulada += cuota
            act.ultima_depreciacion = fecha_dep
            act.save(update_fields=["depreciacion_acumulada", "ultima_depreciacion"])
    return asiento, len(procesados), total_dep, f"Depreciación {ano}-{mes:02d} contabilizada ({len(procesados)} activos, ${total_dep:,.0f})."


def calcular_borradores_tributarios(empresa, ano: int, mes: int):
    """Calcula borradores informativos de Formulario 300 (IVA) y Formulario 350 (ReteFuente)."""
    from apps.facturacion.models import Factura

    qs_vta = Factura.objects.filter(
        empresa=empresa,
        fecha_emision__year=ano,
        fecha_emision__month=mes,
        tipo_documento__in=("FACTURA", "POS_ELECTRONICO", "NOTA_DEBITO"),
    ).exclude(estado="ANULADA")
    qs_nc = Factura.objects.filter(
        empresa=empresa,
        fecha_emision__year=ano,
        fecha_emision__month=mes,
        tipo_documento="NOTA_CREDITO",
    ).exclude(estado="ANULADA")

    ingresos_brutos = sum((f.subtotal for f in qs_vta), Decimal("0.00"))
    devoluciones_vta = sum((f.subtotal for f in qs_nc), Decimal("0.00"))
    iva_generado = sum((f.iva for f in qs_vta), Decimal("0.00")) - sum((f.iva for f in qs_nc), Decimal("0.00"))

    mov_iva_desc = AsientoLinea.objects.filter(
        asiento__empresa=empresa,
        asiento__fecha__year=ano,
        asiento__fecha__month=mes,
        cuenta__codigo__startswith="240805",
    ).aggregate(deb=Sum("debito"), cred=Sum("credito"))
    iva_descontable = (mov_iva_desc["deb"] or Decimal("0.00")) - (mov_iva_desc["cred"] or Decimal("0.00"))
    saldo_pagar_iva = max(Decimal("0.00"), iva_generado - max(Decimal("0.00"), iva_descontable))

    mov_rte = AsientoLinea.objects.filter(
        asiento__empresa=empresa,
        asiento__fecha__year=ano,
        asiento__fecha__month=mes,
        cuenta__codigo__startswith="2365",
    ).aggregate(deb=Sum("debito"), cred=Sum("credito"))
    retefuente_pagar = max(Decimal("0.00"), (mov_rte["cred"] or Decimal("0.00")) - (mov_rte["deb"] or Decimal("0.00")))

    return {
        "ano": ano,
        "mes": mes,
        "form_300": {
            "ingresos_brutos": ingresos_brutos,
            "devoluciones": devoluciones_vta,
            "iva_generado": max(Decimal("0.00"), iva_generado),
            "iva_descontable": max(Decimal("0.00"), iva_descontable),
            "saldo_pagar": saldo_pagar_iva,
        },
        "form_350": {
            "retefuente_practicada": retefuente_pagar,
        },
    }


def generar_xml_exogena_prevalidador(empresa, ano: int, formato: str = "1001") -> str:
    """Genera XML compatible con Prevalidador DIAN de Información Exógena (Formatos 1001 y 1007)."""
    import xml.etree.ElementTree as ET
    from apps.facturacion.models import Factura
    from apps.contabilidad.models import ComprobanteEgreso, GastoOperativo

    fmt = "1007" if str(formato) == "1007" else "1001"
    root = ET.Element("mas")
    cab = ET.SubElement(root, "Cab")
    for k, v in (
        ("Ano", str(ano)),
        ("CodCpt", "1"),
        ("Formato", fmt),
        ("Version", "10" if fmt == "1001" else "9"),
        ("NumEnvio", "1"),
        ("FecEnvio", f"{date.today().strftime('%Y-%m-%d')}T08:00:00"),
        ("FecInicial", f"{ano}-01-01"),
        ("FecFinal", f"{ano}-12-31"),
        ("ValorTotal", "0"),
        ("CantReg", "0"),
    ):
        ET.SubElement(cab, k).text = v

    registros = []
    if fmt == "1007":
        qs = (
            Factura.objects.filter(
                empresa=empresa,
                fecha_emision__year=ano,
                tipo_documento__in=("FACTURA", "POS_ELECTRONICO"),
            )
            .exclude(estado="ANULADA")
            .values("tercero__tipo_documento", "tercero__numero_documento", "tercero__razon_social")
            .annotate(base=Sum("subtotal"), iva=Sum("iva"))
        )
        for row in qs:
            registros.append({
                "tdoc": "31" if row["tercero__tipo_documento"] == "NIT" else "13",
                "nid": row["tercero__numero_documento"] or "",
                "raz": row["tercero__razon_social"] or "",
                "val": int((row["base"] or Decimal("0")).quantize(Decimal("1"))),
                "iva": int((row["iva"] or Decimal("0")).quantize(Decimal("1"))),
            })
    else:
        qs_g = (
            GastoOperativo.objects.filter(empresa=empresa, fecha__year=ano)
            .values("tercero__tipo_documento", "tercero__numero_documento", "tercero__razon_social")
            .annotate(base=Sum("subtotal"), rte=Sum("retencion_fuente"))
        )
        for row in qs_g:
            registros.append({
                "tdoc": "31" if row["tercero__tipo_documento"] == "NIT" else "13",
                "nid": row["tercero__numero_documento"] or "",
                "raz": row["tercero__razon_social"] or "",
                "val": int((row["base"] or Decimal("0")).quantize(Decimal("1"))),
                "rte": int((row["rte"] or Decimal("0")).quantize(Decimal("1"))),
            })

    total_sum = sum(r["val"] for r in registros)
    cab.find("ValorTotal").text = str(total_sum)
    cab.find("CantReg").text = str(len(registros))

    tag_item = "ingresos" if fmt == "1007" else "pagos"
    for r in registros:
        attrs = {
            "cpt": "4001" if fmt == "1007" else "5016",
            "tdoc": r["tdoc"],
            "nid": r["nid"],
            "raz": r["raz"][:120],
            "pago" if fmt == "1001" else "vbr": str(r["val"]),
        }
        if fmt == "1001":
            attrs["ret"] = str(r.get("rte", 0))
        ET.SubElement(root, tag_item, attrs)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
