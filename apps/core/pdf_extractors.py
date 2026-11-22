"""Extractores PDF locales (RUT DIAN y facturas/gastos) usando pypdf sin requerir APIs externas."""

import io
import re
from decimal import Decimal
from pypdf import PdfReader


def extraer_texto_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    chunks = []
    for page in reader.pages[:6]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(chunks)


def extraer_datos_rut_pdf(file_bytes: bytes) -> dict:
    """Extrae NIT, DV, razón social, dirección, teléfono, correo y responsabilidades del PDF del RUT DIAN."""
    texto = extraer_texto_pdf(file_bytes)
    compacto = " ".join(texto.split())

    nit = ""
    dv = ""
    # Patrones comunes en casillas 5 y 6 del Formulario 001 RUT DIAN
    m_nit = re.search(r"(?:NIT|Identificaci[oó]n Tributaria|5\.\s*N[uú]mero)[^\d]{0,40}(\d{6,12})\s*[- ]?\s*(\d)?", texto, re.IGNORECASE)
    if m_nit:
        nit = m_nit.group(1)
        dv = m_nit.group(2) or ""
    if not nit:
        m_alt = re.search(r"\b(\d{8,11})\s*-\s*(\d)\b", compacto)
        if m_alt:
            nit, dv = m_alt.group(1), m_alt.group(2)

    email = ""
    m_mail = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", texto)
    if m_mail:
        email = m_mail.group(1).lower()

    telefono = ""
    m_tel = re.search(r"(?:Tel[eé]fono|Celular|44\.|45\.)[^\d]{0,20}(\d{7,12})", texto, re.IGNORECASE)
    if m_tel:
        telefono = m_tel.group(1)

    razon_social = ""
    m_raz = re.search(
        r"(?:35\.\s*Raz[oó]n social|Raz[oó]n\s+social)\s*[:\-]?\s*\n?\s*([A-ZÁÉÍÓÚÑ0-9 .,&-]{4,120})",
        texto,
        re.IGNORECASE,
    )
    if m_raz:
        razon_social = m_raz.group(1).strip().splitlines()[0].strip()

    direccion = ""
    m_dir = re.search(
        r"(?:41\.\s*Direcci[oó]n principal|Direcci[oó]n principal)\s*[:\-]?\s*\n?\s*([A-ZÁÉÍÓÚÑ0-9 #.,-]{5,120})",
        texto,
        re.IGNORECASE,
    )
    if m_dir:
        direccion = m_dir.group(1).strip().splitlines()[0].strip()

    resps = sorted(set(re.findall(r"\b(?:O-\d{2}|R-99-PN)\b", texto.upper())))
    tipo_persona = "JURIDICA" if ("O-13" in resps or "SAS" in razon_social.upper() or "LTDA" in razon_social.upper() or "S.A" in razon_social.upper()) else "NATURAL"
    regimen = "RESPONSABLE_IVA" if ("O-48" in resps or "48 -" in texto) else ("REGIMEN_SIMPLE" if "O-47" in resps else "NO_RESPONSABLE_IVA")

    return {
        "nit": nit,
        "dv": dv,
        "razon_social": razon_social[:200],
        "direccion": direccion[:200],
        "telefono": telefono[:30],
        "email": email[:150],
        "responsabilidades": ", ".join(resps),
        "tipo_persona": tipo_persona,
        "regimen": regimen,
        "texto_detectado": bool(texto.strip()),
    }


def _parse_money(raw: str) -> Decimal:
    clean = re.sub(r"[^\d.,]", "", raw or "")
    if not clean:
        return Decimal("0.00")
    if clean.count(".") >= 1 and clean.count(",") == 1 and clean.rfind(",") > clean.rfind("."):
        clean = clean.replace(".", "").replace(",", ".")
    elif clean.count(",") >= 1 and "." not in clean:
        parts = clean.split(",")
        if len(parts[-1]) == 2:
            clean = clean.replace(",", ".")
        else:
            clean = clean.replace(",", "")
    else:
        parts = clean.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            clean = clean.replace(".", "")
    try:
        return Decimal(clean).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def extraer_datos_factura_pdf(file_bytes: bytes) -> dict:
    """Extrae NIT, soporte, subtotal, IVA, total y sugiere cuenta PUC para causar gasto o compra."""
    texto = extraer_texto_pdf(file_bytes)
    low = texto.lower()

    nit = ""
    m_nit = re.search(r"(?:NIT|N\.I\.T\.?)\s*[:#]?\s*(\d{7,12})", texto, re.IGNORECASE)
    if m_nit:
        nit = m_nit.group(1)

    num_soporte = ""
    m_num = re.search(r"(?:Factura(?:\s+Electr[oó]nica)?|No\.?|Nro\.?)\s*[:#]?\s*([A-Z]{1,6}[- ]?\d{1,12})", texto, re.IGNORECASE)
    if m_num:
        num_soporte = m_num.group(1).replace(" ", "")

    total = Decimal("0.00")
    m_tot = re.search(r"(?:Total(?:\s+a\s+Pagar|\s+Factura)?)\s*[:$]?\s*\$?\s*([\d.,]{3,20})", texto, re.IGNORECASE)
    if m_tot:
        total = _parse_money(m_tot.group(1))

    iva = Decimal("0.00")
    m_iva = re.search(r"(?:IVA(?:\s+19%|\s+5%)?|Impuesto\s+IVA)\s*[:$]?\s*\$?\s*([\d.,]{2,20})", texto, re.IGNORECASE)
    if m_iva:
        iva = _parse_money(m_iva.group(1))

    subtotal = max(Decimal("0.00"), total - iva) if total >= iva else Decimal("0.00")

    puc_sugerida = "5195"
    concepto = "Gasto según soporte PDF"
    if any(k in low for k in ("arriendo", "arrendamiento", "canon")):
        puc_sugerida = "5120"
        concepto = "Arrendamiento según soporte PDF"
    elif any(k in low for k in ("honorario", "asesoria", "asesoría", "consultoria")):
        puc_sugerida = "5110"
        concepto = "Honorarios profesionales"
    elif any(k in low for k in ("energia", "acueducto", "internet", "telefonia", "servicio p", "aws", "cloud")):
        puc_sugerida = "5135"
        concepto = "Servicios públicos / tecnológicos"
    elif any(k in low for k in ("papeleria", "papelería", "aseo", "cafeteria")):
        puc_sugerida = "5195"
        concepto = "Útiles, papelería y aseo"

    return {
        "nit": nit,
        "numero_soporte": num_soporte[:60],
        "subtotal": subtotal,
        "iva": iva,
        "total": total,
        "puc_sugerida": puc_sugerida,
        "concepto": concepto,
        "texto_detectado": bool(texto.strip()),
    }
