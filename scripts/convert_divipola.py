import re
import json

src_path = r"C:\Proyectos\facturaco\database\seeders\ColombiaDivisionSeeder.php"

with open(src_path, "r", encoding="utf-8") as f:
    text = f.read()

# Pattern to capture each department block
dep_pattern = re.compile(
    r"\[\s*'codigo'\s*=>\s*'(\d+)',\s*'nombre'\s*=>\s*'([^']+)',\s*'municipios'\s*=>\s*\[(.*?)\]\s*,?\s*\]",
    re.DOTALL
)

mun_pattern = re.compile(r"\['(\d+)'\s*,\s*'([^']+)'\]")

departamentos = []
for dep_code, dep_name, muns_block in dep_pattern.findall(text):
    municipios = []
    for mun_code, mun_name in mun_pattern.findall(muns_block):
        municipios.append({"codigo": mun_code, "nombre": mun_name.strip()})
    departamentos.append({
        "codigo": dep_code,
        "nombre": dep_name.strip(),
        "municipios": municipios
    })

out_path = "colombia_divipola.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(departamentos, f, ensure_ascii=False, indent=2)

total_muns = sum(len(d["municipios"]) for d in departamentos)
print(f"SUCCESS: Exported {len(departamentos)} departamentos and {total_muns} municipios to {out_path}")
