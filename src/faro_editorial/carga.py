"""Etapa 1 · Cargar: lee el snapshot de data/raw/, lo valida y emite un reporte de calidad.

Una fila inválida nunca bloquea la carga (T01): se separa en rechazos.jsonl con su motivo
y el resto se carga en DuckDB. Salidas en data/processed/:

- faro.duckdb           tablas noticias, indicadores y eventos (fechas en UTC, nulos como NULL)
- rechazos.jsonl        una línea por fila rechazada: archivo, fila, id, motivos y datos crudos
- reporte_calidad.json  integridad SHA-256, conteos por archivo, rechazos y nulos por campo

Uso:  uv run python -m faro_editorial.carga
"""

import csv
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, ValidationError

from faro_editorial.contrato import VERSION_CONTRATO, Evento, Indicador, Manifest, Noticia

ARCHIVO_NOTICIAS = "noticias.csv"
ARCHIVO_FUENTES = "fuentes.json"
ARCHIVO_INDICADORES = "indicadores.csv"
ARCHIVO_EVENTOS = "eventos.geojson"
ARCHIVO_MANIFEST = "manifest.json"
ARCHIVOS_SNAPSHOT = (
    ARCHIVO_NOTICIAS,
    ARCHIVO_FUENTES,
    ARCHIVO_INDICADORES,
    ARCHIVO_EVENTOS,
)

COLUMNAS_EXTRA = "_columnas_extra"

NOMBRE_DB = "faro.duckdb"
NOMBRE_RECHAZOS = "rechazos.jsonl"
NOMBRE_REPORTE = "reporte_calidad.json"

ESQUEMAS = {
    "noticias": """
        id_noticia VARCHAR PRIMARY KEY, titulo VARCHAR NOT NULL, url VARCHAR NOT NULL,
        medio VARCHAR NOT NULL, idioma VARCHAR, fecha_publicacion TIMESTAMP,
        fecha_deteccion TIMESTAMP, fecha_extraccion TIMESTAMP NOT NULL, tema VARCHAR,
        origen VARCHAR NOT NULL, alcance_texto VARCHAR""",
    "indicadores": """
        pais_iso3 VARCHAR NOT NULL, indicador_id VARCHAR NOT NULL, anio INTEGER NOT NULL,
        valor DOUBLE, unidad VARCHAR, fuente_url VARCHAR NOT NULL,
        fecha_extraccion TIMESTAMP NOT NULL, licencia VARCHAR NOT NULL,
        PRIMARY KEY (pais_iso3, indicador_id, anio)""",
    "eventos": """
        id VARCHAR PRIMARY KEY, magnitude DOUBLE NOT NULL, time TIMESTAMP NOT NULL,
        updated TIMESTAMP, longitude DOUBLE NOT NULL, latitude DOUBLE NOT NULL,
        depth DOUBLE, place VARCHAR, status VARCHAR, url VARCHAR""",
}


@dataclass
class Rechazo:
    archivo: str
    fila: int | None
    id: str | None
    motivos: list[str]
    datos: Any

    def a_dict(self) -> dict[str, Any]:
        return {
            "archivo": self.archivo,
            "fila": self.fila,
            "id": self.id,
            "motivos": self.motivos,
            "datos": self.datos,
        }


@dataclass
class ResultadoArchivo:
    archivo: str
    presente: bool = False
    filas_leidas: int = 0
    validos: list[BaseModel] = field(default_factory=list)
    rechazos: list[Rechazo] = field(default_factory=list)
    error: str | None = None

    def resumen(self) -> dict[str, Any]:
        motivos = Counter(motivo_corto(m) for r in self.rechazos for m in r.motivos)
        nulos: dict[str, int] = {}
        if self.validos:
            for campo in type(self.validos[0]).model_fields:
                nulos[campo] = sum(getattr(v, campo) is None for v in self.validos)
        return {
            "presente": self.presente,
            "error": self.error,
            "filas_leidas": self.filas_leidas,
            "filas_validas": len(self.validos),
            "filas_rechazadas": len(self.rechazos),
            "motivos_rechazo": dict(motivos.most_common()),
            "nulos_por_campo": nulos,
        }


@dataclass
class ResultadoCarga:
    noticias: ResultadoArchivo
    indicadores: ResultadoArchivo
    eventos: ResultadoArchivo
    fuentes: dict[str, Any]
    integridad: dict[str, Any]
    reporte: dict[str, Any]
    rutas: dict[str, Path]

    @property
    def rechazos(self) -> list[Rechazo]:
        return self.noticias.rechazos + self.indicadores.rechazos + self.eventos.rechazos


# --- Lectura y validación -------------------------------------------------------------


def _motivos(error: ValidationError) -> list[str]:
    motivos = []
    for e in error.errors():
        campo = ".".join(str(p) for p in e["loc"]) or "fila"
        mensaje = e["msg"].removeprefix("Value error, ")
        motivos.append(f"{campo}: {mensaje}")
    return motivos


def motivo_corto(motivo: str) -> str:
    """Agrupa motivos para el reporte: 'fecha_publicacion: fecha inválida: 'x'' -> sin el valor."""
    return motivo.split(": '", 1)[0].split(': "', 1)[0]


def _leer_csv(ruta: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    # utf-8-sig tolera el BOM que agrega Excel. Los valores se leen como texto: nada se
    # convierte a número ni se rellena antes de validar.
    with ruta.open(encoding="utf-8-sig", newline="") as f:
        lector = csv.DictReader(f)
        for fila in lector:
            if None in fila:  # DictReader guarda las columnas sobrantes bajo la clave None
                fila[COLUMNAS_EXTRA] = fila.pop(None)
            # La fila 1 es el encabezado; line_num apunta a la última línea leída.
            yield lector.line_num, fila


def _validar_filas(
    resultado: ResultadoArchivo,
    filas: Iterator[tuple[int | None, Any]],
    construir: Callable[[Any], BaseModel],
    obtener_id: Callable[[Any], Any],
    clave: Callable[[BaseModel], Any],
    filtro: Callable[[BaseModel], str | None] | None = None,
) -> None:
    vistos: set[Any] = set()
    for numero, crudo in filas:
        resultado.filas_leidas += 1
        id_crudo = obtener_id(crudo)
        if isinstance(crudo, dict) and COLUMNAS_EXTRA in crudo:
            motivo = "fila con más columnas que el encabezado"
            resultado.rechazos.append(Rechazo(resultado.archivo, numero, id_crudo, [motivo], crudo))
            continue
        try:
            modelo = construir(crudo)
        except ValidationError as e:
            resultado.rechazos.append(
                Rechazo(resultado.archivo, numero, id_crudo, _motivos(e), crudo)
            )
            continue
        motivo = filtro(modelo) if filtro else None
        if motivo is None and clave(modelo) in vistos:
            motivo = f"id duplicado: {clave(modelo)}"
        if motivo:
            resultado.rechazos.append(Rechazo(resultado.archivo, numero, id_crudo, [motivo], crudo))
            continue
        vistos.add(clave(modelo))
        resultado.validos.append(modelo)


def _filtro_ventana(
    desde: datetime | None, hasta: datetime | None
) -> Callable[[BaseModel], str | None] | None:
    """Excluye noticias fuera de [desde, hasta). Usa la fecha de publicación y, si falta,
    la de detección. Sin ventana configurada no se excluye nada."""
    if desde is None and hasta is None:
        return None

    def filtro(noticia: BaseModel) -> str | None:
        fecha = noticia.fecha_publicacion or noticia.fecha_deteccion
        if fecha is None:
            return None
        if (desde and fecha < desde) or (hasta and fecha >= hasta):
            return "fuera de la ventana de fechas"
        return None

    return filtro


def cargar_noticias(
    ruta: Path, desde: datetime | None = None, hasta: datetime | None = None
) -> ResultadoArchivo:
    resultado = ResultadoArchivo(ruta.name)
    if not ruta.exists():
        return resultado
    resultado.presente = True
    _validar_filas(
        resultado,
        _leer_csv(ruta),
        Noticia.model_validate,
        lambda f: f.get("id_noticia"),
        lambda n: n.id_noticia,
        _filtro_ventana(desde, hasta),
    )
    return resultado


def cargar_indicadores(ruta: Path) -> ResultadoArchivo:
    resultado = ResultadoArchivo(ruta.name)
    if not ruta.exists():
        return resultado
    resultado.presente = True
    _validar_filas(
        resultado,
        _leer_csv(ruta),
        Indicador.model_validate,
        lambda f: "|".join(str(f.get(k)) for k in ("pais_iso3", "indicador_id", "anio")),
        lambda i: (i.pais_iso3, i.indicador_id, i.anio),
    )
    return resultado


def cargar_eventos(ruta: Path) -> ResultadoArchivo:
    resultado = ResultadoArchivo(ruta.name)
    if not ruta.exists():
        return resultado
    resultado.presente = True
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
        features = datos["features"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        resultado.error = f"GeoJSON ilegible: {e}"
        return resultado
    _validar_filas(
        resultado,
        ((i, f) for i, f in enumerate(features)),
        Evento.desde_feature,
        lambda f: f.get("id") if isinstance(f, dict) else None,
        lambda e: e.id,
    )
    return resultado


def leer_fuentes(ruta: Path) -> dict[str, Any]:
    if not ruta.exists():
        return {"presente": False}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        return {"presente": True, "error": f"JSON ilegible: {e}"}
    cantidad = len(datos) if isinstance(datos, list | dict) else None
    return {"presente": True, "entradas": cantidad}


# --- Integridad -----------------------------------------------------------------------


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def verificar_integridad(raw_dir: Path) -> dict[str, Any]:
    """Compara el SHA-256 de cada archivo con manifest.json. No detiene la carga: el
    resultado queda en el reporte para que una persona decida."""
    ruta_manifest = raw_dir / ARCHIVO_MANIFEST
    if not ruta_manifest.exists():
        return {"manifest": "ausente", "ok": False, "archivos": {}}
    try:
        manifest = Manifest.model_validate_json(ruta_manifest.read_text(encoding="utf-8-sig"))
    except ValidationError as e:
        return {"manifest": "inválido", "ok": False, "errores": _motivos(e), "archivos": {}}

    archivos: dict[str, Any] = {}
    for nombre in sorted(set(ARCHIVOS_SNAPSHOT) | set(manifest.archivos)):
        ruta = raw_dir / nombre
        esperado = manifest.archivos.get(nombre)
        calculado = sha256_archivo(ruta) if ruta.exists() else None
        if esperado is None:
            estado = "no declarado en manifest"
        elif calculado is None:
            estado = "archivo ausente"
        elif calculado.lower() == esperado.sha256.lower():
            estado = "ok"
        else:
            estado = "hash distinto"
        archivos[nombre] = {
            "estado": estado,
            "sha256_esperado": esperado.sha256.lower() if esperado else None,
            "sha256_calculado": calculado,
        }
    return {
        "manifest": "ok",
        "version": manifest.version,
        "fecha_corte_utc": manifest.fecha_corte_utc.isoformat(),
        "ok": all(a["estado"] == "ok" for a in archivos.values()),
        "archivos": archivos,
    }


# --- Escritura ------------------------------------------------------------------------


def _a_naive_utc(valor: Any) -> Any:
    # DuckDB TIMESTAMP no guarda zona: se almacena la hora UTC explícitamente.
    if isinstance(valor, datetime):
        return valor.astimezone(UTC).replace(tzinfo=None)
    return valor


def _escribir_duckdb(ruta: Path, tablas: dict[str, list[BaseModel]]) -> None:
    ruta.unlink(missing_ok=True)
    with duckdb.connect(str(ruta)) as con:
        for tabla, esquema in ESQUEMAS.items():
            con.execute(f"CREATE TABLE {tabla} ({esquema})")
            filas = tablas[tabla]
            if not filas:
                continue
            campos = list(type(filas[0]).model_fields)
            marcadores = ", ".join("?" for _ in campos)
            con.executemany(
                f"INSERT INTO {tabla} ({', '.join(campos)}) VALUES ({marcadores})",
                [[_a_naive_utc(getattr(f, c)) for c in campos] for f in filas],
            )


def _json_default(valor: Any) -> Any:
    if isinstance(valor, datetime):
        return valor.isoformat()
    return str(valor)


def cargar_snapshot(
    raw_dir: Path,
    processed_dir: Path,
    desde: datetime | None = None,
    hasta: datetime | None = None,
) -> ResultadoCarga:
    """Ejecuta la etapa completa de carga y escribe las salidas en processed_dir."""
    raw_dir, processed_dir = Path(raw_dir), Path(processed_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)

    integridad = verificar_integridad(raw_dir)
    noticias = cargar_noticias(raw_dir / ARCHIVO_NOTICIAS, desde, hasta)
    indicadores = cargar_indicadores(raw_dir / ARCHIVO_INDICADORES)
    eventos = cargar_eventos(raw_dir / ARCHIVO_EVENTOS)
    fuentes = leer_fuentes(raw_dir / ARCHIVO_FUENTES)

    rutas = {
        "db": processed_dir / NOMBRE_DB,
        "rechazos": processed_dir / NOMBRE_RECHAZOS,
        "reporte": processed_dir / NOMBRE_REPORTE,
    }
    _escribir_duckdb(
        rutas["db"],
        {
            "noticias": noticias.validos,
            "indicadores": indicadores.validos,
            "eventos": eventos.validos,
        },
    )

    rechazos = noticias.rechazos + indicadores.rechazos + eventos.rechazos
    with rutas["rechazos"].open("w", encoding="utf-8") as f:
        for r in rechazos:
            f.write(json.dumps(r.a_dict(), ensure_ascii=False, default=_json_default) + "\n")

    reporte = {
        "version_contrato": VERSION_CONTRATO,
        "generado_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "ventana_noticias": {
            "desde": desde.isoformat() if desde else None,
            "hasta": hasta.isoformat() if hasta else None,
        },
        "integridad": integridad,
        "archivos": {
            ARCHIVO_NOTICIAS: noticias.resumen(),
            ARCHIVO_INDICADORES: indicadores.resumen(),
            ARCHIVO_EVENTOS: eventos.resumen(),
            ARCHIVO_FUENTES: fuentes,
        },
        "totales": {
            "filas_leidas": noticias.filas_leidas + indicadores.filas_leidas + eventos.filas_leidas,
            "filas_validas": len(noticias.validos)
            + len(indicadores.validos)
            + len(eventos.validos),
            "filas_rechazadas": len(rechazos),
        },
    }
    rutas["reporte"].write_text(
        json.dumps(reporte, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return ResultadoCarga(noticias, indicadores, eventos, fuentes, integridad, reporte, rutas)


def main() -> None:
    # Importaciones locales: catalogo importa este módulo y settings no se necesita en pruebas.
    from faro_editorial.settings import get_settings

    s = get_settings()
    resultado = cargar_snapshot(s.raw_dir, s.processed_dir, s.noticias_desde, s.noticias_hasta)
    integridad = resultado.integridad
    print(f"Integridad del snapshot: {'OK' if integridad['ok'] else 'REVISAR'}")
    for nombre, info in integridad.get("archivos", {}).items():
        print(f"  {nombre}: {info['estado']}")
    if integridad["manifest"] != "ok":
        print(f"  manifest.json: {integridad['manifest']}")
    for nombre, info in resultado.reporte["archivos"].items():
        if "filas_leidas" not in info:
            continue
        if not info["presente"]:
            print(f"{nombre}: ausente")
            continue
        print(
            f"{nombre}: {info['filas_validas']} válidas, "
            f"{info['filas_rechazadas']} rechazadas de {info['filas_leidas']}"
            + (f" ({info['error']})" if info["error"] else "")
        )
    print(f"Reporte: {resultado.rutas['reporte']}")

    from faro_editorial.catalogo import escribir_catalogo, generar_catalogo

    rutas = escribir_catalogo(generar_catalogo(resultado), s.processed_dir)
    print(f"Catálogo para Notion: {rutas['csv']}")


if __name__ == "__main__":
    main()
