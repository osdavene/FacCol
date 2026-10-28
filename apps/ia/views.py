import json
import os
from decimal import Decimal
from datetime import date
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.conf import settings
from django.db import models
from django.db.models import Sum

from apps.ia.models import ConversacionIA, MensajeIA
from apps.facturacion.models import Factura, ReciboCaja
from apps.inventario.models import Producto
from apps.terceros.models import Tercero


@login_required
def asistente_view(request):
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    # Obtener o crear conversación activa
    conversacion = ConversacionIA.objects.filter(
        empresa=empresa,
        usuario=request.user
    ).order_by('-updated_at').first()

    if not conversacion:
        conversacion = ConversacionIA.objects.create(
            empresa=empresa,
            usuario=request.user,
            titulo="Consultas y Análisis FacCol AI"
        )
        # Mensaje de bienvenida
        MensajeIA.objects.create(
            conversacion=conversacion,
            rol='model',
            contenido=(
                f"¡Hola {request.user.first_name or request.user.username}! Soy tu Copilot de Inteligencia Artificial para **{empresa.display_name}**.\n\n"
                "Puedo ayudarte a:\n"
                "- Consultar tus ventas y facturación del mes en tiempo real.\n"
                "- Identificar clientes con saldos pendientes por cobrar.\n"
                "- Alertarte sobre productos con stock bajo o crítico.\n"
                "- Explicarte retenciones e impuestos en Colombia (IVA, ReteFuente, ReteICA).\n\n"
                "¿En qué te puedo colaborar hoy?"
            )
        )

    mensajes = conversacion.mensajes.order_by('created_at')

    return render(request, 'ia/asistente.html', {
        'conversacion': conversacion,
        'mensajes': mensajes,
    })


@login_required
def chat_api(request):
    empresa = request.empresa_activa
    if request.method != 'POST' or not empresa:
        return JsonResponse({'error': 'POST requerido'}, status=400)

    try:
        data = json.loads(request.body)
        pregunta = data.get('mensaje', '').strip()
        conv_id = data.get('conversacion_id')

        if not pregunta:
            return JsonResponse({'error': 'Mensaje vacío'}, status=400)

        conversacion = get_object_or_404(ConversacionIA, id=conv_id, empresa=empresa, usuario=request.user)

        # Guardar mensaje del usuario
        MensajeIA.objects.create(
            conversacion=conversacion,
            rol='user',
            contenido=pregunta
        )

        # Generar respuesta contextual con el motor de ERP o Gemini API
        respuesta_texto = generar_respuesta_ia(pregunta, empresa, request.user)

        # Guardar respuesta del asistente
        MensajeIA.objects.create(
            conversacion=conversacion,
            rol='model',
            contenido=respuesta_texto
        )

        return JsonResponse({
            'success': True,
            'respuesta': respuesta_texto
        })

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def generar_respuesta_ia(pregunta, empresa, user):
    """
    Motor inteligente de IA para ERP:
    1. Si GEMINI_API_KEY está configurada, utiliza Google GenAI SDK con contexto en vivo de la empresa.
    2. Si no, utiliza el motor analítico de datos en vivo de la empresa.
    """
    p_lower = pregunta.lower()

    # Métricas en vivo de la empresa
    hoy = date.today()
    primer_dia_mes = hoy.replace(day=1)

    ventas_mes = Factura.objects.filter(
        empresa=empresa,
        tipo_documento='FACTURA',
        fecha_emision__gte=primer_dia_mes
    ).exclude(estado='ANULADA').aggregate(t=Sum('total'))['t'] or Decimal('0.00')

    cartera_pendiente = Factura.objects.filter(
        empresa=empresa,
        tipo_documento='FACTURA',
        saldo_pendiente__gt=0
    ).exclude(estado='ANULADA').aggregate(t=Sum('saldo_pendiente'))['t'] or Decimal('0.00')

    prods_criticos = list(Producto.objects.filter(
        empresa=empresa,
        maneja_inventario=True,
        stock_actual__lte=models.F('stock_minimo'),
        stock_minimo__gt=0,
        activo=True
    ).values('nombre', 'codigo', 'stock_actual', 'stock_minimo')[:5])

    total_clientes = Tercero.objects.filter(empresa=empresa, es_cliente=True, activo=True).count()

    # Si hay clave de Gemini API configurada, consultar al LLM
    gemini_key = getattr(settings, 'GEMINI_API_KEY', '') or os.getenv('GEMINI_API_KEY', '')
    if gemini_key:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            contexto_sistema = (
                f"Eres el asistente financiero y analítico de la plataforma ERP FacCol en Colombia. "
                f"Empresa activa: {empresa.display_name} (NIT {empresa.nit_completo}). "
                f"Contexto en tiempo real: "
                f"Ventas del mes actual: ${ventas_mes:,.0f} COP. "
                f"Cartera pendiente por cobrar: ${cartera_pendiente:,.0f} COP. "
                f"Total clientes registrados: {total_clientes}. "
                f"Productos con stock bajo: {prods_criticos}. "
                f"Responde de forma clara, profesional, concisa y con formato Markdown amigable."
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"{contexto_sistema}\n\nPregunta del usuario: {pregunta}"
            )
            if response and response.text:
                return response.text
        except Exception:
            pass  # Fallback suave al motor analítico nativo

    # Motor analítico local integrado
    if any(k in p_lower for k in ['venta', 'vendido', 'facturado', 'ingreso']):
        return (
            f"📊 **Resumen de Ventas para {empresa.display_name}:**\n\n"
            f"- **Ventas acumuladas este mes:** `${ventas_mes:,.2f} COP`\n"
            f"- **Cartera pendiente por cobrar:** `${cartera_pendiente:,.2f} COP`\n\n"
            "¿Deseas que preparemos un reporte detallado en PDF de las facturas del período?"
        )

    if any(k in p_lower for k in ['stock', 'inventario', 'producto', 'agotado', 'escaso']):
        if prods_criticos:
            items_txt = "\n".join([f"- **{p['nombre']}** (Cód: `{p['codigo']}`): Stock actual **{p['stock_actual']}** (Mínimo: {p['stock_minimo']})" for p in prods_criticos])
            return (
                f"⚠️ **Alerta de Stock Crítico:**\n\n"
                f"Tienes los siguientes productos en o por debajo del mínimo de seguridad:\n\n"
                f"{items_txt}\n\n"
                "Te sugiero generar una Orden de Compra a tus proveedores para evitar desabastecimiento."
            )
        else:
            return "✅ Todos tus productos se encuentran actualmente con stock por encima de los mínimos configurados."

    if any(k in p_lower for k in ['deuda', 'deben', 'cartera', 'saldo', 'pendiente', 'cobro']):
        facs_pendientes = Factura.objects.filter(
            empresa=empresa,
            saldo_pendiente__gt=0
        ).select_related('tercero')[:5]
        if facs_pendientes.exists():
            detalles = "\n".join([f"- **{f.numero_completo}** | {f.tercero.razon_social}: `${f.saldo_pendiente:,.0f} COP` (Vence: {f.fecha_vencimiento})" for f in facs_pendientes])
            return (
                f"💼 **Cartera por Cobrar:**\n\n"
                f"El saldo total pendiente por cobrar es de **${cartera_pendiente:,.2f} COP**.\n\n"
                f"Facturas pendientes más prioritarias:\n{detalles}\n\n"
                "Puedes registrar los abonos desde **Ventas → Recibos de Caja**."
            )
        else:
            return "🎉 ¡Excelente! No tienes facturas con saldo pendiente por cobrar en este momento."

    if any(k in p_lower for k in ['iva', 'retefuente', 'reteica', 'impuesto', 'retencion', 'dian']):
        return (
            "🏛️ **Guía Tributaria DIAN Colombia:**\n\n"
            "- **IVA General:** 19% aplicable a bienes y servicios gravados.\n"
            "- **ReteFuente:** 2.5% (general para compras de bienes a declarantes de renta) o 3.5% (no declarantes).\n"
            "- **ReteICA:** Varía según el municipio (ej. Bogotá 4.14‰ a 11.04‰ según actividad económica).\n"
            "- **Régimen Simple (RST):** Integra renta, ICA y en algunos casos sustituye aportes parafiscales.\n\n"
            f"Tu empresa `{empresa.display_name}` está configurada bajo el régimen: **{empresa.get_regimen_tributario_display()}**."
        )

    return (
        f"Con gusto puedo ayudarte con cualquier aspecto comercial, contable o tributario de **{empresa.display_name}**.\n\n"
        "Puedes preguntarme por ejemplo:\n"
        "- *¿Cuánto hemos vendido este mes?*\n"
        "- *¿Cuáles productos tienen stock bajo?*\n"
        "- *¿Cuánto dinero tenemos en cartera pendiente de cobro?*\n"
        "- *¿Cuáles son las tarifas de retención en la fuente vigentes?*"
    )
