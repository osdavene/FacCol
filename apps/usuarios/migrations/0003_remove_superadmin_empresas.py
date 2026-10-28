from django.db import migrations
from django.contrib.auth.hashers import make_password


def purge_superadmin_company_assignments(apps, schema_editor):
    User = apps.get_model('usuarios', 'User')
    EmpresaUsuario = apps.get_model('usuarios', 'EmpresaUsuario')
    Empresa = apps.get_model('empresas', 'Empresa')

    # 1. Obtener todos los usuarios SuperAdmin / SuperUser
    superadmins = User.objects.filter(is_superadmin=True) | User.objects.filter(is_superuser=True)
    
    # 2. Eliminar cualquier membresía o asignación de inquilino para superadmins
    EmpresaUsuario.objects.filter(user__in=superadmins).delete()

    # 3. Si la empresa 1 quedó sin usuario administrador, crear un administrador de inquilino independiente
    empresa_principal = Empresa.objects.filter(id=1).first()
    if empresa_principal:
        tiene_admin = EmpresaUsuario.objects.filter(empresa=empresa_principal, activo=True).exists()
        if not tiene_admin:
            # Crear o reutilizar usuario inquilino no-superadmin
            tenant_user, created = User.objects.get_or_create(
                username='admin.empresa@faccol.com',
                defaults={
                    'email': 'admin.empresa@faccol.com',
                    'first_name': 'Admin Inquilino',
                    'last_name': 'FacCol Tech',
                    'password': make_password('password123'),
                    'is_superadmin': False,
                    'is_superuser': False,
                    'is_staff': False,
                }
            )
            if not created and not tenant_user.password:
                tenant_user.password = make_password('password123')
                tenant_user.save()

            EmpresaUsuario.objects.create(
                user=tenant_user,
                empresa=empresa_principal,
                rol='propietario',
                activo=True
            )


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0002_alter_empresausuario_rol'),
        ('empresas', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(
            purge_superadmin_company_assignments,
            reverse_code=migrations.RunPython.noop
        ),
    ]
