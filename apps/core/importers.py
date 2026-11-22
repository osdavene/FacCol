"""Utilidades compartidas de importación masiva desde Excel (.xlsx) y CSV (.csv)."""

import csv
import io
from typing import List, Tuple

import openpyxl


def leer_filas_archivo(uploaded_file) -> Tuple[List[str], List[dict]]:
    """
    Lee un archivo .xlsx o .csv y retorna (columnas_normalizadas, lista_de_diccionarios).
    Normaliza cabeceras a minúsculas sin espacios extras para facilitar migraciones desde Siigo/Excel.
    """
    if not uploaded_file:
        raise ValueError("No se recibió ningún archivo.")

    nombre = (getattr(uploaded_file, "name", "") or "").lower()
    raw_bytes = uploaded_file.read()
    if not raw_bytes:
        raise ValueError("El archivo está vacío.")

    if nombre.endswith(".xlsx"):
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
        ws = wb.active
        filas_raw = list(ws.iter_rows(values_only=True))
        if not filas_raw:
            return [], []
        headers = [str(h or "").strip().lower() for h in filas_raw[0]]
        datos = []
        for row in filas_raw[1:]:
            if not any(cell is not None and str(cell).strip() != "" for cell in row):
                continue
            item = {}
            for idx, h in enumerate(headers):
                if not h:
                    continue
                val = row[idx] if idx < len(row) else ""
                item[h] = "" if val is None else str(val).strip()
            datos.append(item)
        return [h for h in headers if h], datos

    # CSV (UTF-8 with BOM o Latin-1 típico de Excel español)
    texto = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            texto = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if texto is None:
        raise ValueError("No fue posible decodificar el archivo CSV (use UTF-8 o .xlsx).")

    sample = texto[:2048]
    delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(texto), delimiter=delim)
    if not reader.fieldnames:
        return [], []
    headers = [str(h or "").strip().lower() for h in reader.fieldnames]
    datos = []
    for row in reader:
        item = {}
        has_data = False
        for raw_k, raw_v in row.items():
            k = str(raw_k or "").strip().lower()
            v = "" if raw_v is None else str(raw_v).strip()
            if k:
                item[k] = v
                if v:
                    has_data = True
        if has_data:
            datos.append(item)
    return [h for h in headers if h], datos
