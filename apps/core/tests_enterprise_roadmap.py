import base64
from datetime import date, datetime, timedelta, timezone as dt_tz
from decimal import Decimal
from io import BytesIO

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from reportlab.pdfgen import canvas

from apps.contabilidad.models import ActivoFijo, AsientoContable, CentroCosto, CuentaBancaria, GastoOperativo, MovimientoExtractoBancario, PlanCuenta
from apps.core.models import ApiTokenEmpresa
from apps.core.pdf_extractors import extraer_datos_factura_pdf, extraer_datos_rut_pdf
from apps.empresas.models import Empresa, Modulo, Plan, ResolucionDIAN
from apps.facturacion.dian_service import construir_sobre_soap_dian, generar_xml_ubl, parsear_attached_document_o_invoice
from apps.facturacion.models import Factura, FacturaRecurrente
from apps.inventario.models import CompraProveedor, ListaPrecio, LoteProducto, OrdenCompra, PrecioProducto, Producto
from apps.nomina.models import Empleado, Nomina, NominaAjuste
from apps.terceros.models import Tercero
from apps.usuarios.models import EmpresaUsuario, User
from apps.usuarios.totp import generar_codigo_totp


def _crear_p12_prueba(password: str = "claveP12!") -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Empresa Pruebas DIAN SAS"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CO"),
    ])
    now = datetime.now(dt_tz.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        name=b"faccol-test",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")),
    )


def _crear_pdf_texto(lineas: list[str]) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf)
    y = 750
    for ln in lineas:
        c.drawString(50, y, ln)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


class EnterpriseRoadmapTests(TestCase):
    def setUp(self):
        for cod, nom in (
            ("facturacion", "Facturación"),
            ("pos", "POS"),
            ("inventario", "Inventario"),
            ("contabilidad", "Contabilidad"),
            ("nomina", "Nómina"),
            ("ia", "IA"),
        ):
            Modulo.objects.get_or_create(codigo=cod, defaults={"nombre": nom, "activo_por_defecto": True})

        self.plan = Plan.objects.create(nombre="Enterprise", precio_mensual=Decimal("199000.00"))
        self.plan.modulos.set(Modulo.objects.all())

        self.empresa = Empresa.objects.create(
            razon_social="Corporacion Enterprise SAS",
            nit="901888777",
            dv="1",
            email="gerencia@enterprise.co",
            plan=self.plan,
            resolucion_dian="187640999888",
            dian_software_id="SW-ENT-01",
            dian_pin="99887",
            dian_test_set_id="TS-ENT-01",
            exonerado_art_114_1=True,
        )
        self.user = User.objects.create_user(
            username="admin@enterprise.co",
            email="admin@enterprise.co",
            password="SafePass!2026",
            first_name="Carlos",
            email_verified_at=datetime.now(dt_tz.utc),
        )
        EmpresaUsuario.objects.create(user=self.user, empresa=self.empresa, rol="propietario", activo=True)
        self.cliente = Tercero.objects.create(
            empresa=self.empresa, tipo_documento="NIT", numero_documento="800123456", dv="7",
            razon_social="Cliente Corporativo SAS", email="cliente@corp.co", es_cliente=True,
        )
        self.prod = Producto.objects.create(
            empresa=self.empresa, codigo="SRV-1", nombre="Licencia Anual",
            precio_venta=Decimal("200000.00"), costo_unitario=Decimal("80000.00"),
            tarifa_iva=Decimal("19.00"), stock_actual=Decimal("20.00"),
        )
        PlanCuenta.objects.create(empresa=self.empresa, codigo="111005", nombre="Bancolombia", nivel=6, naturaleza="DEBITO", permite_movimiento=True)
        PlanCuenta.objects.create(empresa=self.empresa, codigo="519505", nombre="Gastos Generales", nivel=6, naturaleza="DEBITO", permite_movimiento=True)

    def test_01_2fa_totp_flujo_completo(self):
        c = Client()
        c.force_login(self.user)
        # Iniciar QR, obtener secreto de sesión y activar con código TOTP válido
        c.post(reverse("usuarios:perfil_2fa"), {"accion": "iniciar"})
        secret = c.session.get("pending_totp_secret")
        self.assertTrue(secret)
        code_ok = generar_codigo_totp(secret)
        c.post(reverse("usuarios:perfil_2fa"), {"accion": "activar", "codigo": code_ok})
        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_enabled)

        # Cerrar sesión e iniciar con 2FA
        c.logout()
        r_login = c.post(reverse("usuarios:login"), {"login": "admin@enterprise.co", "password": "SafePass!2026"})
        self.assertRedirects(r_login, reverse("usuarios:verificar_2fa"), fetch_redirect_response=False)

        # Código malo falla, código bueno autentica
        r_bad = c.post(reverse("usuarios:verificar_2fa"), {"codigo": "000000"})
        self.assertEqual(r_bad.status_code, 200)
        self.assertFalse(c.session.get("_auth_user_id"))

        r_ok = c.post(reverse("usuarios:verificar_2fa"), {"codigo": generar_codigo_totp(secret)})
        self.assertEqual(r_ok.status_code, 302)
        self.assertTrue(c.session.get("_auth_user_id"))

        # Desactivar con contraseña correcta
        c.post(reverse("usuarios:perfil_2fa"), {"accion": "desactivar", "password_confirm": "SafePass!2026"})
        self.user.refresh_from_db()
        self.assertFalse(self.user.totp_enabled)

    def test_02_p12_xades_multiresolucion_soap_y_api_rest_v1(self):
        c = Client()
        c.force_login(self.user)

        # Cargar certificado .p12 cifrado en BD
        p12_bytes = _crear_p12_prueba("claveP12!")
        c.post(reverse("core:configuracion_empresa"), {
            "seccion": "certificado_p12",
            "archivo_p12": SimpleUploadedFile("firma.p12", p12_bytes, content_type="application/x-pkcs12"),
            "password_p12": "claveP12!",
        })
        self.empresa.refresh_from_db()
        self.assertTrue(self.empresa.dian_certificado_p12_enc.startswith("enc:"))
        self.assertIn("Empresa Pruebas DIAN SAS", self.empresa.dian_certificado_sujeto)

        # Crear Resolución DIAN específica
        c.post(reverse("core:configuracion_empresa"), {
            "seccion": "resolucion_dian",
            "tipo_documento": "FACTURA",
            "prefijo": "FENT",
            "numero_resolucion": "18764000111222",
            "rango_desde": "100",
            "rango_hasta": "5000",
            "consecutivo_actual": "100",
            "predeterminada": "on",
        })
        self.assertTrue(ResolucionDIAN.objects.filter(empresa=self.empresa, prefijo="FENT").exists())

        # Generar Token API REST v1 y emitir factura vía API usando resolución FENT-100
        c.post(reverse("core:configuracion_empresa"), {"seccion": "api_token", "nombre_token": "ERP Sync"})
        raw_tok = c.session.get("ultimo_api_token_plano")
        self.assertTrue(raw_tok and raw_tok.startswith("fc_live_"))

        anon = Client()
        self.assertEqual(anon.get(reverse("api_v1_resumen")).status_code, 401)
        self.assertEqual(anon.get(reverse("api_v1_openapi")).status_code, 200)

        r_fac_api = anon.post(
            reverse("api_v1_facturas"),
            data='{"tercero_id": %d, "items": [{"producto_id": %d, "cantidad": 2}]}' % (self.cliente.id, self.prod.id),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw_tok}",
        )
        self.assertEqual(r_fac_api.status_code, 201)
        data_api = r_fac_api.json()
        self.assertEqual(data_api["numero_completo"], "FENT100")

        fac = Factura.objects.get(id=data_api["id"])
        xml_signed = generar_xml_ubl(fac)
        self.assertIn("Signature", xml_signed)
        self.assertIn("QualifyingProperties", xml_signed)
        soap = construir_sobre_soap_dian(base64.b64encode(xml_signed.encode("utf-8")).decode("ascii"), "FENT100.zip")
        self.assertIn("SendBillSync", soap)

    def test_03_recepcion_xml_xxe_radian_oc_lotes_listas_precios_y_recurrente(self):
        c = Client()
        c.force_login(self.user)

        # XXE rechazado
        xxe = b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><Invoice></Invoice>'
        with self.assertRaises(ValueError):
            parsear_attached_document_o_invoice(xxe)

        # XML proveedor válido
        ubl_prov = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Invoice xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" '
            'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">'
            '<cbc:ID>PROV-55</cbc:ID><cbc:UUID>cufe-prov-xyz</cbc:UUID><cbc:IssueDate>2026-11-22</cbc:IssueDate>'
            '<cac:AccountingSupplierParty><cac:Party><cac:PartyTaxScheme>'
            '<cbc:RegistrationName>Distribuidora XML SAS</cbc:RegistrationName><cbc:CompanyID>900555444</cbc:CompanyID>'
            '</cac:PartyTaxScheme></cac:Party></cac:AccountingSupplierParty>'
            '<cac:LegalMonetaryTotal><cbc:LineExtensionAmount>100000</cbc:LineExtensionAmount>'
            '<cbc:PayableAmount>119000</cbc:PayableAmount></cac:LegalMonetaryTotal>'
            '<cac:TaxTotal><cbc:TaxAmount>19000</cbc:TaxAmount></cac:TaxTotal>'
            '</Invoice>'
        ).encode("utf-8")
        c.post(reverse("inventario:compra_importar_xml"), {
            "archivo_xml": SimpleUploadedFile("prov55.xml", ubl_prov, content_type="application/xml"),
            "metodo_pago": "CREDITO",
        }, follow=True)
        compra = CompraProveedor.objects.filter(empresa=self.empresa, numero_factura_proveedor="PROV-55").first()
        self.assertIsNotNone(compra)
        c.post(reverse("inventario:compra_evento_radian", args=[compra.id]), {"codigo_evento": "030"})
        compra.refresh_from_db()
        self.assertIn("030", compra.radian_estado)

        # Orden de compra -> Compra
        c.post(reverse("inventario:ordenes_compra_lista"), {
            "proveedor": str(self.cliente.id), "producto": str(self.prod.id), "cantidad": "3", "costo_unitario": "75000",
        })
        oc = OrdenCompra.objects.filter(empresa=self.empresa).first()
        c.post(reverse("inventario:orden_compra_convertir", args=[oc.id]))
        oc.refresh_from_db()
        self.assertEqual(oc.estado, "RECIBIDA")
        self.assertIsNotNone(oc.compra_generada)

        # Lotes y Lista de Precios aplicada en facturación
        c.post(reverse("inventario:lotes_lista"), {
            "producto": str(self.prod.id), "codigo_lote": "LOT-A1", "fecha_vencimiento": (date.today() + timedelta(days=10)).isoformat(), "cantidad": "5",
        })
        self.assertTrue(LoteProducto.objects.filter(empresa=self.empresa, codigo_lote="LOT-A1").exists())

        lp = ListaPrecio.objects.create(empresa=self.empresa, nombre="VIP")
        PrecioProducto.objects.create(lista=lp, producto=self.prod, precio=Decimal("150000.00"))
        self.cliente.lista_precio = lp
        self.cliente.save(update_fields=["lista_precio"])

        c.post(reverse("facturacion:factura_crear"), {
            "tercero": str(self.cliente.id),
            "tipo_documento": "FACTURA",
            "metodo_pago": "CREDITO",
            "item_producto[]": [str(self.prod.id)],
            "item_cantidad[]": ["1"],
            "item_precio[]": ["0"],
        })
        fac_vip = Factura.objects.filter(empresa=self.empresa).latest("id")
        self.assertEqual(fac_vip.subtotal, Decimal("150000.00"))

        # Facturación recurrente + comando
        FacturaRecurrente.objects.create(
            empresa=self.empresa, tercero=self.cliente, nombre_contrato="Soporte Mensual",
            proxima_fecha=date.today(), items_json=[{"producto_id": self.prod.id, "cantidad": 1, "precio_unitario": "100000"}],
        )
        call_command("procesar_facturas_recurrentes")
        self.assertTrue(Factura.objects.filter(empresa=self.empresa, observaciones__icontains="Soporte Mensual").exists())

    def test_04_contabilidad_bancos_activos_tributario_pdf_y_nomina_completa(self):
        c = Client()
        c.force_login(self.user)

        # PDF RUT y PDF Gasto
        pdf_rut = _crear_pdf_texto(["5. Numero Identificacion Tributaria NIT: 901222333 - 4", "35. Razon social: TRANSPORTES ANDES SAS", "Correo: contacto@andes.co"])
        r_rut = extraer_datos_rut_pdf(pdf_rut)
        self.assertEqual(r_rut["nit"], "901222333")
        self.assertEqual(r_rut["email"], "contacto@andes.co")

        pdf_fac = _crear_pdf_texto(["NIT: 890111222", "Factura Electronica No. FE-881", "Servicio de arrendamiento oficina", "IVA 19%: $190.000", "Total a Pagar: $1.190.000"])
        r_g = extraer_datos_factura_pdf(pdf_fac)
        self.assertEqual(r_g["total"], Decimal("1190000.00"))
        self.assertEqual(r_g["puc_sugerida"], "5120")

        # Centro de costos, conciliación bancaria y activos fijos
        c.post(reverse("contabilidad:centros_costos"), {"codigo": "CC-NORTE", "nombre": "Sede Norte"})
        self.assertTrue(CentroCosto.objects.filter(empresa=self.empresa, codigo="CC-NORTE").exists())

        cta_bco = PlanCuenta.objects.get(empresa=self.empresa, codigo="111005")
        c.post(reverse("contabilidad:conciliacion_bancaria"), {
            "accion": "crear_cuenta", "nombre": "Bancolombia Principal", "banco": "Bancolombia", "cuenta_contable": str(cta_bco.id),
        })
        cb = CuentaBancaria.objects.get(empresa=self.empresa, nombre="Bancolombia Principal")
        csv_ext = b"fecha,descripcion,referencia,valor\n2026-11-22,Pago Cliente,RC1,250000\n"
        c.post(reverse("contabilidad:conciliacion_bancaria"), {
            "accion": "importar_extracto", "cuenta_bancaria": str(cb.id),
            "archivo": SimpleUploadedFile("extracto.csv", csv_ext, content_type="text/csv"),
        })
        self.assertTrue(MovimientoExtractoBancario.objects.filter(empresa=self.empresa, valor=Decimal("250000")).exists())

        c.post(reverse("contabilidad:activos_fijos"), {
            "codigo": "AF-1", "nombre": "Servidor Cloud Local", "fecha_compra": "2026-01-01",
            "valor_compra": "6000000", "valor_residual": "0", "vida_util_meses": "60",
        })
        c.post(reverse("contabilidad:activos_fijos"), {"accion": "depreciar", "ano": "2026", "mes": "11"})
        act = ActivoFijo.objects.get(empresa=self.empresa, codigo="AF-1")
        self.assertEqual(act.depreciacion_acumulada, Decimal("100000.00"))
        self.assertTrue(AsientoContable.objects.filter(empresa=self.empresa, referencia_origen="DEP-202611").exists())

        # Formularios 300/350 y Exógena XML
        GastoOperativo.objects.create(
            empresa=self.empresa, consecutivo=1, tercero=self.cliente,
            cuenta_gasto=PlanCuenta.objects.get(empresa=self.empresa, codigo="519505"),
            fecha=date(2026, 11, 10), concepto="Servicios", subtotal=Decimal("1000000"), retencion_fuente=Decimal("40000"),
            total_pagar=Decimal("960000"),
        )
        self.assertEqual(c.get(reverse("contabilidad:formularios_tributarios")).status_code, 200)
        r_xml = c.get(reverse("contabilidad:exogena_xml"), {"ano": "2026", "formato": "1001"})
        self.assertEqual(r_xml.status_code, 200)
        self.assertIn(b"<Formato>1001</Formato>", r_xml.content)

        # Nómina con FSP + ReteFuente salarios Art. 383 + Provisiones contables + PILA 2388 + Nota Ajuste 103 + Prestaciones
        emp_alto = Empleado.objects.create(
            empresa=self.empresa, numero_documento="79555666", nombres="Elena", apellidos="Directiva",
            cargo="Gerente", salario_base=Decimal("12000000.00"), tiene_auxilio_transporte=False,
            fecha_ingreso=date(2025, 1, 1),
        )
        c.post(reverse("nomina:nomina_liquidar"), {"periodo_mes": "11", "periodo_ano": "2026"})
        nom = Nomina.objects.get(empresa=self.empresa, periodo_mes=11, periodo_ano=2026)
        det = nom.detalles.get(empleado=emp_alto)
        self.assertGreater(det.fondo_solidaridad, Decimal("0.00"))
        self.assertGreater(det.retefuente_salarios, Decimal("0.00"))
        asiento_nom = AsientoContable.objects.get(empresa=self.empresa, referencia_origen="NOM-202611")
        self.assertTrue(asiento_nom.esta_cuadrado)
        self.assertTrue(asiento_nom.lineas.filter(cuenta__codigo="261005").exists())

        r_pila_txt = c.get(reverse("nomina:nomina_exportar_pila_2388", args=[nom.id]))
        self.assertEqual(r_pila_txt.status_code, 200)
        self.assertIn(b"0200001", r_pila_txt.content)

        c.post(reverse("nomina:nomina_nota_ajuste_crear", args=[det.id]), {"tipo_nota": "1", "motivo": "Corrección bono"})
        self.assertTrue(NominaAjuste.objects.filter(empresa=self.empresa, nomina_detalle=det).exists())

        r_prest = c.post(reverse("nomina:liquidacion_prestaciones"), {
            "empleado": str(emp_alto.id), "fecha_inicio": "2026-01-01", "fecha_fin": "2026-11-22",
        })
        self.assertEqual(r_prest.status_code, 200)
        self.assertContains(r_prest, "Total Liquidaci")
