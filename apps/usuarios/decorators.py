from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages


def requiere_superadmin(view_func):
    """
    Exige que el usuario sea Superadministrador global de la plataforma.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('usuarios:login')
        if not (request.user.is_superadmin or request.user.is_superuser):
            messages.error(request, "Acceso restringido: Esta sección requiere privilegios de Superadministrador global.")
            return redirect('core:dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped


def requiere_admin(view_func):
    """
    Exige que el usuario sea Administrador o Propietario de la empresa activa.
    El SuperAdmin gestiona la plataforma global y no opera inquilinos comerciales.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('usuarios:login')
        if request.user.es_superadmin_user():
            messages.warning(request, "Acceso restringido: El SuperAdmin administra la plataforma global SaaS y no administra empresas inquilinas individuales.")
            return redirect('core:dashboard')
        empresa = getattr(request, 'empresa_activa', None)
        if not request.user.es_admin(empresa):
            messages.warning(request, "Acceso restringido: Se requieren permisos de Administrador o Propietario de la empresa.")
            return redirect('core:dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped


def requiere_contador(view_func):
    """
    Exige que el usuario sea Contador, Administrador o Propietario de la empresa activa.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('usuarios:login')
        if request.user.es_superadmin_user():
            messages.warning(request, "Acceso restringido: El SuperAdmin gestiona la infraestructura de la plataforma y no los registros contables de inquilinos.")
            return redirect('core:dashboard')
        empresa = getattr(request, 'empresa_activa', None)
        if not request.user.puede_contabilidad(empresa):
            messages.warning(request, "Acceso restringido: El módulo contable está reservado para Contadores y Administradores.")
            return redirect('core:dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped


def requiere_nomina(view_func):
    """
    Exige que el usuario tenga permisos de Nómina (Admin, Propietario o Contador) de la empresa activa.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('usuarios:login')
        if request.user.es_superadmin_user():
            messages.warning(request, "Acceso restringido: El SuperAdmin gestiona la infraestructura de la plataforma y no liquida nómina de inquilinos.")
            return redirect('core:dashboard')
        empresa = getattr(request, 'empresa_activa', None)
        if not request.user.puede_nomina(empresa):
            messages.warning(request, "Acceso restringido: El módulo de Nómina está reservado para Administración y Contabilidad.")
            return redirect('core:dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped


def requiere_facturacion(view_func):
    """
    Exige que el usuario pueda emitir facturación o cobros (Vendedor, Contador, Admin, Propietario).
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('usuarios:login')
        if request.user.es_superadmin_user():
            messages.warning(request, "Acceso restringido: La cuenta SuperAdmin es exclusiva para la gestión de la plataforma SaaS y no emite facturación comercial ni POS.")
            return redirect('core:dashboard')
        empresa = getattr(request, 'empresa_activa', None)
        if not request.user.puede_facturar(empresa):
            messages.warning(request, "Acceso restringido: Su rol no tiene permisos para emitir facturación o cobros.")
            return redirect('core:dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped


def requiere_inventario(view_func):
    """
    Exige que el usuario pueda gestionar o consultar inventario (Operador, Vendedor, Admin, Propietario).
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('usuarios:login')
        if request.user.es_superadmin_user():
            messages.warning(request, "Acceso restringido: El SuperAdmin administra la plataforma SaaS y no gestiona existencias de inventario de inquilinos.")
            return redirect('core:dashboard')
        empresa = getattr(request, 'empresa_activa', None)
        if not request.user.puede_inventario(empresa):
            messages.warning(request, "Acceso restringido: Su rol no tiene permisos para gestionar inventario.")
            return redirect('core:dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped
