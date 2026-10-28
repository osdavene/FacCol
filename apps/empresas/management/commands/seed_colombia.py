import json
import os
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.empresas.models import Departamento, Municipio


class Command(BaseCommand):
    help = "Carga los departamentos y municipios de Colombia (DANE DIVIPOLA)"

    def handle(self, *args, **options):
        json_file = "colombia_divipola.json"
        if not os.path.exists(json_file):
            self.stdout.write(self.style.ERROR(f"Archivo {json_file} no encontrado."))
            return

        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.stdout.write(self.style.WARNING("Cargando division politica de Colombia..."))

        with transaction.atomic():
            for d in data:
                dep_obj, _ = Departamento.objects.get_or_create(
                    codigo=d["codigo"],
                    defaults={"nombre": d["nombre"]}
                )

                for m in d["municipios"]:
                    Municipio.objects.get_or_create(
                        codigo=m["codigo"],
                        defaults={
                            "nombre": m["nombre"],
                            "departamento": dep_obj
                        }
                    )

        self.stdout.write(self.style.SUCCESS(
            f"Exitoso: {Departamento.objects.count()} departamentos y {Municipio.objects.count()} municipios cargados en PostgreSQL."
        ))
