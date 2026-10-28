from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import user_passes_test
from django.contrib import messages
from apps.empresas.models import Empresa, Plan, Modulo, Departamento, Municipio
from apps.usuarios.models import User, EmpresaUsuario
from apps.facturacion.models import Factura


def es_superadmin(user):
    return user.is_authenticated and (user.is_superadmin or user.is_superuser)


@user_passes_test(es_superadmin)
def backoffice_dashboard(request):
    total_empresas = Empresa.objects.count()
    empresas_activas = Empresa.objects.filter(activa=True).count()
    total_usuarios = User.objects.count()
    total_facturas_global = Factura.objects.count()

    empresas_recientes = Empresa.objects.select_related('plan').order_by('-created_at')[:10]

    return render(request, 'backoffice/dashboard.html', {
        'total_empresas': total_empresas,
        'empresas_activas': empresas_activas,
        'total_usuarios': total_usuarios,
        'total_facturas_global': total_facturas_global,
        'empresas_recientes': empresas_recientes,
    })


@user_passes_test(es_superadmin)
def empresas_lista(request):
    busqueda = request.GET.get('q', '').strip()
    empresas = Empresa.objects.select_related('plan').order_by('-created_at')

    if busqueda:
        empresas = empresas.filter(razon_social__icontains=busqueda) | empresas.filter(nit__icontains=busqueda)

    return render(request, 'backoffice/empresas_lista.html', {'empresas': empresas, 'busqueda': busqueda})


@user_passes_test(es_superadmin)
def empresa_crear(request):
    departamentos = Departamento.objects.all()
    planes = Plan.objects.filter(activo=True)

    if request.method == 'POST':
        razon_social = request.POST.get('razon_social', '').strip()
        nit = request.POST.get('nit', '').strip()
        dv = request.POST.get('dv', '').strip()
        email = request.POST.get('email', '').strip()
        plan_id = request.POST.get('plan')

        if not razon_social or not nit:
            messages.error(request, "Razón Social y NIT son obligatorios.")
            return render(request, 'backoffice/empresa_formulario.html', {
                'departamentos': departamentos, 'planes': planes, 'accion': 'Crear'
            })

        empresa = Empresa.objects.create(
            razon_social=razon_social,
            nombre_comercial=request.POST.get('nombre_comercial', '').strip(),
            nit=nit,
            dv=dv,
            email=email,
            telefono=request.POST.get('telefono', '').strip(),
            direccion=request.POST.get('direccion', '').strip(),
            plan_id=plan_id if plan_id else None,
            prefijo_factura=request.POST.get('prefijo_factura', 'FE').strip().upper(),
            resolucion_dian=request.POST.get('resolucion_dian', '').strip(),
        )

        # Habilitar módulos por defecto
        for mod in Modulo.objects.filter(activo_por_defecto=True):
            empresa.modulos.add(mod)

        # Asignar al creador como propietario
        EmpresaUsuario.objects.create(
            user=request.user,
            empresa=empresa,
            rol='propietario',
            activo=True
        )

        messages.success(request, f"Empresa '{empresa.razon_social}' creada exitosamente.")
        return redirect('backoffice:empresas_lista')

    return render(request, 'backoffice/empresa_formulario.html', {
        'departamentos': departamentos,
        'planes': planes,
        'accion': 'Crear'
    })


@user_passes_test(es_superadmin)
def empresa_editar(request, empresa_id):
    empresa = get_object_or_404(Empresa, id=empresa_id)
    departamentos = Departamento.objects.all()
    planes = Plan.objects.filter(activo=True)
    modulos = Modulo.objects.all()

    if request.method == 'POST':
        empresa.razon_social = request.POST.get('razon_social', empresa.razon_social).strip()
        empresa.nombre_comercial = request.POST.get('nombre_comercial', '').strip()
        empresa.nit = request.POST.get('nit', empresa.nit).strip()
        empresa.dv = request.POST.get('dv', '').strip()
        empresa.email = request.POST.get('email', '').strip()
        empresa.telefono = request.POST.get('telefono', '').strip()
        empresa.direccion = request.POST.get('direccion', '').strip()

        plan_id = request.POST.get('plan')
        empresa.plan_id = plan_id if plan_id else None

        # Configuración DIAN
        empresa.prefijo_factura = request.POST.get('prefijo_factura', 'FE').strip().upper()
        empresa.resolucion_dian = request.POST.get('resolucion_dian', '').strip()
        empresa.rango_desde = int(request.POST.get('rango_desde', 1) or 1)
        empresa.rango_hasta = int(request.POST.get('rango_hasta', 5000) or 5000)

        # Configuración SMTP
        empresa.smtp_host = request.POST.get('smtp_host', '').strip()
        empresa.smtp_port = int(request.POST.get('smtp_port', 587) or 587)
        empresa.smtp_user = request.POST.get('smtp_user', '').strip()
        empresa.smtp_password = request.POST.get('smtp_password', '').strip()
        empresa.smtp_from_address = request.POST.get('smtp_from_address', '').strip()

        # Configuración Wompi
        empresa.wompi_public_key = request.POST.get('wompi_public_key', '').strip()
        empresa.wompi_private_key = request.POST.get('wompi_private_key', '').strip()
        empresa.wompi_events_secret = request.POST.get('wompi_events_secret', '').strip()

        empresa.activa = 'activa' in request.POST
        empresa.save()

        # Actualizar módulos
        modulos_seleccionados = request.POST.getlist('modulos[]')
        empresa.modulos.clear()
        for mod_id in modulos_seleccionados:
            empresa.modulos.add(mod_id)

        messages.success(request, f"Empresa '{empresa.razon_social}' actualizada correctamente.")
        return redirect('backoffice:empresas_lista')

    return render(request, 'backoffice/empresa_formulario.html', {
        'empresa': empresa,
        'departamentos': departamentos,
        'planes': planes,
        'modulos': modulos,
        'modulos_activos_ids': list(empresa.modulos.values_list('id', flat=True)),
        'accion': 'Editar'
    })
