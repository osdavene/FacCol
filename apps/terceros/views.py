from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q

from apps.terceros.models import Tercero
from apps.empresas.models import Departamento, Municipio


@login_required
def terceros_lista(request):
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
    empresa = request.empresa_activa
    if not empresa:
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
            observaciones=request.POST.get('observaciones', '').strip(),
        )

        messages.success(request, f"Tercero {tercero.razon_social} creado correctamente.")
        return redirect('terceros:lista')

    return render(request, 'terceros/formulario.html', {'departamentos': departamentos, 'accion': 'Crear'})


@login_required
def tercero_editar(request, tercero_id):
    empresa = request.empresa_activa
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
    empresa = request.empresa_activa
    tercero = get_object_or_404(Tercero, id=tercero_id, empresa=empresa)
    facturas = tercero.facturas.filter(empresa=empresa).order_by('-fecha_emision')[:10]

    return render(request, 'terceros/detalle.html', {
        'tercero': tercero,
        'facturas': facturas,
    })


@login_required
def tercero_eliminar(request, tercero_id):
    empresa = request.empresa_activa
    tercero = get_object_or_404(Tercero, id=tercero_id, empresa=empresa)
    tercero.activo = False
    tercero.save(update_fields=['activo'])
    messages.success(request, f"Tercero {tercero.razon_social} desactivado correctamente.")
    return redirect('terceros:lista')
