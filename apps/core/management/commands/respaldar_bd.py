import gzip
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Genera respaldo JSON (.json.gz) y SQL nativo (.sql.gz si aplica) manteniendo los últimos 7."

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

        db_cfg = settings.DATABASES.get("default", {})
        engine = db_cfg.get("ENGINE", "")
        sql_path = None
        if "mysql" in engine and shutil.which("mysqldump"):
            sql_path = dest_dir / f"faccol_backup_{stamp}.sql.gz"
            env = os.environ.copy()
            env["MYSQL_PWD"] = str(db_cfg.get("PASSWORD") or "")
            cmd = [
                "mysqldump",
                "--single-transaction",
                "--quick",
                "-h", str(db_cfg.get("HOST") or "127.0.0.1"),
                "-P", str(db_cfg.get("PORT") or "3306"),
                "-u", str(db_cfg.get("USER") or "root"),
                str(db_cfg.get("NAME") or ""),
            ]
            try:
                proc = subprocess.run(cmd, env=env, capture_output=True, check=True)
                with gzip.open(sql_path, "wb") as gz_sql:
                    gz_sql.write(proc.stdout)
            except Exception:
                sql_path = None

        for pattern in ("faccol_backup_*.json.gz", "faccol_backup_*.sql.gz"):
            archivos = sorted(dest_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            for antiguo in archivos[7:]:
                try:
                    antiguo.unlink()
                except OSError:
                    pass

        extra = f" + {sql_path.name}" if sql_path else ""
        self.stdout.write(f"Respaldo generado: {out_path.name}{extra}")
