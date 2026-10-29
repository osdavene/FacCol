from decimal import Decimal
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
    """
    Ruta unificada: Redirige directamente al Dashboard Principal, donde el SuperAdmin
    cuenta con su centro de comando global integrado sin rutas dispersas.
    """
    return redirect('core:dashboard')


@user_passes_test(es_superadmin)
def empresas_lista(request):
    busqueda = request.GET.get('q', '').strip()
    empresas = Empresa.objects.select_related('plan').prefetch_related(
        'usuarios_asignados__user'
    ).order_by('-created_at')

    if busqueda:
        empresas = empresas.filter(razon_social__icontains=busqueda) | empresas.filter(nit__icontains=busqueda)

    return render(request, 'backoffice/empresas_lista.html', {'empresas': empresas, 'busqueda': busqueda})


@user_passes_test(es_superadmin)
def empresa_toggle_activa(request, empresa_id):
    empresa = get_object_or_404(Empresa, id=empresa_id)
    empresa.activa = not empresa.activa
    empresa.save(update_fields=['activa'])
    estado_str = "activada" if empresa.activa else "suspendida"
    messages.success(request, f"La empresa inquilina '{empresa.razon_social}' ha sido {estado_str} exitosamente.")
    return redirect('backoffice:empresas_lista')


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

        # Habilitar módulos según el plan o por defecto
        if empresa.plan and empresa.plan.modulos.exists():
            for mod in empresa.plan.modulos.all():
                empresa.modulos.add(mod)
        else:
            for mod in Modulo.objects.filter(activo_por_defecto=True):
                empresa.modulos.add(mod)

        # Aislamiento Estricto SaaS: Determinar el Propietario inicial
        admin_tipo = request.POST.get('admin_tipo', 'nuevo_cliente')

        if admin_tipo == 'asignar_superadmin':
            # Empresa propia del SuperAdmin (ej. ServiGest)
            EmpresaUsuario.objects.create(
                user=request.user,
                empresa=empresa,
                rol='propietario',
                activo=True
            )
            messages.success(request, f"Empresa propia '{empresa.razon_social}' creada exitosamente y asignada a tu cuenta como Propietario.")
        else:
            # Empresa de un cliente: Creamos su usuario administrador inicial independiente
            admin_email = request.POST.get('admin_email', '').strip().lower() or email
            admin_nombre = request.POST.get('admin_nombre', '').strip() or razon_social
            admin_password = request.POST.get('admin_password', '').strip() or 'Clave1234*'

            client_user, created = User.objects.get_or_create(
                username=admin_email,
                defaults={
                    'email': admin_email,
                    'first_name': admin_nombre,
                    'is_superadmin': False,
                    'is_staff': False,
                    'is_superuser': False,
                }
            )
            if created or admin_password:
                client_user.set_password(admin_password)
                client_user.save()

            EmpresaUsuario.objects.create(
                user=client_user,
                empresa=empresa,
                rol='propietario',
                activo=True
            )

            messages.success(
                request,
                f"Empresa inquilina '{empresa.razon_social}' creada exitosamente con Aislamiento Estricto. "
                f"Credenciales del cliente: Usuario: {admin_email} | Clave: {admin_password}"
            )

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


# ══════════════════════════════════════════════════════════════════════
# GESTIÓN INTEGRAL DE PLANES & SUSCRIPCIONES SAAS
# ══════════════════════════════════════════════════════════════════════

@user_passes_test(es_superadmin)
def planes_lista(request):
    """
    Catálogo comercial y panel de suscripciones de la plataforma SaaS.
    """
    planes = Plan.objects.prefetch_related('modulos', 'empresas').order_by('precio_mensual')
    total_planes = planes.count()
    planes_activos = planes.filter(activo=True).count()
    total_mrr = sum(p.mrr_generado for p in planes)
    total_empresas_suscritas = Empresa.objects.filter(plan__isnull=False, activa=True).count()

    context = {
        'planes': planes,
        'total_planes': total_planes,
        'planes_activos': planes_activos,
        'total_mrr': total_mrr,
        'total_empresas_suscritas': total_empresas_suscritas,
    }
    return render(request, 'backoffice/planes_lista.html', context)


@user_passes_test(es_superadmin)
def plan_crear(request):
    """
    Crea un nuevo plan comercial definiendo módulos, límites y precio.
    """
    modulos = Modulo.objects.all().order_by('orden', 'nombre')

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        precio_mensual = Decimal(request.POST.get('precio_mensual', '0') or '0')
        limite_facturas_mes = int(request.POST.get('limite_facturas_mes', 500) or 500)
        limite_usuarios = int(request.POST.get('limite_usuarios', 5) or 5)
        duracion_meses = int(request.POST.get('duracion_meses', 1) or 1)
        color_tema = request.POST.get('color_tema', 'amber')
        destacado = 'destacado' in request.POST
        activo = 'activo' in request.POST
        caracteristicas = request.POST.get('caracteristicas', '').strip()

        if not nombre:
            messages.error(request, "El nombre del plan es obligatorio.")
            return render(request, 'backoffice/plan_formulario.html', {
                'modulos': modulos,
                'colores': Plan.COLOR_CHOICES,
                'accion': 'Crear',
            })

        plan = Plan.objects.create(
            nombre=nombre,
            descripcion=descripcion,
            precio_mensual=precio_mensual,
            limite_facturas_mes=limite_facturas_mes,
            limite_usuarios=limite_usuarios,
            duracion_meses=duracion_meses,
            color_tema=color_tema,
            destacado=destacado,
            activo=activo,
            caracteristicas=caracteristicas,
        )

        modulos_ids = request.POST.getlist('modulos[]') or request.POST.getlist('modulos')
        if modulos_ids:
            plan.modulos.set(modulos_ids)

        messages.success(request, f"Plan '{plan.nombre}' creado exitosamente en el catálogo SaaS.")
        return redirect('backoffice:planes_lista')

    return render(request, 'backoffice/plan_formulario.html', {
        'modulos': modulos,
        'colores': Plan.COLOR_CHOICES,
        'accion': 'Crear',
    })


@user_passes_test(es_superadmin)
def plan_editar(request, plan_id):
    """
    Edita un plan comercial existente.
    """
    plan = get_object_or_404(Plan, id=plan_id)
    modulos = Modulo.objects.all().order_by('orden', 'nombre')

    if request.method == 'POST':
        plan.nombre = request.POST.get('nombre', plan.nombre).strip()
        plan.descripcion = request.POST.get('descripcion', '').strip()
        plan.precio_mensual = Decimal(request.POST.get('precio_mensual', '0') or '0')
        plan.limite_facturas_mes = int(request.POST.get('limite_facturas_mes', 500) or 500)
        plan.limite_usuarios = int(request.POST.get('limite_usuarios', 5) or 5)
        plan.duracion_meses = int(request.POST.get('duracion_meses', 1) or 1)
        plan.color_tema = request.POST.get('color_tema', 'amber')
        plan.destacado = 'destacado' in request.POST
        plan.activo = 'activo' in request.POST
        plan.caracteristicas = request.POST.get('caracteristicas', '').strip()

        if not plan.nombre:
            messages.error(request, "El nombre del plan no puede quedar vacío.")
            return render(request, 'backoffice/plan_formulario.html', {
                'plan': plan,
                'modulos': modulos,
                'colores': Plan.COLOR_CHOICES,
                'modulos_activos_ids': list(plan.modulos.values_list('id', flat=True)),
                'accion': 'Editar',
            })

        plan.save()

        modulos_ids = request.POST.getlist('modulos[]') or request.POST.getlist('modulos')
        plan.modulos.set(modulos_ids)

        messages.success(request, f"Plan '{plan.nombre}' actualizado correctamente.")
        return redirect('backoffice:planes_lista')

    return render(request, 'backoffice/plan_formulario.html', {
        'plan': plan,
        'modulos': modulos,
        'colores': Plan.COLOR_CHOICES,
        'modulos_activos_ids': list(plan.modulos.values_list('id', flat=True)),
        'accion': 'Editar',
    })


@user_passes_test(es_superadmin)
def plan_toggle_activo(request, plan_id):
    """
    Activa o pausa un plan comercial.
    """
    plan = get_object_or_404(Plan, id=plan_id)
    plan.activo = not plan.activo
    plan.save(update_fields=['activo'])
    estado_str = "activado" if plan.activo else "pausado"
    messages.success(request, f"El plan '{plan.nombre}' ha sido {estado_str}.")
    return redirect('backoffice:planes_lista')


@user_passes_test(es_superadmin)
def plan_eliminar(request, plan_id):
    """
    Elimina un plan siempre que no tenga empresas vinculadas.
    """
    plan = get_object_or_404(Plan, id=plan_id)
    if plan.empresas.exists():
        messages.warning(
            request,
            f"No es posible eliminar el plan '{plan.nombre}' porque tiene {plan.empresas.count()} empresas suscritas. Puede pausarlo para impedir nuevas suscripciones."
        )
        return redirect('backoffice:planes_lista')

    nombre = plan.nombre
    plan.delete()
    messages.success(request, f"Plan '{nombre}' eliminado correctamente.")
    return redirect('backoffice:planes_lista')
