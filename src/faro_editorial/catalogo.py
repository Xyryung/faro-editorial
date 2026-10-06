"""Catálogo de datos para la página "Catálogo de datos" de Notion (issue #4, sección 5).

Una fila por fuente con: fuente, URL, fecha de extracción, cobertura, campos,
licencia/condiciones, transformaciones, hash del snapshot y registros excluidos.
Combina los datos fijos de config/fuentes_catalogo.yaml con lo calculado en la carga.

Salidas en data/processed/:
- catalogo_datos.csv  se importa en Notion como base de datos (Importar → CSV)
- catalogo_datos.md   versión legible para revisar o pegar como tabla
"""

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from faro_editorial.carga import ResultadoArchivo, ResultadoCarga, motivo_corto
from faro_editorial.settings import ROOT_DIR

RUTA_CONFIG = ROOT_DIR / "config" / "fuentes_catalogo.yaml"
NOMBRE_CSV = "catalogo_datos.csv"
NOMBRE_MD = "catalogo_datos.md"

COLUMNAS = {
    "fuente": "Fuente",
    "url": "URL",
    "archivo": "Archivo",
    "fecha_extraccion": "Fecha de extracción (UTC)",
    "registros": "Registros cargados",
    "cobertura": "Cobertura",
    "campos": "Campos",
    "licencia": "Licencia / condiciones",
    "transformaciones": "Transformaciones",
    "hash": "SHA-256 del archivo",
    "excluidos": "Registros excluidos y motivo",
}

SIN_DATO = "sin dato"


def _rango(fechas: list[datetime], formato: str = "%Y-%m-%d") -> str:
    if not fechas:
        return SIN_DATO
    inicio, fin = min(fechas).strftime(formato), max(fechas).strftime(formato)
    return inicio if inicio == fin else f"{inicio} a {fin}"


def _fecha_extraccion(validos: list[BaseModel], fecha_corte: str | None) -> str:
    fechas = [v.fecha_extraccion for v in validos if hasattr(v, "fecha_extraccion")]
    if fechas:
        return _rango(fechas, "%Y-%m-%d %H:%M")
    if fecha_corte:
        return f"{fecha_corte[:16].replace('T', ' ')} (corte del manifest)"
    return SIN_DATO


def _cobertura(archivo: str, validos: list[Any]) -> str:
    if not validos:
        return "sin registros válidos"
    if archivo == "noticias.csv":
        fechas = [n.fecha_publicacion or n.fecha_deteccion for n in validos]
        con_fecha = [f for f in fechas if f]
        medios = len({n.medio for n in validos})
        return (
            f"{_rango(con_fecha)}; {len(validos)} noticias de {medios} medio(s); "
            f"{len(validos) - len(con_fecha)} sin fecha"
        )
    if archivo == "indicadores.csv":
        paises = ", ".join(sorted({i.pais_iso3 for i in validos}))
        indicadores = len({i.indicador_id for i in validos})
        anios = [i.anio for i in validos]
        con_valor = sum(i.valor is not None for i in validos)
        return (
            f"Países: {paises}; {indicadores} indicador(es); años {min(anios)}–{max(anios)}; "
            f"{con_valor} de {len(validos)} combinaciones con valor"
        )
    if archivo == "eventos.geojson":
        mags = [e.magnitude for e in validos]
        return (
            f"{_rango([e.time for e in validos])}; {len(validos)} sismos; "
            f"magnitud {min(mags)}–{max(mags)}"
        )
    return f"{len(validos)} registros"


def _excluidos(rechazos: list[Any]) -> str:
    if not rechazos:
        return "0"
    motivos = Counter(motivo_corto(m) for r in rechazos for m in r.motivos)
    detalle = "; ".join(f"{m} ({n})" for m, n in motivos.most_common())
    return f"{len(rechazos)}: {detalle}"


def _hash(integridad: dict[str, Any], archivo: str) -> str:
    info = integridad.get("archivos", {}).get(archivo)
    if not info:
        return f"{SIN_DATO} (manifest {integridad.get('manifest', SIN_DATO)})"
    sha = info["sha256_calculado"] or info["sha256_esperado"] or SIN_DATO
    return sha if info["estado"] == "ok" else f"{sha} ({info['estado']})"


def _origen_crudo(rechazo: Any) -> str | None:
    datos = rechazo.datos if isinstance(rechazo.datos, dict) else {}
    origen = datos.get("origen")
    return origen.strip() if isinstance(origen, str) and origen.strip() else None


def _por_archivo(resultado: ResultadoCarga) -> dict[str, ResultadoArchivo]:
    return {r.archivo: r for r in (resultado.noticias, resultado.indicadores, resultado.eventos)}


def generar_catalogo(
    resultado: ResultadoCarga, ruta_config: Path = RUTA_CONFIG
) -> list[dict[str, Any]]:
    config = yaml.safe_load(Path(ruta_config).read_text(encoding="utf-8"))
    comunes = config["transformaciones_comunes"].strip()
    archivos = _por_archivo(resultado)
    integridad = resultado.integridad
    fecha_corte = integridad.get("fecha_corte_utc")

    def fila(cfg: dict[str, Any], validos: list[Any], rechazos: list[Any]) -> dict[str, Any]:
        archivo = cfg["archivo"]
        modelo = type(validos[0]) if validos else None
        campos = ", ".join(modelo.model_fields) if modelo else SIN_DATO
        especificas = (cfg.get("transformaciones") or "").strip()
        return {
            "fuente": cfg["nombre"],
            "url": cfg.get("url") or SIN_DATO,
            "archivo": archivo,
            "fecha_extraccion": _fecha_extraccion(validos, fecha_corte),
            "registros": len(validos),
            "cobertura": _cobertura(archivo, validos),
            "campos": campos,
            "licencia": (cfg.get("licencia") or "por documentar").strip(),
            "transformaciones": f"{comunes} {especificas}".strip(),
            "hash": _hash(integridad, archivo),
            "excluidos": _excluidos(rechazos),
        }

    filas = []
    origenes_documentados = set()
    for cfg in config["fuentes"].values():
        resultado_archivo = archivos[cfg["archivo"]]
        validos, rechazos = resultado_archivo.validos, resultado_archivo.rechazos
        if origen := cfg.get("origen"):
            origenes_documentados.add(origen)
            validos = [v for v in validos if v.origen == origen]
            rechazos = [r for r in rechazos if _origen_crudo(r) == origen]
        filas.append(fila(cfg, validos, rechazos))

    # Noticias de un origen sin documentar: se listan igual para que nada quede oculto.
    noticias = resultado.noticias
    otros = {n.origen for n in noticias.validos} | {_origen_crudo(r) for r in noticias.rechazos}
    for origen in sorted(otros - origenes_documentados, key=lambda o: o or ""):
        cfg = {"nombre": f"Noticias · origen {origen or 'vacío'}", "archivo": "noticias.csv"}
        filas.append(
            fila(
                cfg,
                [n for n in noticias.validos if n.origen == origen],
                [r for r in noticias.rechazos if _origen_crudo(r) == origen],
            )
        )
    return filas


def _celda_md(valor: Any) -> str:
    return str(valor).replace("|", "\\|").replace("\n", " ")


def escribir_catalogo(filas: list[dict[str, Any]], processed_dir: Path) -> dict[str, Path]:
    processed_dir = Path(processed_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    rutas = {"csv": processed_dir / NOMBRE_CSV, "md": processed_dir / NOMBRE_MD}

    # utf-8-sig: Excel y Notion reconocen las tildes sin configurar nada.
    with rutas["csv"].open("w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=list(COLUMNAS.values()))
        escritor.writeheader()
        for fila in filas:
            escritor.writerow({COLUMNAS[k]: v for k, v in fila.items()})

    lineas = [
        "# Catálogo de datos",
        "",
        "Generado por `uv run python -m faro_editorial.carga`. "
        "Los datos fijos están en `config/fuentes_catalogo.yaml`.",
        "",
        "| " + " | ".join(COLUMNAS.values()) + " |",
        "|" + "---|" * len(COLUMNAS),
    ]
    for fila in filas:
        lineas.append("| " + " | ".join(_celda_md(fila[k]) for k in COLUMNAS) + " |")
    rutas["md"].write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return rutas
