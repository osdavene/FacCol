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
