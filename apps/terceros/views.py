from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.views.decorators.http import require_POST

from apps.core.audit import registrar_actividad
from apps.core.importers import leer_filas_archivo
from apps.core.pdf_extractors import extraer_datos_rut_pdf
from apps.terceros.models import Tercero
from apps.empresas.models import Departamento, Municipio


def _tiene_permiso_terceros(user, empresa):
    return bool(
        empresa and (user.es_admin(empresa) or user.puede_facturar(empresa) or user.puede_contabilidad(empresa))
    )


@login_required
def terceros_lista(request):
    if request.user.es_superadmin_user():
        messages.warning(request, "Acceso restringido: El SuperAdmin administra la plataforma SaaS y no gestiona contactos comerciales de inquilinos.")
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')

    filtro_tipo = request.GET.get('tipo', 'todos')
    busqueda = request.GET.get('q', '').strip()

    terceros = Tercero.objects.filter(empresa=empresa, activo=True)

    if filtro_tipo == 'clientes':
        terceros = terceros.filter(es_cliente=True)
    elif filtro_tipo == 'proveedores':
        terceros = terceros.filter(es_proveedor=True)

    if busqueda:
        terceros = terceros.filter(
            Q(razon_social__icontains=busqueda) |
            Q(numero_documento__icontains=busqueda) |
            Q(email__icontains=busqueda)
        )

    context = {
        'terceros': terceros,
        'filtro_tipo': filtro_tipo,
        'busqueda': busqueda,
    }
    return render(request, 'terceros/lista.html', context)


@login_required
def tercero_crear(request):
    if request.user.es_superadmin_user():
        messages.warning(request, "Acceso restringido: El SuperAdmin administra la plataforma SaaS y no crea clientes de inquilinos.")
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa or not _tiene_permiso_terceros(request.user, empresa):
        messages.warning(request, "No tienes permisos para crear o editar terceros.")
        return redirect('core:dashboard')

    departamentos = Departamento.objects.all()

    if request.method == 'POST':
        numero_documento = request.POST.get('numero_documento', '').strip()
        razon_social = request.POST.get('razon_social', '').strip()
        tipo_documento = request.POST.get('tipo_documento', 'CC')
        tipo_persona = request.POST.get('tipo_persona', 'NATURAL')
        dv = request.POST.get('dv', '').strip()

        es_cliente = 'es_cliente' in request.POST
        es_proveedor = 'es_proveedor' in request.POST

        if not numero_documento or not razon_social:
            messages.error(request, "El número de documento y la razón social / nombre son obligatorios.")
            return render(request, 'terceros/formulario.html', {'departamentos': departamentos, 'accion': 'Crear'})

        dep_id = request.POST.get('departamento')
        mun_id = request.POST.get('municipio')

        tercero = Tercero.objects.create(
            empresa=empresa,
            numero_documento=numero_documento,
            dv=dv,
            razon_social=razon_social,
            nombre_comercial=request.POST.get('nombre_comercial', '').strip(),
            tipo_persona=tipo_persona,
            tipo_documento=tipo_documento,
            es_cliente=es_cliente,
            es_proveedor=es_proveedor,
            direccion=request.POST.get('direccion', '').strip(),
            departamento_id=dep_id if dep_id else None,
            municipio_id=mun_id if mun_id else None,
            telefono=request.POST.get('telefono', '').strip(),
            email=request.POST.get('email', '').strip(),
            regimen=request.POST.get('regimen', 'NO_RESPONSABLE_IVA'),
            responsabilidades_fiscales=request.POST.get('responsabilidades_fiscales', '').strip(),
            aplica_retefuente='aplica_retefuente' in request.POST,
            tarifa_retefuente=Decimal(request.POST.get('tarifa_retefuente', '0') or '0'),
            aplica_reteica='aplica_reteica' in request.POST,
            tarifa_reteica=Decimal(request.POST.get('tarifa_reteica', '0') or '0'),
            observaciones=request.POST.get('observaciones', '').strip(),
        )

        messages.success(request, f"Tercero {tercero.razon_social} creado correctamente.")
        return redirect('terceros:lista')

    return render(request, 'terceros/formulario.html', {'departamentos': departamentos, 'accion': 'Crear'})


@login_required
def tercero_editar(request, tercero_id):
    if request.user.es_superadmin_user():
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa or not _tiene_permiso_terceros(request.user, empresa):
        messages.warning(request, "No tienes permisos para editar terceros.")
        return redirect('core:dashboard')
    tercero = get_object_or_404(Tercero, id=tercero_id, empresa=empresa)
    departamentos = Departamento.objects.all()
    municipios = Municipio.objects.filter(departamento=tercero.departamento) if tercero.departamento else []

    if request.method == 'POST':
        tercero.razon_social = request.POST.get('razon_social', '').strip()
        tercero.nombre_comercial = request.POST.get('nombre_comercial', '').strip()
        tercero.tipo_persona = request.POST.get('tipo_persona', tercero.tipo_persona)
        tercero.tipo_documento = request.POST.get('tipo_documento', tercero.tipo_documento)
        tercero.dv = request.POST.get('dv', '').strip()

        tercero.es_cliente = 'es_cliente' in request.POST
        tercero.es_proveedor = 'es_proveedor' in request.POST

        tercero.direccion = request.POST.get('direccion', '').strip()
        dep_id = request.POST.get('departamento')
        mun_id = request.POST.get('municipio')
        tercero.departamento_id = dep_id if dep_id else None
        tercero.municipio_id = mun_id if mun_id else None

        tercero.telefono = request.POST.get('telefono', '').strip()
        tercero.email = request.POST.get('email', '').strip()
        tercero.regimen = request.POST.get('regimen', tercero.regimen)
        tercero.responsabilidades_fiscales = request.POST.get('responsabilidades_fiscales', '').strip()
        tercero.aplica_retefuente = 'aplica_retefuente' in request.POST
        tercero.tarifa_retefuente = Decimal(request.POST.get('tarifa_retefuente', '0') or '0')
        tercero.aplica_reteica = 'aplica_reteica' in request.POST
        tercero.tarifa_reteica = Decimal(request.POST.get('tarifa_reteica', '0') or '0')
        tercero.observaciones = request.POST.get('observaciones', '').strip()

        tercero.save()
        messages.success(request, f"Tercero {tercero.razon_social} actualizado con éxito.")
        return redirect('terceros:lista')

    return render(request, 'terceros/formulario.html', {
        'tercero': tercero,
        'departamentos': departamentos,
        'municipios': municipios,
        'accion': 'Editar',
    })


@login_required
def tercero_detalle(request, tercero_id):
    if request.user.es_superadmin_user():
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa:
        return redirect('core:dashboard')
    tercero = get_object_or_404(Tercero, id=tercero_id, empresa=empresa)
    facturas = tercero.facturas.filter(empresa=empresa).order_by('-fecha_emision')[:10]

    return render(request, 'terceros/detalle.html', {
        'tercero': tercero,
        'facturas': facturas,
    })


@login_required
@require_POST
def tercero_eliminar(request, tercero_id):
    if request.user.es_superadmin_user():
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa or not _tiene_permiso_terceros(request.user, empresa):
        messages.warning(request, "No tienes permisos para eliminar terceros.")
        return redirect('core:dashboard')
    tercero = get_object_or_404(Tercero, id=tercero_id, empresa=empresa)
    tercero.activo = False
    tercero.save(update_fields=['activo'])
    messages.success(request, f"Tercero {tercero.razon_social} desactivado correctamente.")
    return redirect('terceros:lista')


@login_required
def terceros_importar(request):
    """Importa clientes y proveedores masivamente desde Excel (.xlsx) o CSV."""
    if request.user.es_superadmin_user():
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa or not _tiene_permiso_terceros(request.user, empresa):
        messages.warning(request, "No tienes permisos para importar terceros.")
        return redirect('core:dashboard')

    if request.method == 'POST' and request.FILES.get('archivo'):
        try:
            _headers, filas = leer_filas_archivo(request.FILES['archivo'])
            creados = 0
            actualizados = 0
            for fila in filas:
                doc = (fila.get('numero_documento') or fila.get('nit') or fila.get('documento') or '').strip()
                nom = (fila.get('razon_social') or fila.get('nombre') or '').strip()
                if not doc or not nom:
                    continue
                tipo_doc = (fila.get('tipo_documento') or 'CC').upper()[:20]
                if tipo_doc not in ('CC', 'NIT', 'CE', 'TI', 'PASAPORTE', 'DIE'):
                    tipo_doc = 'NIT' if len(doc) >= 9 else 'CC'
                defaults = {
                    'razon_social': nom[:200],
                    'tipo_documento': tipo_doc,
                    'tipo_persona': 'JURIDICA' if tipo_doc == 'NIT' else 'NATURAL',
                    'email': (fila.get('email') or fila.get('correo') or '')[:254],
                    'telefono': (fila.get('telefono') or '')[:50],
                    'direccion': (fila.get('direccion') or '')[:200],
                    'es_cliente': str(fila.get('es_cliente', '1')).lower() in ('1', 'si', 'sí', 'true', 'x'),
                    'es_proveedor': str(fila.get('es_proveedor', '0')).lower() in ('1', 'si', 'sí', 'true', 'x'),
                    'activo': True,
                }
                _obj, created = Tercero.objects.update_or_create(
                    empresa=empresa, numero_documento=doc[:30], defaults=defaults
                )
                if created:
                    creados += 1
                else:
                    actualizados += 1
            registrar_actividad(request, 'terceros', 'IMPORTAR_TERCEROS', f"Importó terceros: {creados} creados, {actualizados} actualizados.", empresa=empresa)
            messages.success(request, f"Importación completada: {creados} terceros creados y {actualizados} actualizados.")
            return redirect('terceros:lista')
        except Exception as exc:
            messages.error(request, f"Error importando archivo: {exc}")

    return render(request, 'terceros/importar.html')


@login_required
def tercero_importar_rut_pdf(request):
    """Lee el PDF oficial del RUT DIAN para crear o actualizar un tercero en 1 clic."""
    if request.user.es_superadmin_user():
        return redirect('core:dashboard')
    empresa = request.empresa_activa
    if not empresa or not _tiene_permiso_terceros(request.user, empresa):
        messages.warning(request, "No tienes permisos para importar RUT.")
        return redirect('core:dashboard')

    if request.method == 'POST' and request.FILES.get('archivo_rut'):
        try:
            datos = extraer_datos_rut_pdf(request.FILES['archivo_rut'].read())
            nit = request.POST.get('nit_manual', '').strip() or datos['nit']
            raz = request.POST.get('razon_manual', '').strip() or datos['razon_social']
            if not nit or not raz:
                messages.warning(request, "No se pudo leer NIT o Razón Social en el PDF. Verifica que no sea un escaneo sin texto.")
                return render(request, 'terceros/importar_rut.html', {'datos_rut': datos})
            tercero, created = Tercero.objects.update_or_create(
                empresa=empresa,
                numero_documento=nit[:30],
                defaults={
                    'tipo_documento': 'NIT',
                    'tipo_persona': datos['tipo_persona'],
                    'dv': datos['dv'][:1],
                    'razon_social': raz[:200],
                    'direccion': datos['direccion'][:200],
                    'telefono': datos['telefono'][:50],
                    'email': datos['email'][:254],
                    'regimen': datos['regimen'],
                    'responsabilidades_fiscales': datos['responsabilidades'][:150],
                    'es_cliente': 'es_cliente' in request.POST or 'es_proveedor' not in request.POST,
                    'es_proveedor': 'es_proveedor' in request.POST,
                    'activo': True,
                },
            )
            registrar_actividad(request, 'terceros', 'IMPORTAR_RUT_PDF', f"Importó RUT PDF para {tercero.razon_social} ({tercero.numero_documento}).", empresa=empresa, objeto=tercero)
            messages.success(request, f"Tercero {'creado' if created else 'actualizado'} desde RUT PDF: {tercero.razon_social}.")
            return redirect('terceros:detalle', tercero_id=tercero.id)
        except Exception as exc:
            messages.error(request, f"Error leyendo RUT PDF: {exc}")

    return render(request, 'terceros/importar_rut.html')
