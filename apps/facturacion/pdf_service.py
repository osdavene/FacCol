import io
import qrcode
from decimal import Decimal
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


def generar_pdf_factura(factura):
    """
    Genera el archivo PDF oficial para una Factura Electrónica colombiana con código QR DIAN.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    normal_style = styles['Normal']
    normal_style.fontSize = 9
    normal_style.leading = 12

    bold_style = ParagraphStyle(
        'BoldStyle',
        parent=normal_style,
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12
    )

    title_style = ParagraphStyle(
        'TitleStyle',
        parent=normal_style,
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=16,
        textColor=colors.HexColor('#0f172a')
    )

    story = []
    empresa = factura.empresa
    tercero = factura.tercero

    # 1. ENCABEZADO
    header_data = [
        [
            Paragraph(f"<b>{empresa.razon_social}</b><br/>"
                      f"NIT: {empresa.nit_completo}<br/>"
                      f"{empresa.direccion}<br/>"
                      f"{empresa.municipio.nombre if empresa.municipio else ''} - Tel: {empresa.telefono}<br/>"
                      f"{empresa.email}", normal_style),
            Paragraph(f"<font color='#d97706'><b>{factura.get_tipo_documento_display().upper()}</b></font><br/>"
                      f"<b>No. {factura.numero_completo}</b><br/>"
                      f"Fecha Emisión: {factura.fecha_emision}<br/>"
                      f"Fecha Vencimiento: {factura.fecha_vencimiento}<br/>"
                      f"Forma de Pago: {factura.get_metodo_pago_display()}", bold_style)
        ]
    ]
    t_header = Table(header_data, colWidths=[320, 220])
    t_header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(t_header)
    story.append(Spacer(1, 10))

    # 2. DATOS DEL CLIENTE
    cliente_data = [
        [
            Paragraph("<b>DATOS DEL CLIENTE / ADQUIRIENTE</b>", bold_style),
            Paragraph("<b>INFORMACIÓN FISCAL DIAN</b>", bold_style)
        ],
        [
            Paragraph(f"<b>Razón Social:</b> {tercero.razon_social}<br/>"
                      f"<b>Identificación:</b> {tercero.tipo_documento} {tercero.documento_completo}<br/>"
                      f"<b>Dirección:</b> {tercero.direccion}<br/>"
                      f"<b>Teléfono:</b> {tercero.telefono} - <b>Email:</b> {tercero.email}", normal_style),
            Paragraph(f"<b>Resolución DIAN:</b> {empresa.resolucion_dian or 'Habilitación'}<br/>"
                      f"<b>Rango Autorizado:</b> {empresa.prefijo_factura} {empresa.rango_desde} al {empresa.rango_hasta}<br/>"
                      f"<b>Régimen:</b> {empresa.get_regimen_tributario_display()}<br/>"
                      f"<b>CUFE:</b> {factura.cufe[:24] if factura.cufe else 'N/A'}...", normal_style)
        ]
    ]
    t_cliente = Table(cliente_data, colWidths=[320, 220])
    t_cliente.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
    ]))
    story.append(t_cliente)
    story.append(Spacer(1, 12))

    # 3. TABLA DE ÍTEMS / DETALLE
    items_header = [
        Paragraph("<b>#</b>", bold_style),
        Paragraph("<b>Descripción</b>", bold_style),
        Paragraph("<b>Cant.</b>", bold_style),
        Paragraph("<b>Precio Unit.</b>", bold_style),
        Paragraph("<b>Desc.</b>", bold_style),
        Paragraph("<b>IVA</b>", bold_style),
        Paragraph("<b>Total</b>", bold_style)
    ]
    table_rows = [items_header]

    for idx, it in enumerate(factura.items.all(), 1):
        table_rows.append([
            Paragraph(str(idx), normal_style),
            Paragraph(it.descripcion, normal_style),
            Paragraph(f"{it.cantidad:,.2f}", normal_style),
            Paragraph(f"${it.precio_unitario:,.0f}", normal_style),
            Paragraph(f"${it.descuento:,.0f}", normal_style),
            Paragraph(f"{it.porcentaje_iva:.0f}%", normal_style),
            Paragraph(f"<b>${it.total:,.0f}</b>", normal_style)
        ])

    t_items = Table(table_rows, colWidths=[25, 215, 45, 75, 55, 45, 80])
    t_items.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('PADDING', (0, 0), (-1, -1), 5),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(t_items)
    story.append(Spacer(1, 10))

    # 4. CÓDIGO QR Y TOTALES
    # Generar código QR
    qr_img_data = factura.qr_data or f"NumFac={factura.numero_completo}&Total={factura.total}&Nit={empresa.nit}"
    qr = qrcode.QRCode(box_size=3, border=1)
    qr.add_data(qr_img_data)
    qr.make(fit=True)
    img_qr = qr.make_image(fill_color="black", back_color="white")
    qr_buffer = io.BytesIO()
    img_qr.save(qr_buffer, format='PNG')
    qr_buffer.seek(0)
    reportlab_qr = Image(qr_buffer, width=80, height=80)

    totales_data = [
        [reportlab_qr, Paragraph(f"<b>Subtotal:</b>", normal_style), Paragraph(f"${factura.subtotal:,.2f}", normal_style)],
        ["", Paragraph(f"<b>Descuentos:</b>", normal_style), Paragraph(f"-${factura.descuento:,.2f}", normal_style)],
        ["", Paragraph(f"<b>IVA (Impuestos):</b>", normal_style), Paragraph(f"+${factura.iva:,.2f}", normal_style)],
        ["", Paragraph(f"<b>Retenciones:</b>", normal_style), Paragraph(f"-${factura.retenciones:,.2f}", normal_style)],
        ["", Paragraph(f"<b>TOTAL A PAGAR:</b>", bold_style), Paragraph(f"<b>${factura.total:,.2f} COP</b>", bold_style)],
        ["", Paragraph(f"<b>Saldo Pendiente:</b>", normal_style), Paragraph(f"<b>${factura.saldo_pendiente:,.2f} COP</b>", normal_style)],
    ]
    t_totales = Table(totales_data, colWidths=[280, 140, 120])
    t_totales.setStyle(TableStyle([
        ('SPAN', (0, 0), (0, 5)),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('PADDING', (0, 0), (-1, -1), 3),
        ('LINEABOVE', (1, 4), (2, 4), 1, colors.HexColor('#0f172a')),
    ]))
    story.append(t_totales)

    # 5. OBSERVACIONES Y PIE LEGAL
    story.append(Spacer(1, 15))
    if factura.observaciones:
        story.append(Paragraph(f"<b>Observaciones:</b> {factura.observaciones}", normal_style))
        story.append(Spacer(1, 5))

    pie_legal = (
        "Esta factura electrónica de venta constituye título valor en los términos de la Ley 1231 de 2008 y normas concordantes de Colombia. "
        "Firma digital y representación gráfica autorizada por la DIAN."
    )
    story.append(Paragraph(f"<font color='#64748b' size='7'>{pie_legal}</font>", normal_style))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
