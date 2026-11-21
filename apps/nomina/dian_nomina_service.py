import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal


DIAN_NOMINA_QR_PROD = "https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey="
DIAN_NOMINA_QR_HAB = "https://catalogo-vpfe-hab.dian.gov.co/document/searchqr?documentkey="


def _fmt_dec(val):
    return f"{Decimal(val or 0):.2f}"


def calcular_cune_nomina(detalle):
    """
    Calcula el CUNE (Código Único de Nómina Electrónica - Resolución DIAN 000013 de 2021)
    y el número del documento soporte de pago individual (`NE-{ano}{mes}-{id}`).
    """
    nomina = detalle.nomina
    empresa = nomina.empresa
    empleado = detalle.empleado

    num_ne = f"NE{nomina.periodo_ano}{nomina.periodo_mes:02d}{detalle.id}"
    fecha_gen = nomina.fecha_fin.strftime("%Y-%m-%d")
    hora_dt = nomina.created_at or datetime.now()
    hora_gen = hora_dt.strftime("%H:%M:%S-05:00")

    val_dev = _fmt_dec(detalle.total_devengado)
    val_ded = _fmt_dec(detalle.total_deducciones)
    val_tol = _fmt_dec(detalle.neto_a_pagar)
    nit_emp = (empresa.nit or "").strip()
    doc_trab = (empleado.numero_documento or "").strip()
    tipo_xml = "102"
    pin_soft = (empresa.dian_pin or empresa.dian_software_id or "FACCOL-NE-PIN").strip()
    tipo_amb = "1" if empresa.modo_dian == "PRODUCCION" else "2"

    cadena = (
        f"{num_ne}{fecha_gen}{hora_gen}"
        f"{val_dev}{val_ded}{val_tol}"
        f"{nit_emp}{doc_trab}{tipo_xml}{pin_soft}{tipo_amb}"
    )
    cune = hashlib.sha384(cadena.encode("utf-8")).hexdigest()
    base_qr = DIAN_NOMINA_QR_PROD if tipo_amb == "1" else DIAN_NOMINA_QR_HAB
    return num_ne, cune, f"{base_qr}{cune}"


def generar_xml_nomina_individual(detalle):
    """
    Genera XML de Documento Soporte de Pago de Nómina Electrónica (NominaIndividual DIAN).
    """
    nomina = detalle.nomina
    empresa = nomina.empresa
    empleado = detalle.empleado
    num_ne, cune, _ = calcular_cune_nomina(detalle)
    tipo_amb = "1" if empresa.modo_dian == "PRODUCCION" else "2"

    ns = "dian:gov:co:facturaelectronica:NominaIndividual"
    ET.register_namespace("", ns)

    def el(tag, parent=None, text=None, **attrs):
        node = ET.Element(f"{{{ns}}}{tag}", {k: str(v) for k, v in attrs.items()})
        if text is not None:
            node.text = str(text)
        if parent is not None:
            parent.append(node)
        return node

    root = el("NominaIndividual")
    periodo = el("Periodo", root,
                 FechaIngreso=empleado.fecha_ingreso.strftime("%Y-%m-%d"),
                 FechaLiquidacionInicio=nomina.fecha_inicio.strftime("%Y-%m-%d"),
                 FechaLiquidacionFin=nomina.fecha_fin.strftime("%Y-%m-%d"),
                 TiempoLaborado=str(detalle.dias_trabajados),
                 FechaGen=nomina.fecha_fin.strftime("%Y-%m-%d"))
    _ = periodo

    el("NumeroSecuenciaXML", root,
       Prefijo="NE",
       Consecutivo=str(detalle.id),
       Numero=num_ne)
    el("LugarGeneracionXML", root, Pais="CO", DepartamentoEstado="11", MunicipioCiudad="11001", Idioma="es")
    el("ProveedorXML", root, RazonSocial=empresa.razon_social, NIT=empresa.nit, DV=empresa.dv or "0", SoftwareID=empresa.dian_software_id or "FACCOL")
    el("InformacionGeneral", root,
       Version="V1.0: Documento Soporte de Pago de Nómina Electrónica",
       Ambiente=tipo_amb,
       TipoXML="102",
       CUNE=cune,
       EncripCUNE="CUNE-SHA384",
       FechaGen=nomina.fecha_fin.strftime("%Y-%m-%d"),
       TipoMoneda="COP")
    el("Empleador", root, RazonSocial=empresa.razon_social, NIT=empresa.nit, DV=empresa.dv or "0", Pais="CO")
    el("Trabajador", root,
       TipoTrabajador="01",
       TipoDocumento="13" if empleado.tipo_documento == "CC" else "22",
       NumeroDocumento=empleado.numero_documento,
       PrimerApellido=empleado.apellidos,
       PrimerNombre=empleado.nombres,
       SalarioIntegral="false",
       Sueldo=_fmt_dec(empleado.salario_base))

    dev = el("Devengados", root)
    el("Basico", dev, DiasTrabajados=str(detalle.dias_trabajados), SueldoTrabajado=_fmt_dec(detalle.sueldo_basico_devengado))
    if detalle.auxilio_transporte > 0:
        el("Transporte", dev, AuxilioTransporte=_fmt_dec(detalle.auxilio_transporte))

    ded = el("Deducciones", root)
    el("Salud", ded, Porcentaje="4.00", Deduccion=_fmt_dec(detalle.salud_empleado_4))
    el("FondoPension", ded, Porcentaje="4.00", Deduccion=_fmt_dec(detalle.pension_empleado_4))

    el("DevengadosTotal", root, text=_fmt_dec(detalle.total_devengado))
    el("DeduccionesTotal", root, text=_fmt_dec(detalle.total_deducciones))
    el("ComprobanteTotal", root, text=_fmt_dec(detalle.neto_a_pagar))

    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
