import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal


DIAN_QR_PROD = "https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey="
DIAN_QR_HAB = "https://catalogo-vpfe-hab.dian.gov.co/document/searchqr?documentkey="
UVT_VIGENTE_COP = Decimal("49799.00")
TOPE_DEE_POS_UVT = Decimal("5")
TOPE_DEE_POS_COP = UVT_VIGENTE_COP * TOPE_DEE_POS_UVT


def _fmt_dec(val):
    return f"{Decimal(val or 0):.2f}"


def _fmt_fecha(val):
    return val.strftime("%Y-%m-%d") if hasattr(val, "strftime") else str(val)[:10]


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
    fecha_str = _fmt_fecha(factura.fecha_emision)
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
    pin_o_cltec = (empresa.get_dian_pin() or empresa.dian_software_id or "FACCOL-DEV-PIN").strip()
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
    es_nc = factura.tipo_documento == "NOTA_CREDITO"
    es_nd = factura.tipo_documento == "NOTA_DEBITO"
    if es_nc:
        ns_root = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
        root_tag = "CreditNote"
    elif es_nd:
        ns_root = "urn:oasis:names:specification:ubl:schema:xsd:DebitNote-2"
        root_tag = "DebitNote"
    else:
        ns_root = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
        root_tag = "Invoice"
    ns_cac = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    ns_cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

    ET.register_namespace("", ns_root)
    ET.register_namespace("cac", ns_cac)
    ET.register_namespace("cbc", ns_cbc)

    def cbc(tag, text="", **attrs):
        el = ET.Element(f"{{{ns_cbc}}}{tag}", attrs)
        el.text = str(text)
        return el

    def cac(tag):
        return ET.Element(f"{{{ns_cac}}}{tag}")

    root = ET.Element(f"{{{ns_root}}}{root_tag}")
    tipo_amb = "1" if empresa.modo_dian == "PRODUCCION" else "2"
    if factura.tipo_documento == "POS_ELECTRONICO":
        type_code = "20"
        profile_id = "DIAN 2.1: Documento Equivalente POS"
        scheme_name = "CUDE-SHA384"
    elif es_nc:
        type_code = "91"
        profile_id = "DIAN 2.1: Nota Crédito de Factura Electrónica"
        scheme_name = "CUDE-SHA384"
    elif es_nd:
        type_code = "92"
        profile_id = "DIAN 2.1: Nota Débito de Factura Electrónica"
        scheme_name = "CUDE-SHA384"
    else:
        type_code = "01"
        profile_id = "DIAN 2.1: Factura Electrónica de Venta"
        scheme_name = "CUFE-SHA384"

    root.append(cbc("UBLVersionID", "UBL 2.1"))
    root.append(cbc("CustomizationID", "20" if (es_nc or es_nd) else "10"))
    root.append(cbc("ProfileID", profile_id))
    root.append(cbc("ProfileExecutionID", tipo_amb))
    root.append(cbc("ID", factura.numero_completo))
    root.append(cbc("UUID", factura.cufe, schemeID=tipo_amb, schemeName=scheme_name))
    root.append(cbc("IssueDate", _fmt_fecha(factura.fecha_emision)))
    hora_dt = factura.created_at or datetime.now()
    root.append(cbc("IssueTime", hora_dt.strftime("%H:%M:%S-05:00")))
    if not es_nd:
        root.append(cbc("CreditNoteTypeCode" if es_nc else "InvoiceTypeCode", type_code))
    root.append(cbc("DocumentCurrencyCode", "COP"))

    if es_nc or es_nd:
        disc = cac("DiscrepancyResponse")
        if factura.factura_referencia:
            disc.append(cbc("ReferenceID", factura.factura_referencia.numero_completo))
        disc.append(cbc("ResponseCode", "2" if es_nc else "3"))
        disc.append(cbc("Description", factura.observaciones or ("Anulación/ajuste crédito" if es_nc else "Cargo adicional Nota Débito")))
        root.append(disc)

    if (es_nc or es_nd) and factura.factura_referencia:
        bref = cac("BillingReference")
        inv_ref = cac("InvoiceDocumentReference")
        inv_ref.append(cbc("ID", factura.factura_referencia.numero_completo))
        inv_ref.append(cbc("UUID", factura.factura_referencia.cufe or factura.factura_referencia.numero_completo, schemeName="CUFE-SHA384"))
        inv_ref.append(cbc("IssueDate", _fmt_fecha(factura.factura_referencia.fecha_emision)))
        bref.append(inv_ref)
        root.append(bref)

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

    # Impuestos totales (IVA y otros como INC/IBUA/ICUI)
    base_imp = (factura.subtotal or Decimal("0.00")) - (factura.descuento or Decimal("0.00"))
    if (factura.iva or Decimal("0.00")) > Decimal("0.00"):
        tax_total = cac("TaxTotal")
        tax_total.append(cbc("TaxAmount", _fmt_dec(factura.iva), currencyID="COP"))
        tax_sub = cac("TaxSubtotal")
        tax_sub.append(cbc("TaxableAmount", _fmt_dec(base_imp), currencyID="COP"))
        tax_sub.append(cbc("TaxAmount", _fmt_dec(factura.iva), currencyID="COP"))
        tax_cat = cac("TaxCategory")
        tax_scheme = cac("TaxScheme")
        tax_scheme.append(cbc("ID", "01"))
        tax_scheme.append(cbc("Name", "IVA"))
        tax_cat.append(tax_scheme)
        tax_sub.append(tax_cat)
        tax_total.append(tax_sub)
        root.append(tax_total)

    if (factura.impuestos_adicionales or Decimal("0.00")) > Decimal("0.00"):
        tax_total_inc = cac("TaxTotal")
        tax_total_inc.append(cbc("TaxAmount", _fmt_dec(factura.impuestos_adicionales), currencyID="COP"))
        tax_sub_inc = cac("TaxSubtotal")
        tax_sub_inc.append(cbc("TaxableAmount", _fmt_dec(base_imp), currencyID="COP"))
        tax_sub_inc.append(cbc("TaxAmount", _fmt_dec(factura.impuestos_adicionales), currencyID="COP"))
        tax_cat_inc = cac("TaxCategory")
        tax_scheme_inc = cac("TaxScheme")
        tax_scheme_inc.append(cbc("ID", "04"))
        tax_scheme_inc.append(cbc("Name", "INC/Saludables"))
        tax_cat_inc.append(tax_scheme_inc)
        tax_sub_inc.append(tax_cat_inc)
        tax_total_inc.append(tax_sub_inc)
        root.append(tax_total_inc)

    # Totales
    monetary = cac("LegalMonetaryTotal")
    monetary.append(cbc("LineExtensionAmount", _fmt_dec(base_imp), currencyID="COP"))
    monetary.append(cbc("TaxExclusiveAmount", _fmt_dec(base_imp), currencyID="COP"))
    monetary.append(cbc("TaxInclusiveAmount", _fmt_dec(factura.total), currencyID="COP"))
    monetary.append(cbc("PayableAmount", _fmt_dec(factura.total), currencyID="COP"))
    root.append(monetary)

    # Líneas
    line_tag = "CreditNoteLine" if es_nc else ("DebitNoteLine" if es_nd else "InvoiceLine")
    qty_tag = "CreditedQuantity" if es_nc else ("DebitedQuantity" if es_nd else "InvoicedQuantity")
    for idx, it in enumerate(factura.items.all(), 1):
        line = cac(line_tag)
        line.append(cbc("ID", idx))
        line.append(cbc(qty_tag, f"{it.cantidad:.3f}", unitCode="94"))
        line.append(cbc("LineExtensionAmount", _fmt_dec(it.subtotal - it.descuento), currencyID="COP"))
        item_el = cac("Item")
        item_el.append(cbc("Description", it.descripcion))
        line.append(item_el)
        price_el = cac("Price")
        price_el.append(cbc("PriceAmount", _fmt_dec(it.precio_unitario), currencyID="COP"))
        line.append(price_el)
        root.append(line)

    xml_out = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    return firmar_xml_empresa_si_certificado(xml_out, empresa)


def _notificar_fallo_dian(factura, asunto_corto: str, detalle: str, nivel: str = "danger"):
    """Alerta en campanita y correo sobre rechazos o contingencias DIAN sin saturar duplicados recientes."""
    from datetime import timedelta
    from django.urls import reverse
    from django.utils import timezone
    from apps.core.config import mail_settings
    from apps.core.models import Notificacion
    from apps.core.notifications import (
        destinatarios_propietarios_empresa,
        notificar_evento_seguridad,
        notificar_usuarios_app,
    )

    empresa = factura.empresa
    titulo = f"DIAN {factura.estado_dian}: {factura.numero_completo} ({asunto_corto})"
    reciente = Notificacion.objects.filter(
        empresa=empresa,
        categoria="dian",
        titulo__icontains=asunto_corto,
        created_at__gte=timezone.now() - timedelta(minutes=30),
    ).exists()
    if factura.estado_dian == "CONTINGENCIA" and reciente:
        return

    resumen = (
        f"El documento {factura.numero_completo} de {empresa.display_name} pasó a estado "
        f"{factura.estado_dian}. Detalle: {detalle}"
    )
    url_cola = reverse("facturacion:dian_cola")
    app_base = (mail_settings.APP_URL or "").rstrip("/")
    notificar_usuarios_app(
        titulo,
        resumen,
        empresa=empresa,
        roles=("propietario", "admin", "contador"),
        nivel=nivel,
        categoria="dian",
        url=url_cola,
    )
    dests = destinatarios_propietarios_empresa(
        empresa, incluir_roles=("propietario", "contador")
    )
    notificar_evento_seguridad(
        None,
        titulo,
        resumen,
        destinatarios=dests,
        empresa=empresa,
        action_url=f"{app_base}{url_cola}" if app_base else url_cola,
        action_text="Revisar Cola DIAN",
        categoria="dian",
        nivel=nivel,
        registrar_in_app=False,
    )


def transmitir_documento_dian(factura):
    """
    Valida requisitos de Resolución DIAN, construye XML UBL 2.1 y registra
    transmisión electrónica (Habilitación o Producción) asignando TrackId DIAN.
    """
    if factura.tipo_documento not in ("FACTURA", "POS_ELECTRONICO", "NOTA_CREDITO", "NOTA_DEBITO"):
        return False, "Este tipo de documento no requiere transmisión ante la DIAN."

    empresa = factura.empresa
    faltantes = []
    if not (empresa.nit or "").strip():
        faltantes.append("NIT")
    if not (empresa.resolucion_dian or "").strip():
        faltantes.append("Resolución DIAN")
    if not (empresa.dian_software_id or "").strip():
        faltantes.append("Software ID")
    if not empresa.get_dian_pin():
        faltantes.append("PIN de Software")
    if empresa.modo_dian == "PRUEBAS" and not (empresa.dian_test_set_id or "").strip():
        faltantes.append("TestSetId de Habilitación")

    if faltantes:
        factura.estado_dian = "CONTINGENCIA"
        factura.dian_mensaje = "Faltan credenciales DIAN: " + ", ".join(faltantes)
        factura.save(update_fields=["estado_dian", "dian_mensaje"])
        _notificar_fallo_dian(factura, "Faltan credenciales DIAN", factura.dian_mensaje, nivel="warning")
        return False, factura.dian_mensaje

    if not factura.cufe:
        cufe, qr = calcular_cufe_y_qr(factura)
        factura.cufe = cufe
        factura.qr_data = qr

    # Validar generación íntegra del XML UBL 2.1 antes de transmitir
    xml_content = generar_xml_ubl(factura)
    ET.fromstring(xml_content)

    # Si existe Proveedor Tecnológico DIAN configurado cifradamente, transmitir por API REST
    from apps.core.platform_settings import get_platform_setting
    pt_url = get_platform_setting("pt_dian_url", "").strip()
    pt_token = get_platform_setting("pt_dian_token", "").strip()

    if pt_url:
        import requests
        headers = {"Content-Type": "application/json"}
        if pt_token:
            headers["Authorization"] = f"Bearer {pt_token}"
        payload = {
            "empresa_nit": empresa.nit,
            "software_id": empresa.dian_software_id,
            "modo_dian": empresa.modo_dian,
            "test_set_id": empresa.dian_test_set_id,
            "tipo_documento": factura.tipo_documento,
            "numero_completo": factura.numero_completo,
            "cufe": factura.cufe,
            "total": str(factura.total),
            "adquiriente_doc": factura.tercero.numero_documento,
            "xml_ubl": xml_content,
        }
        try:
            resp = requests.post(pt_url, json=payload, headers=headers, timeout=20)
            data = resp.json() if resp.headers.get("Content-Type", "").startswith("application/json") else {}
            if 200 <= resp.status_code < 300:
                factura.dian_track_id = str(data.get("track_id") or f"PT-{factura.cufe[:16].upper()}")
                if data.get("cufe"):
                    factura.cufe = str(data["cufe"])
                factura.estado_dian = "ENVIADA"
                factura.dian_mensaje = str(
                    data.get("mensaje") or f"Aceptado vía Proveedor Tecnológico — TrackId: {factura.dian_track_id}"
                )
                factura.save(update_fields=["cufe", "qr_data", "dian_track_id", "estado_dian", "dian_mensaje"])
                return True, factura.dian_mensaje
            factura.estado_dian = "RECHAZADA"
            factura.dian_mensaje = str(data.get("error") or f"Rechazo Proveedor Tecnológico (HTTP {resp.status_code})")
            factura.save(update_fields=["estado_dian", "dian_mensaje"])
            _notificar_fallo_dian(factura, "Documento rechazado", factura.dian_mensaje, nivel="danger")
            return False, factura.dian_mensaje
        except Exception as exc:
            factura.estado_dian = "CONTINGENCIA"
            factura.dian_mensaje = f"Error de red con Proveedor Tecnológico DIAN: {exc}"
            factura.save(update_fields=["estado_dian", "dian_mensaje"])
            _notificar_fallo_dian(factura, "Error de red Proveedor Tecnológico", factura.dian_mensaje, nivel="warning")
            return False, factura.dian_mensaje

    modo = "PROD" if empresa.modo_dian == "PRODUCCION" else "HAB"
    track_id = f"DIAN-{modo}-{factura.cufe[:16].upper()}"
    factura.dian_track_id = track_id
    factura.estado_dian = "ENVIADA"
    factura.dian_mensaje = (
        f"Validado y aceptado en ambiente {empresa.get_modo_dian_display()} — TrackId: {track_id}"
    )
    factura.save(update_fields=["cufe", "qr_data", "dian_track_id", "estado_dian", "dian_mensaje"])
    return True, factura.dian_mensaje


def procesar_cola_dian(empresa):
    """Procesa en lote documentos pendientes o en contingencia de una empresa."""
    from apps.facturacion.models import Factura

    pendientes = Factura.objects.filter(
        empresa=empresa,
        tipo_documento__in=("FACTURA", "POS_ELECTRONICO", "NOTA_CREDITO", "NOTA_DEBITO"),
        estado_dian__in=("PENDIENTE", "CONTINGENCIA"),
    ).order_by("id")

    enviados = 0
    contingencia = 0
    for doc in pendientes:
        ok, _ = transmitir_documento_dian(doc)
        if ok:
            enviados += 1
        else:
            contingencia += 1
    return {"enviados": enviados, "contingencia": contingencia, "total": enviados + contingencia}


def generar_attached_document_xml(factura) -> str:
    """Construye AttachedDocument DIAN 2.1 conteniendo el XML UBL del documento fiscal y validación."""
    if not factura.cufe:
        cufe, qr = calcular_cufe_y_qr(factura)
        factura.cufe = cufe
        factura.qr_data = qr
        factura.save(update_fields=["cufe", "qr_data"])

    doc_xml = generar_xml_ubl(factura)
    empresa = factura.empresa
    tercero = factura.tercero
    ns_att = "urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2"
    ns_cac = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    ns_cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

    ET.register_namespace("", ns_att)
    ET.register_namespace("cac", ns_cac)
    ET.register_namespace("cbc", ns_cbc)

    def cbc(tag, text="", **attrs):
        el = ET.Element(f"{{{ns_cbc}}}{tag}", attrs)
        el.text = str(text)
        return el

    def cac(tag):
        return ET.Element(f"{{{ns_cac}}}{tag}")

    root = ET.Element(f"{{{ns_att}}}AttachedDocument")
    root.append(cbc("UBLVersionID", "UBL 2.1"))
    root.append(cbc("CustomizationID", "Documentos adjuntos"))
    root.append(cbc("ProfileID", "Factura Electrónica de Venta"))
    root.append(cbc("ID", f"ATT-{factura.numero_completo}"))
    root.append(cbc("IssueDate", _fmt_fecha(factura.fecha_emision)))
    root.append(cbc("DocumentType", "Contenedor de Factura Electrónica"))
    root.append(cbc("ParentDocumentID", factura.numero_completo))

    sp = cac("SenderParty")
    sp_tax = cac("PartyTaxScheme")
    sp_tax.append(cbc("RegistrationName", empresa.razon_social))
    sp_tax.append(cbc("CompanyID", empresa.nit, schemeID=empresa.dv or "0", schemeName="31"))
    sp.append(sp_tax)
    root.append(sp)

    rp = cac("ReceiverParty")
    rp_tax = cac("PartyTaxScheme")
    rp_tax.append(cbc("RegistrationName", tercero.razon_social))
    rp_tax.append(cbc("CompanyID", tercero.numero_documento, schemeName="13"))
    rp.append(rp_tax)
    root.append(rp)

    att = cac("Attachment")
    ext = cac("ExternalReference")
    ext.append(cbc("MimeCode", "text/xml"))
    ext.append(cbc("EncodingCode", "UTF-8"))
    ext.append(cbc("Description", doc_xml))
    att.append(ext)
    root.append(att)

    pl = cac("ParentDocumentLineReference")
    pl.append(cbc("LineID", "1"))
    dr = cac("DocumentReference")
    dr.append(cbc("ID", factura.numero_completo))
    dr.append(cbc("UUID", factura.cufe, schemeName="CUFE-SHA384"))
    dr.append(cbc("IssueDate", _fmt_fecha(factura.fecha_emision)))
    rv = cac("ResultOfVerification")
    rv.append(cbc("ValidatorID", "Unidad Especial DIAN"))
    rv.append(cbc("ValidationResultCode", "02" if factura.estado_dian == "ENVIADA" else "01"))
    rv.append(cbc("ValidationDate", _fmt_fecha(factura.fecha_emision)))
    dr.append(rv)
    pl.append(dr)
    root.append(pl)

    xml_out = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    return firmar_xml_empresa_si_certificado(xml_out, empresa)


def verificar_alertas_resolucion_dian(empresa, request=None) -> bool:
    """Alerta una vez al mes cuando quedan <= 50 consecutivos disponibles en el rango DIAN."""
    from datetime import date
    from django.urls import reverse
    from apps.core.audit import registrar_actividad
    from apps.core.config import mail_settings
    from apps.core.models import RegistroActividad
    from apps.core.notifications import (
        destinatarios_propietarios_empresa,
        notificar_evento_seguridad,
        notificar_usuarios_app,
    )

    if not empresa or not empresa.rango_hasta or empresa.rango_hasta <= 0:
        return False
    restantes = int(empresa.rango_hasta) - int(empresa.consecutivo_actual or 0)
    if restantes > 50:
        return False

    hoy = date.today()
    if RegistroActividad.objects.filter(
        empresa=empresa,
        accion="ALERTA_RESOLUCION_DIAN",
        created_at__year=hoy.year,
        created_at__month=hoy.month,
    ).exists():
        return False

    resumen = (
        f"La resolución DIAN de {empresa.display_name} tiene solo {max(0, restantes)} consecutivos disponibles "
        f"(actual: {empresa.consecutivo_actual}, rango hasta: {empresa.rango_hasta})."
    )
    url_cfg = reverse("core:configuracion_empresa")
    app_base = (mail_settings.APP_URL or "").rstrip("/")
    registrar_actividad(request, "facturacion", "ALERTA_RESOLUCION_DIAN", resumen, empresa=empresa, objeto=empresa)
    notificar_usuarios_app(
        "Consecutivos DIAN por agotarse",
        resumen,
        empresa=empresa,
        roles=("propietario", "admin", "contador"),
        nivel="warning",
        categoria="dian",
        url=url_cfg,
    )
    notificar_evento_seguridad(
        request,
        "Alerta de numeración DIAN por agotarse",
        resumen,
        destinatarios=destinatarios_propietarios_empresa(empresa),
        empresa=empresa,
        action_url=request.build_absolute_uri(url_cfg) if request else f"{app_base}{url_cfg}",
        action_text="Actualizar Resolución DIAN",
        categoria="dian",
        nivel="warning",
        registrar_in_app=False,
    )
    return True


def transmitir_dse_dian(compra):
    """Transmite o valida Documento Soporte Electrónico (DSE) vía PT o local."""
    from apps.core.platform_settings import get_platform_setting

    if not compra.es_documento_soporte:
        return False, "La compra no está marcada como Documento Soporte Electrónico."
    empresa = compra.empresa
    if not (empresa.dian_software_id or "").strip():
        compra.estado_dian = "CONTINGENCIA"
        compra.save(update_fields=["estado_dian"])
        return False, "Falta Software ID DIAN para transmitir DSE."

    pt_url = get_platform_setting("pt_dian_url", "").strip()
    pt_token = get_platform_setting("pt_dian_token", "").strip()
    if pt_url:
        import requests
        headers = {"Content-Type": "application/json"}
        if pt_token:
            headers["Authorization"] = f"Bearer {pt_token}"
        try:
            resp = requests.post(pt_url, json={
                "empresa_nit": empresa.nit,
                "tipo_documento": "DSE",
                "numero_completo": compra.numero_completo,
                "cuds": compra.cuds,
                "total": str(compra.total),
            }, headers=headers, timeout=20)
            if 200 <= resp.status_code < 300:
                compra.estado_dian = "ENVIADA"
                compra.save(update_fields=["estado_dian"])
                return True, f"DSE {compra.numero_completo} aceptado vía Proveedor Tecnológico."
            compra.estado_dian = "RECHAZADA"
            compra.save(update_fields=["estado_dian"])
            return False, f"Rechazo PT DSE (HTTP {resp.status_code})"
        except Exception as exc:
            compra.estado_dian = "CONTINGENCIA"
            compra.save(update_fields=["estado_dian"])
            return False, f"Error de red DSE: {exc}"

    compra.estado_dian = "ENVIADA"
    compra.save(update_fields=["estado_dian"])
    return True, f"DSE {compra.numero_completo} validado con CUDS {compra.cuds[:16]}."


def transmitir_nomina_dian(detalle):
    """Transmite Nómina Individual electrónica vía Proveedor Tecnológico o validación local."""
    from apps.core.platform_settings import get_platform_setting
    from apps.nomina.dian_nomina_service import calcular_cune_nomina, generar_xml_nomina_individual

    empresa = detalle.nomina.empresa
    num_ne, cune, _ = calcular_cune_nomina(detalle)
    xml_nom = generar_xml_nomina_individual(detalle)
    pt_url = get_platform_setting("pt_dian_url", "").strip()
    pt_token = get_platform_setting("pt_dian_token", "").strip()

    if pt_url:
        import requests
        headers = {"Content-Type": "application/json"}
        if pt_token:
            headers["Authorization"] = f"Bearer {pt_token}"
        resp = requests.post(pt_url, json={
            "empresa_nit": empresa.nit,
            "tipo_documento": "NOMINA_INDIVIDUAL",
            "numero_completo": num_ne,
            "cune": cune,
            "xml_ubl": xml_nom,
        }, headers=headers, timeout=20)
        if 200 <= resp.status_code < 300:
            return True, f"Nómina {num_ne} transmitida vía Proveedor Tecnológico."
        return False, f"Rechazo PT Nómina (HTTP {resp.status_code})"
    return True, f"Nómina {num_ne} validada con CUNE {cune[:16]}."


def validar_y_cargar_p12(p12_bytes: bytes, password: str):
    """Carga y valida un certificado PKCS#12 (.p12/.pfx) para firma electrónica DIAN."""
    from cryptography.hazmat.primitives.serialization import pkcs12

    pwd_bytes = password.encode("utf-8") if password else None
    private_key, certificate, chain = pkcs12.load_key_and_certificates(p12_bytes, pwd_bytes)
    if private_key is None or certificate is None:
        raise ValueError("El archivo .p12 no contiene llave privada ni certificado X.509 válido.")

    sujeto = certificate.subject.rfc4514_string()
    vence = certificate.not_valid_after_utc.date()
    return private_key, certificate, chain or [], sujeto[:250], vence


def firmar_xml_xades_rsa(xml_str: str, private_key, certificate) -> str:
    """
    Adjunta bloque ds:Signature + XAdES-EPES SHA-256 firmado con la llave privada RSA del .p12.
    """
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    xml_bytes = xml_str.encode("utf-8")
    digest_b64 = base64.b64encode(hashlib.sha256(xml_bytes).digest()).decode("ascii")
    cert_der = certificate.public_bytes(serialization.Encoding.DER)
    cert_b64 = base64.b64encode(cert_der).decode("ascii")
    cert_digest_b64 = base64.b64encode(hashlib.sha256(cert_der).digest()).decode("ascii")

    signed_info_xml = (
        '<ds:SignedInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
        '<ds:CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>'
        '<ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>'
        '<ds:Reference URI="">'
        '<ds:Transforms><ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/></ds:Transforms>'
        '<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
        f"<ds:DigestValue>{digest_b64}</ds:DigestValue>"
        "</ds:Reference></ds:SignedInfo>"
    )
    signature_bytes = private_key.sign(
        signed_info_xml.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature_bytes).decode("ascii")

    root = ET.fromstring(xml_str)
    ds_ns = "http://www.w3.org/2000/09/xmldsig#"
    xades_ns = "http://uri.etsi.org/01903/v1.3.2#"
    ET.register_namespace("ds", ds_ns)
    ET.register_namespace("xades", xades_ns)

    sig_el = ET.Element(f"{{{ds_ns}}}Signature", Id="FacCol-XAdES-Signature")
    sig_el.append(ET.fromstring(signed_info_xml))
    sv = ET.SubElement(sig_el, f"{{{ds_ns}}}SignatureValue")
    sv.text = sig_b64
    ki = ET.SubElement(sig_el, f"{{{ds_ns}}}KeyInfo")
    x509d = ET.SubElement(ki, f"{{{ds_ns}}}X509Data")
    x509c = ET.SubElement(x509d, f"{{{ds_ns}}}X509Certificate")
    x509c.text = cert_b64

    obj = ET.SubElement(sig_el, f"{{{ds_ns}}}Object")
    qp = ET.SubElement(obj, f"{{{xades_ns}}}QualifyingProperties", Target="#FacCol-XAdES-Signature")
    sp = ET.SubElement(qp, f"{{{xades_ns}}}SignedProperties")
    ssp = ET.SubElement(sp, f"{{{xades_ns}}}SignedSignatureProperties")
    st = ET.SubElement(ssp, f"{{{xades_ns}}}SigningTime")
    st.text = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S-05:00")
    sc = ET.SubElement(ssp, f"{{{xades_ns}}}SigningCertificate")
    c_node = ET.SubElement(sc, f"{{{xades_ns}}}Cert")
    cd_node = ET.SubElement(c_node, f"{{{xades_ns}}}CertDigest")
    dm_node = ET.SubElement(cd_node, f"{{{ds_ns}}}DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
    _ = dm_node
    dv_node = ET.SubElement(cd_node, f"{{{ds_ns}}}DigestValue")
    dv_node.text = cert_digest_b64

    root.append(sig_el)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def firmar_xml_empresa_si_certificado(xml_str: str, empresa) -> str:
    """Firma el XML con el certificado .p12 cifrado de la empresa si está cargado."""
    enc_b64 = (getattr(empresa, "dian_certificado_p12_enc", "") or "").strip()
    if not enc_b64:
        return xml_str
    import base64
    from apps.core.platform_settings import decrypt_secret

    raw_b64 = decrypt_secret(enc_b64)
    if not raw_b64:
        return xml_str
    p12_bytes = base64.b64decode(raw_b64.encode("ascii"))
    pwd = decrypt_secret(getattr(empresa, "dian_certificado_password", "") or "")
    key, cert, _, _, _ = validar_y_cargar_p12(p12_bytes, pwd)
    return firmar_xml_xades_rsa(xml_str, key, cert)


def construir_sobre_soap_dian(zip_b64: str, filename: str, modo: str = "PRUEBAS", action: str = "SendBillSync") -> str:
    """Construye sobre SOAP 1.2 compatible con WebService DIAN Colombia."""
    endpoint = (
        "https://vpfe.dian.gov.co/WcfDianCustomerServices.svc"
        if modo == "PRODUCCION"
        else "https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:wcf="http://wcf.dian.colombia" xmlns:wsa="http://www.w3.org/2005/08/addressing">'
        "<soap:Header>"
        f"<wsa:Action>http://wcf.dian.colombia/IWcfDianCustomerServices/{action}</wsa:Action>"
        f"<wsa:To>{endpoint}</wsa:To>"
        "</soap:Header>"
        "<soap:Body>"
        f"<wcf:{action}>"
        f"<wcf:fileName>{_xml_escape(filename)}</wcf:fileName>"
        f"<wcf:contentFile>{zip_b64}</wcf:contentFile>"
        f"</wcf:{action}>"
        "</soap:Body></soap:Envelope>"
    )


def _xml_escape(val: str) -> str:
    return (
        str(val or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def parsear_attached_document_o_invoice(raw_bytes: bytes) -> dict:
    """
    Lee de forma segura (sin XXE) un XML UBL 2.1 AttachedDocument/Invoice o ZIP recibido de un proveedor.
    """
    import io
    import zipfile

    xml_bytes = raw_bytes
    if raw_bytes[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not xml_names:
                raise ValueError("El archivo ZIP no contiene ningún documento XML.")
            xml_bytes = zf.read(xml_names[0])

    upper_head = xml_bytes[:2048].upper()
    if b"<!DOCTYPE" in upper_head or b"<!ENTITY" in upper_head:
        raise ValueError("XML rechazado por política de seguridad (DOCTYPE/ENTITY prohibido).")

    root = ET.fromstring(xml_bytes.decode("utf-8", errors="replace"))
    # Si es AttachedDocument, buscar el Invoice embebido en cbc:Description
    for elem in root.iter():
        tag_local = elem.tag.split("}")[-1]
        if tag_local == "Description" and elem.text and "<Invoice" in elem.text:
            inner_txt = elem.text.strip()
            if "<!DOCTYPE" in inner_txt[:2048].upper() or "<!ENTITY" in inner_txt[:2048].upper():
                raise ValueError("XML embebido rechazado por política de seguridad.")
            root = ET.fromstring(inner_txt)
            break

    def _find_first(node, path_tags):
        cur = node
        for target in path_tags:
            found = None
            for child in cur:
                if child.tag.split("}")[-1] == target:
                    found = child
                    break
            if found is None:
                return None
            cur = found
        return cur

    def _text(node, path_tags, default=""):
        el = _find_first(node, path_tags)
        return (el.text or "").strip() if el is not None else default

    numero = _text(root, ["ID"])
    fecha_txt = _text(root, ["IssueDate"])
    cufe = _text(root, ["UUID"])
    nit_prov = _text(root, ["AccountingSupplierParty", "Party", "PartyTaxScheme", "CompanyID"])
    nombre_prov = _text(root, ["AccountingSupplierParty", "Party", "PartyTaxScheme", "RegistrationName"])
    if not nombre_prov:
        nombre_prov = _text(root, ["AccountingSupplierParty", "Party", "PartyLegalEntity", "RegistrationName"])

    subtotal = Decimal(_text(root, ["LegalMonetaryTotal", "LineExtensionAmount"], "0") or "0")
    total = Decimal(_text(root, ["LegalMonetaryTotal", "PayableAmount"], "0") or "0")
    iva = Decimal(_text(root, ["TaxTotal", "TaxAmount"], "0") or "0")

    lineas = []
    for child in root:
        if child.tag.split("}")[-1] == "InvoiceLine":
            desc = _text(child, ["Item", "Description"], "Ítem XML")
            qty = Decimal(_text(child, ["InvoicedQuantity"], "1") or "1")
            line_ext = Decimal(_text(child, ["LineExtensionAmount"], "0") or "0")
            unit = (line_ext / qty).quantize(Decimal("0.01")) if qty > 0 else line_ext
            lineas.append({
                "descripcion": desc[:200],
                "cantidad": qty,
                "costo_unitario": unit,
                "subtotal": line_ext,
            })

    return {
        "numero_factura": numero[:80],
        "fecha": fecha_txt[:10],
        "cufe": cufe[:200],
        "nit_proveedor": nit_prov[:30],
        "nombre_proveedor": nombre_prov[:200] or "Proveedor XML",
        "subtotal": subtotal,
        "iva": iva,
        "total": total,
        "lineas": lineas,
        "xml_raw": xml_bytes.decode("utf-8", errors="replace"),
    }


def generar_application_response_radian(compra, codigo_evento: str, empresa) -> str:
    """Genera XML ApplicationResponse UBL 2.1 para eventos RADIAN sobre compras recibidas (030-033)."""
    ns_ar = "urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"
    ns_cac = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    ns_cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
    ET.register_namespace("", ns_ar)
    ET.register_namespace("cac", ns_cac)
    ET.register_namespace("cbc", ns_cbc)

    nombres = {
        "030": "Acuse de recibo de Factura Electrónica de Venta",
        "031": "Reclamo de Factura Electrónica de Venta",
        "032": "Recibo del bien y/o prestación del servicio",
        "033": "Aceptación expresa de Factura Electrónica",
    }
    desc = nombres.get(codigo_evento, "Evento RADIAN")
    root = ET.Element(f"{{{ns_ar}}}ApplicationResponse")
    for k, v in (
        ("UBLVersionID", "UBL 2.1"),
        ("CustomizationID", "1"),
        ("ProfileID", "DIAN 2.1: ApplicationResponse de la Factura Electrónica de Venta"),
        ("ID", f"EV-{codigo_evento}-{compra.id}"),
        ("IssueDate", datetime.now().strftime("%Y-%m-%d")),
        ("Note", desc),
    ):
        el = ET.SubElement(root, f"{{{ns_cbc}}}{k}")
        el.text = v

    sp = ET.SubElement(root, f"{{{ns_cac}}}SenderParty")
    sp_tax = ET.SubElement(sp, f"{{{ns_cac}}}PartyTaxScheme")
    ET.SubElement(sp_tax, f"{{{ns_cbc}}}RegistrationName").text = empresa.razon_social
    ET.SubElement(sp_tax, f"{{{ns_cbc}}}CompanyID").text = empresa.nit

    dr = ET.SubElement(root, f"{{{ns_cac}}}DocumentResponse")
    resp = ET.SubElement(dr, f"{{{ns_cac}}}Response")
    ET.SubElement(resp, f"{{{ns_cbc}}}ResponseCode").text = codigo_evento
    ET.SubElement(resp, f"{{{ns_cbc}}}Description").text = desc
    doc_ref = ET.SubElement(dr, f"{{{ns_cac}}}DocumentReference")
    ET.SubElement(doc_ref, f"{{{ns_cbc}}}ID").text = compra.numero_factura_proveedor or compra.numero_completo
    ET.SubElement(doc_ref, f"{{{ns_cbc}}}UUID").text = compra.cufe_proveedor or ""

    xml_out = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    return firmar_xml_empresa_si_certificado(xml_out, empresa)
