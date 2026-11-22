"""API REST v1 multi-tenant autenticada por Bearer Token (ApiTokenEmpresa) + especificación OpenAPI."""

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from functools import wraps

from django.db import transaction
from django.db.models import Sum
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.core.models import ApiTokenEmpresa
from apps.facturacion.dian_service import desglosar_impuestos_item, encolar_documento_dian
from apps.facturacion.models import Factura, FacturaItem
from apps.inventario.models import Producto
from apps.terceros.models import Tercero


def _autenticar_api_token(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        auth = (request.headers.get("Authorization") or "").strip()
        if not auth.lower().startswith("bearer "):
            return JsonResponse({"error": "Authorization Bearer requerido"}, status=401)
        raw_token = auth.split(" ", 1)[1].strip()
        if not raw_token:
            return JsonResponse({"error": "Token vacío"}, status=401)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        tok = (
            ApiTokenEmpresa.objects.select_related("empresa")
            .filter(token_hash=token_hash, activo=True, empresa__activa=True)
            .first()
        )
        if not tok:
            return JsonResponse({"error": "Token API inválido o inactivo"}, status=401)
        tok.ultimo_uso_at = timezone.now()
        tok.save(update_fields=["ultimo_uso_at"])
        request.api_empresa = tok.empresa
        request.api_token = tok
        return view_func(request, *args, **kwargs)

    return _wrapped


def api_v1_openapi(request):
    """Especificación OpenAPI 3.0 pública para integraciones FacCol API v1."""
    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "FacCol ERP API v1",
            "version": "1.0.0",
            "description": "API REST empresarial aislada por token Bearer para facturación, terceros, productos e indicadores.",
        },
        "components": {
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "FacColToken"}
            }
        },
        "security": [{"BearerAuth": []}],
        "paths": {
            "/api/v1/resumen/": {"get": {"summary": "Resumen financiero y operativo de la empresa"}},
            "/api/v1/facturas/": {
                "get": {"summary": "Listar facturas recientes"},
                "post": {"summary": "Emitir nueva factura vía API"},
            },
            "/api/v1/terceros/": {
                "get": {"summary": "Listar terceros (clientes/proveedores)"},
                "post": {"summary": "Crear o actualizar tercero"},
            },
            "/api/v1/productos/": {"get": {"summary": "Listar catálogo de productos y existencias"}},
        },
    }
    return JsonResponse(spec)


@csrf_exempt
@_autenticar_api_token
def api_v1_resumen(request):
    if request.method != "GET":
        return JsonResponse({"error": "Método no permitido"}, status=405)
    empresa = request.api_empresa
    hoy = date.today()
    ventas_mes = (
        Factura.objects.filter(
            empresa=empresa,
            tipo_documento__in=("FACTURA", "POS_ELECTRONICO"),
            fecha_emision__year=hoy.year,
            fecha_emision__month=hoy.month,
        )
        .exclude(estado="ANULADA")
        .aggregate(total=Sum("total"))["total"]
        or Decimal("0.00")
    )
    cartera = (
        Factura.objects.filter(
            empresa=empresa,
            tipo_documento__in=("FACTURA", "POS_ELECTRONICO", "NOTA_DEBITO"),
            saldo_pendiente__gt=0,
        )
        .exclude(estado="ANULADA")
        .aggregate(total=Sum("saldo_pendiente"))["total"]
        or Decimal("0.00")
    )
    return JsonResponse({
        "empresa": empresa.display_name,
        "nit": empresa.nit_completo,
        "ventas_mes_cop": str(ventas_mes),
        "cartera_pendiente_cop": str(cartera),
        "productos_activos": Producto.objects.filter(empresa=empresa, activo=True).count(),
        "terceros_activos": Tercero.objects.filter(empresa=empresa, activo=True).count(),
    })


@csrf_exempt
@_autenticar_api_token
def api_v1_productos(request):
    if request.method != "GET":
        return JsonResponse({"error": "Método no permitido"}, status=405)
    empresa = request.api_empresa
    q = (request.GET.get("q") or "").strip()
    qs = Producto.objects.filter(empresa=empresa, activo=True)
    if q:
        qs = qs.filter(nombre__icontains=q) | qs.filter(codigo__icontains=q)
    data = [
        {
            "id": p.id,
            "codigo": p.codigo,
            "codigo_barras": p.codigo_barras,
            "nombre": p.nombre,
            "precio_venta": str(p.precio_venta),
            "tarifa_iva": str(p.tarifa_iva),
            "stock_actual": str(p.stock_actual),
        }
        for p in qs.order_by("nombre")[:100]
    ]
    return JsonResponse({"results": data})


@csrf_exempt
@_autenticar_api_token
def api_v1_terceros(request):
    empresa = request.api_empresa
    if request.method == "GET":
        qs = Tercero.objects.filter(empresa=empresa, activo=True).order_by("razon_social")[:100]
        return JsonResponse({
            "results": [
                {
                    "id": t.id,
                    "tipo_documento": t.tipo_documento,
                    "numero_documento": t.numero_documento,
                    "razon_social": t.razon_social,
                    "email": t.email,
                    "telefono": t.telefono,
                    "es_cliente": t.es_cliente,
                    "es_proveedor": t.es_proveedor,
                }
                for t in qs
            ]
        })

    if request.method == "POST":
        try:
            body = json.loads(request.body.decode("utf-8"))
        except Exception:
            return JsonResponse({"error": "JSON inválido"}, status=400)
        doc = str(body.get("numero_documento") or "").strip()
        raz = str(body.get("razon_social") or "").strip()
        if not doc or not raz:
            return JsonResponse({"error": "numero_documento y razon_social son obligatorios"}, status=400)
        tercero, created = Tercero.objects.update_or_create(
            empresa=empresa,
            numero_documento=doc,
            defaults={
                "tipo_documento": str(body.get("tipo_documento") or "CC")[:20],
                "tipo_persona": str(body.get("tipo_persona") or "NATURAL")[:20],
                "razon_social": raz[:200],
                "email": str(body.get("email") or "").strip()[:150],
                "telefono": str(body.get("telefono") or "").strip()[:50],
                "direccion": str(body.get("direccion") or "").strip()[:200],
                "es_cliente": bool(body.get("es_cliente", True)),
                "es_proveedor": bool(body.get("es_proveedor", False)),
            },
        )
        return JsonResponse({"id": tercero.id, "created": created, "numero_documento": tercero.numero_documento}, status=201 if created else 200)

    return JsonResponse({"error": "Método no permitido"}, status=405)


@csrf_exempt
@_autenticar_api_token
def api_v1_facturas(request):
    empresa = request.api_empresa
    if request.method == "GET":
        qs = Factura.objects.filter(empresa=empresa).select_related("tercero").order_by("-id")[:100]
        return JsonResponse({
            "results": [
                {
                    "id": f.id,
                    "numero_completo": f.numero_completo,
                    "tipo_documento": f.tipo_documento,
                    "fecha_emision": f.fecha_emision.isoformat(),
                    "tercero": f.tercero.razon_social,
                    "estado": f.estado,
                    "estado_dian": f.estado_dian,
                    "total": str(f.total),
                    "saldo_pendiente": str(f.saldo_pendiente),
                    "cufe": f.cufe,
                }
                for f in qs
            ]
        })

    if request.method == "POST":
        try:
            body = json.loads(request.body.decode("utf-8"))
        except Exception:
            return JsonResponse({"error": "JSON inválido"}, status=400)
        tercero_id = body.get("tercero_id")
        items = body.get("items") or []
        if not tercero_id or not isinstance(items, list) or not items:
            return JsonResponse({"error": "tercero_id e items son obligatorios"}, status=400)
        tercero = Tercero.objects.filter(id=tercero_id, empresa=empresa).first()
        if not tercero:
            return JsonResponse({"error": "Tercero no encontrado en la empresa"}, status=404)

        with transaction.atomic():
            consecutivo = empresa.obtener_siguiente_consecutivo("FACTURA")
            res_act = empresa.obtener_resolucion_activa("FACTURA")
            prefijo = res_act.prefijo if res_act else empresa.prefijo_factura
            fac = Factura.objects.create(
                empresa=empresa,
                tipo_documento="FACTURA",
                prefijo=prefijo,
                consecutivo=consecutivo,
                tercero=tercero,
                fecha_emision=date.today(),
                fecha_vencimiento=date.today() + timedelta(days=empresa.dias_vencimiento or 30),
                estado="EMITIDA",
                metodo_pago=str(body.get("metodo_pago") or "CREDITO"),
                es_fiado=(str(body.get("metodo_pago") or "CREDITO") == "CREDITO"),
                estado_dian="PENDIENTE",
                observaciones=str(body.get("observaciones") or "Emitida vía API REST v1"),
            )
            for it in items:
                prod = Producto.objects.select_for_update().filter(id=it.get("producto_id"), empresa=empresa).first()
                precio_in = it.get("precio_unitario") if it.get("precio_unitario") is not None else (prod.precio_venta if prod else 0)
                precio_base, iva, otro_imp = desglosar_impuestos_item(prod, precio_in, empresa)
                cant = Decimal(str(it.get("cantidad") or 1))
                FacturaItem.objects.create(
                    factura=fac,
                    producto=prod,
                    descripcion=str(it.get("descripcion") or (prod.nombre if prod else "Ítem API")),
                    cantidad=cant,
                    precio_unitario=precio_base,
                    porcentaje_iva=iva,
                    porcentaje_otro_impuesto=otro_imp,
                )
                if prod and prod.maneja_inventario:
                    prod.stock_actual = max(Decimal("0.00"), prod.stock_actual - cant)
                    prod.save(update_fields=["stock_actual"])
            fac.recalcular_totales()
            encolar_documento_dian(fac)

        return JsonResponse({
            "id": fac.id,
            "numero_completo": fac.numero_completo,
            "total": str(fac.total),
            "cufe": fac.cufe,
            "estado_dian": fac.estado_dian,
        }, status=201)

    return JsonResponse({"error": "Método no permitido"}, status=405)
