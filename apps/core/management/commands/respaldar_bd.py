import gzip
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Genera respaldo JSON comprimido (.json.gz) de la base de datos y rota manteniendo los últimos 7."

    def add_arguments(self, parser):
        parser.add_argument("--destino", type=str, default="", help="Directorio destino opcional para los respaldos.")

    def handle(self, *args, **options):
        dest_dir = Path(options.get("destino") or (Path(settings.BASE_DIR) / "backups"))
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = dest_dir / f"faccol_backup_{stamp}.json.gz"

        from io import StringIO

        buf = StringIO()
        call_command(
            "dumpdata",
            exclude=["contenttypes", "auth.permission", "sessions.session"],
            stdout=buf,
        )
        with gzip.open(out_path, "wt", encoding="utf-8") as gz:
            gz.write(buf.getvalue())

        archivos = sorted(dest_dir.glob("faccol_backup_*.json.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        for antiguo in archivos[7:]:
            try:
                antiguo.unlink()
            except OSError:
                pass

        self.stdout.write(f"Respaldo generado: {out_path.name}")
