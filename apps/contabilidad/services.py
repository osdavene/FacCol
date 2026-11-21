from decimal import Decimal
from django.db import transaction

from apps.contabilidad.models import AsientoContable, AsientoLinea, PlanCuenta


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
