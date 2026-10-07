"""Paquete de datos para la entrega (issue #23, sección 10 del reto).

Arma un .zip con el snapshot, el manifest, el diccionario de datos, el catálogo con las
licencias y condiciones de cada fuente, el reporte de calidad y el benchmark si existe.

Lo que no se puede redistribuir se excluye: la descripción del RSS de TVN es solo para
análisis interno (decisión #35), así que se quita de noticias y rechazos. Como eso cambia
noticias.csv, manifest_paquete.json trae el SHA-256 de cada archivo tal como va en el zip;
el manifest.json original se conserva para trazabilidad.

Uso:  uv run python -m faro_editorial.paquete
"""

import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from faro_editorial import __version__
from faro_editorial.carga import ARCHIVO_NOTICIAS, cargar_snapshot
from faro_editorial.catalogo import RUTA_CONFIG, escribir_catalogo, generar_catalogo
from faro_editorial.settings import ROOT_DIR

# Columnas con contenido de terceros que no se redistribuye.
COLUMNAS_EXCLUIDAS = {"descripcion"}
RUTA_CONTRATO = ROOT_DIR / "data" / "CONTRATO.md"

LEEME = """# Paquete de datos · Faro Editorial {version}

Generado el {fecha} con `uv run python -m faro_editorial.carga` y
`uv run python -m faro_editorial.paquete`.

| Archivo | Contenido |
|---|---|
| `raw/` | Snapshot: noticias, fuentes, indicadores, sismos y manifest original |
| `CONTRATO.md` | Diccionario de datos: columnas, formatos y consultas usadas |
| `licencias_y_condiciones.yaml` | Licencia y condiciones de reutilización de cada fuente |
| `processed/catalogo_datos.csv` | Catálogo por fuente: URL, cobertura, hash y excluidos |
| `processed/reporte_calidad.json` | Integridad SHA-256, conteos, rechazos y nulos por campo |
| `processed/rechazos.jsonl` | Filas excluidas en la carga con su motivo |
| `benchmark/` | Consultas de desarrollo con sus etiquetas, si existen |
| `manifest_paquete.json` | SHA-256 de cada archivo tal como va en este paquete |

## Contenido excluido

- Columna(s) {excluidas} de `noticias.csv` y de los rechazos: la descripción del RSS de TVN se
  usa solo para análisis interno y no se redistribuye (sección 6 del reto; decisión #35).
  Por eso el SHA-256 de `raw/noticias.csv` difiere del `manifest.json` original; el de este
  paquete está en `manifest_paquete.json`.
- No se incluyen artículos completos, imágenes ni videos: solo titulares, metadatos y enlaces.
  Para reconstruir el contenido excluido, seguir las consultas de `CONTRATO.md`.
"""


def _sha256(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def _csv_sin_columnas(ruta: Path, excluidas: set[str]) -> bytes:
    with ruta.open(encoding="utf-8-sig", newline="") as f:
        lector = csv.DictReader(f)
        columnas = [c for c in (lector.fieldnames or []) if c not in excluidas]
        salida = io.StringIO()
        escritor = csv.DictWriter(salida, fieldnames=columnas, extrasaction="ignore")
        escritor.writeheader()
        for fila in lector:
            escritor.writerow(fila)
    return salida.getvalue().encode("utf-8")


def _rechazos_sin_columnas(ruta: Path, excluidas: set[str]) -> bytes:
    lineas = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        rechazo = json.loads(linea)
        if isinstance(rechazo.get("datos"), dict):
            for columna in excluidas:
                rechazo["datos"].pop(columna, None)
        lineas.append(json.dumps(rechazo, ensure_ascii=False))
    return ("\n".join(lineas) + "\n" if lineas else "").encode("utf-8")


def construir_paquete(
    raw_dir: Path,
    processed_dir: Path,
    destino_dir: Path,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    benchmark_dir: Path | None = None,
) -> Path:
    """Recarga el snapshot (para que reporte y catálogo estén al día) y escribe el .zip."""
    raw_dir, processed_dir, destino_dir = Path(raw_dir), Path(processed_dir), Path(destino_dir)
    resultado = cargar_snapshot(raw_dir, processed_dir, desde, hasta)
    rutas_catalogo = escribir_catalogo(generar_catalogo(resultado), processed_dir)

    entradas: dict[str, bytes] = {}
    for ruta in sorted(p for p in raw_dir.iterdir() if p.is_file() and p.name != ".gitkeep"):
        if ruta.name == ARCHIVO_NOTICIAS:
            entradas[f"raw/{ruta.name}"] = _csv_sin_columnas(ruta, COLUMNAS_EXCLUIDAS)
        else:
            entradas[f"raw/{ruta.name}"] = ruta.read_bytes()
    entradas["processed/reporte_calidad.json"] = resultado.rutas["reporte"].read_bytes()
    entradas["processed/rechazos.jsonl"] = _rechazos_sin_columnas(
        resultado.rutas["rechazos"], COLUMNAS_EXCLUIDAS
    )
    entradas["processed/catalogo_datos.csv"] = rutas_catalogo["csv"].read_bytes()
    entradas["processed/catalogo_datos.md"] = rutas_catalogo["md"].read_bytes()
    entradas["CONTRATO.md"] = RUTA_CONTRATO.read_bytes()
    entradas["licencias_y_condiciones.yaml"] = RUTA_CONFIG.read_bytes()
    if benchmark_dir and Path(benchmark_dir).is_dir():
        for ruta in sorted(Path(benchmark_dir).glob("*.jsonl")):
            entradas[f"benchmark/{ruta.name}"] = ruta.read_bytes()

    generado = datetime.now(UTC)
    entradas["LEEME.md"] = LEEME.format(
        version=__version__,
        fecha=generado.isoformat(timespec="seconds"),
        excluidas=", ".join(f"`{c}`" for c in sorted(COLUMNAS_EXCLUIDAS)),
    ).encode("utf-8")
    manifest = {
        "version": f"faro-editorial-datos-{__version__}",
        "generado_utc": generado.isoformat(timespec="seconds"),
        "integridad_snapshot_original": resultado.integridad["ok"],
        "columnas_excluidas": {ARCHIVO_NOTICIAS: sorted(COLUMNAS_EXCLUIDAS)},
        "archivos": {nombre: {"sha256": _sha256(datos)} for nombre, datos in entradas.items()},
    }
    entradas["manifest_paquete.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode(
        "utf-8"
    )

    destino_dir.mkdir(parents=True, exist_ok=True)
    ruta_zip = destino_dir / f"faro-editorial-datos-{__version__}.zip"
    with ZipFile(ruta_zip, "w", ZIP_DEFLATED) as z:
        for nombre, datos in entradas.items():
            z.writestr(nombre, datos)
    return ruta_zip


def main() -> None:
    from faro_editorial.settings import get_settings

    s = get_settings()
    ruta = construir_paquete(
        s.raw_dir,
        s.processed_dir,
        ROOT_DIR / "dist",
        s.ventana_desde,
        s.ventana_hasta,
        s.data_dir / "benchmark",
    )
    print(f"Paquete de datos: {ruta}")


if __name__ == "__main__":
    main()
