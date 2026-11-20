import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal


DIAN_QR_PROD = "https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey="
DIAN_QR_HAB = "https://catalogo-vpfe-hab.dian.gov.co/document/searchqr?documentkey="


def _fmt_dec(val):
    return f"{Decimal(val or 0):.2f}"


def desglosar_impuestos_item(prod, precio_ingresado, empresa):
    """
    Devuelve (precio_base_sin_impuestos, porcentaje_iva, porcentaje_otro_impuesto)
    considerando si la empresa es No Responsable de IVA y si el precio de góndola
    ya incluye IVA e impuestos saludables (IBUA/ICUI/Impoconsumo).
    """
    precio = Decimal(str(precio_ingresado or 0))
    if empresa.regimen_tributario == "NO_RESPONSABLE_IVA":
        iva_pct = Decimal("0.00")
    else:
        iva_pct = prod.tarifa_iva if prod else Decimal("19.00")

    otro_pct = prod.impuesto_saludable_pct if prod else Decimal("0.00")
    if prod and prod.precio_incluye_impuestos:
        factor = Decimal("1.00") + ((iva_pct + otro_pct) / Decimal("100.00"))
        if factor > 0:
            precio = (precio / factor).quantize(Decimal("0.01"))
    return precio, iva_pct, otro_pct


def calcular_cufe_y_qr(factura):
    """
    Calcula el CUFE (Factura) o CUDE (DEE POS / Nota Crédito) siguiendo la estructura
    del Anexo Técnico DIAN 1.9 / Resolución 000165 de 2023 y genera la URL QR oficial.
    """
    empresa = factura.empresa
    tercero = factura.tercero
    fecha_str = factura.fecha_emision.strftime("%Y-%m-%d")
    hora_dt = factura.created_at or datetime.now()
    hora_str = hora_dt.strftime("%H:%M:%S-05:00")

    base_imponible = (factura.subtotal or Decimal("0.00")) - (factura.descuento or Decimal("0.00"))
    val_fac = _fmt_dec(base_imponible)
    val_iva = _fmt_dec(factura.iva)
    val_inc = _fmt_dec(factura.impuestos_adicionales)
    val_ica = "0.00"
    val_tot = _fmt_dec(factura.total)

    nit_fe = (empresa.nit or "").strip()
    num_adq = (tercero.numero_documento or "222222222222").strip()
    pin_o_cltec = (empresa.dian_pin or empresa.dian_software_id or "FACCOL-DEV-PIN").strip()
    tipo_amb = "1" if empresa.modo_dian == "PRODUCCION" else "2"

    cadena = (
        f"{factura.numero_completo}{fecha_str}{hora_str}"
        f"{val_fac}01{val_iva}04{val_inc}03{val_ica}{val_tot}"
        f"{nit_fe}{num_adq}{pin_o_cltec}{tipo_amb}"
    )
    cufe = hashlib.sha384(cadena.encode("utf-8")).hexdigest()

    base_qr = DIAN_QR_PROD if tipo_amb == "1" else DIAN_QR_HAB
    qr_data = (
        f"NumFac: {factura.numero_completo}\n"
        f"FecFac: {fecha_str}\n"
        f"HorFac: {hora_str}\n"
        f"NitFac: {nit_fe}\n"
        f"DocAdq: {num_adq}\n"
        f"ValFac: {val_fac}\n"
        f"ValIva: {val_iva}\n"
        f"ValOtroIm: {val_inc}\n"
        f"ValTotal: {val_tot}\n"
        f"CUFE: {cufe}\n"
        f"URL: {base_qr}{cufe}"
    )
    return cufe, qr_data


def encolar_documento_dian(factura, save=True):
    """
    Encola el documento para transmisión asíncrona DIAN sin bloquear la caja del tendero.
    """
    if factura.tipo_documento in ("COTIZACION", "REMISION"):
        factura.estado_dian = "NO_APLICA"
        factura.dian_mensaje = "Documento comercial interno (no requiere transmisión DIAN)."
    elif factura.empresa.dian_software_id and factura.empresa.resolucion_dian:
        factura.estado_dian = "PENDIENTE"
        factura.dian_mensaje = "En cola para transmisión electrónica DIAN."
    else:
        factura.estado_dian = "CONTINGENCIA"
        factura.dian_mensaje = (
            "Emitido en contingencia local: configure Software ID, PIN y Resolución DIAN "
            "para transmitir en línea."
        )

    if save:
        factura.save(update_fields=["estado_dian", "dian_mensaje"])
    return factura.estado_dian


def generar_xml_ubl(factura):
    """
    Construye XML UBL 2.1 estructurado para Factura Electrónica (01)
    o Documento Equivalente Electrónico POS - Resolución 000165 (20).
    """
    empresa = factura.empresa
    tercero = factura.tercero
    ns_invoice = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    ns_cac = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    ns_cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

    ET.register_namespace("", ns_invoice)
    ET.register_namespace("cac", ns_cac)
    ET.register_namespace("cbc", ns_cbc)

    def cbc(tag, text="", **attrs):
        el = ET.Element(f"{{{ns_cbc}}}{tag}", attrs)
        el.text = str(text)
        return el

    def cac(tag):
        return ET.Element(f"{{{ns_cac}}}{tag}")

    root = ET.Element(f"{{{ns_invoice}}}Invoice")
    tipo_amb = "1" if empresa.modo_dian == "PRODUCCION" else "2"
    type_code = "20" if factura.tipo_documento == "POS_ELECTRONICO" else "01"
    profile_id = (
        "DIAN 2.1: Documento Equivalente POS"
        if factura.tipo_documento == "POS_ELECTRONICO"
        else "DIAN 2.1: Factura Electrónica de Venta"
    )

    root.append(cbc("UBLVersionID", "UBL 2.1"))
    root.append(cbc("CustomizationID", "10"))
    root.append(cbc("ProfileID", profile_id))
    root.append(cbc("ProfileExecutionID", tipo_amb))
    root.append(cbc("ID", factura.numero_completo))
    root.append(cbc("UUID", factura.cufe, schemeID=tipo_amb, schemeName="CUDE-SHA384" if type_code == "20" else "CUFE-SHA384"))
    root.append(cbc("IssueDate", factura.fecha_emision.strftime("%Y-%m-%d")))
    hora_dt = factura.created_at or datetime.now()
    root.append(cbc("IssueTime", hora_dt.strftime("%H:%M:%S-05:00")))
    root.append(cbc("InvoiceTypeCode", type_code))
    root.append(cbc("DocumentCurrencyCode", "COP"))

    # Emisor
    supplier = cac("AccountingSupplierParty")
    s_party = cac("Party")
    s_tax = cac("PartyTaxScheme")
    s_tax.append(cbc("RegistrationName", empresa.razon_social))
    s_tax.append(cbc("CompanyID", empresa.nit, schemeID=empresa.dv or "0", schemeName="31"))
    s_party.append(s_tax)
    supplier.append(s_party)
    root.append(supplier)

    # Adquiriente
    customer = cac("AccountingCustomerParty")
    c_party = cac("Party")
    c_tax = cac("PartyTaxScheme")
    c_tax.append(cbc("RegistrationName", tercero.razon_social))
    c_tax.append(cbc("CompanyID", tercero.numero_documento, schemeName="13"))
    c_party.append(c_tax)
    customer.append(c_party)
    root.append(customer)

    # Totales
    base_imp = (factura.subtotal or Decimal("0.00")) - (factura.descuento or Decimal("0.00"))
    monetary = cac("LegalMonetaryTotal")
    monetary.append(cbc("LineExtensionAmount", _fmt_dec(base_imp), currencyID="COP"))
    monetary.append(cbc("TaxExclusiveAmount", _fmt_dec(base_imp), currencyID="COP"))
    monetary.append(cbc("TaxInclusiveAmount", _fmt_dec(factura.total), currencyID="COP"))
    monetary.append(cbc("PayableAmount", _fmt_dec(factura.total), currencyID="COP"))
    root.append(monetary)

    # Líneas
    for idx, it in enumerate(factura.items.all(), 1):
        line = cac("InvoiceLine")
        line.append(cbc("ID", idx))
        line.append(cbc("InvoicedQuantity", f"{it.cantidad:.3f}", unitCode="94"))
        line.append(cbc("LineExtensionAmount", _fmt_dec(it.subtotal - it.descuento), currencyID="COP"))
        item_el = cac("Item")
        item_el.append(cbc("Description", it.descripcion))
        line.append(item_el)
        price_el = cac("Price")
        price_el.append(cbc("PriceAmount", _fmt_dec(it.precio_unitario), currencyID="COP"))
        line.append(price_el)
        root.append(line)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
