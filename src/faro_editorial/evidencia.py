"""Muestra el registro original detrás de un ID de evidencia (#22, prueba dinámica del jurado:
"Muéstrame de dónde proviene esta cifra y de qué año es").

IDs aceptados, los mismos que aparecen en bandeja.json y en las citas:
- BM:<país>:<indicador>:<año>   indicador del Banco Mundial (p. ej. BM:PAN:NY.GDP.MKTP.KD.ZG:2023)
- USGS:<id del evento>          sismo del catálogo USGS
- <id_noticia>                  noticia del snapshot

Lee la base que genera la carga; no usa internet. Si el ID no existe, lo dice: no inventa.
La descripción del RSS no se muestra (decisión #35).

Uso:  uv run python -m faro_editorial.evidencia BM:PAN:NY.GDP.MKTP.KD.ZG:2023
"""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from faro_editorial.carga import NOMBRE_DB
from faro_editorial.contexto import PAISES, ZONA_PANAMA, formatear_valor

COLUMNAS_NOTICIA = (
    "id_noticia, titulo, url, medio, origen, idioma, fecha_publicacion, fecha_deteccion, "
    "fecha_extraccion, alcance_texto"
)


def _fecha(valor: Any) -> str:
    if not isinstance(valor, datetime):
        return "sin dato"
    utc = valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)
    local = utc.astimezone(ZONA_PANAMA)
    return f"{utc:%Y-%m-%d %H:%M} UTC ({local:%Y-%m-%d %H:%M} hora de Panamá)"


def _fila(con: duckdb.DuckDBPyConnection, sql: str, parametros: list[Any]) -> dict | None:
    cursor = con.execute(sql, parametros)
    fila = cursor.fetchone()
    if fila is None:
        return None
    return dict(zip([c[0] for c in cursor.description], fila, strict=True))


def buscar_evidencia(id_evidencia: str, ruta_db: Path) -> dict[str, Any]:
    """Devuelve {"tipo", "id", "encontrada", "campos": [(etiqueta, valor), ...], "nota"}."""
    id_evidencia = id_evidencia.strip()
    with duckdb.connect(str(ruta_db), read_only=True) as con:
        if id_evidencia.startswith("BM:"):
            partes = id_evidencia.split(":")
            if len(partes) != 4 or not partes[3].isdigit():
                return _no_encontrada(
                    "indicador", id_evidencia, "Formato: BM:<país>:<indicador>:<año>"
                )
            _, pais, indicador, anio = partes
            r = _fila(
                con,
                "SELECT * FROM indicadores WHERE pais_iso3 = ? AND indicador_id = ? AND anio = ?",
                [pais, indicador, int(anio)],
            )
            if r is None:
                return _no_encontrada("indicador", id_evidencia)
            valor = "sin dato publicado" if r["valor"] is None else formatear_valor(r["valor"])
            return {
                "tipo": "indicador",
                "id": id_evidencia,
                "encontrada": True,
                "campos": [
                    ("Fuente", "Banco Mundial"),
                    ("País", f"{PAISES.get(r['pais_iso3'], r['pais_iso3'])} ({r['pais_iso3']})"),
                    ("Indicador", r["indicador_id"]),
                    ("Año del dato", str(r["anio"])),
                    ("Valor", valor),
                    ("Unidad", r["unidad"] or "no indicada"),
                    ("URL de la consulta", r["fuente_url"]),
                    ("Extraído", _fecha(r["fecha_extraccion"])),
                    ("Licencia", r["licencia"]),
                ],
                "nota": f"Dato anual de {r['anio']}: describe ese año, no la situación de hoy.",
            }

        if id_evidencia.startswith("USGS:"):
            id_evento = id_evidencia.removeprefix("USGS:")
            r = _fila(con, "SELECT * FROM eventos WHERE id = ?", [id_evento])
            if r is None:
                return _no_encontrada("sismo", id_evidencia)
            profundidad = (
                f"{formatear_valor(r['depth'])} km" if r["depth"] is not None else "sin dato"
            )
            return {
                "tipo": "sismo",
                "id": id_evidencia,
                "encontrada": True,
                "campos": [
                    ("Fuente", "USGS · catálogo sísmico"),
                    ("Evento", r["id"]),
                    ("Magnitud", str(r["magnitude"])),
                    ("Hora", _fecha(r["time"])),
                    ("Lugar (según USGS)", r["place"] or "no indicado"),
                    ("Coordenadas", f"lat {r['latitude']}, lon {r['longitude']}"),
                    ("Profundidad", profundidad),
                    ("Estado", r["status"] or "sin dato"),
                    ("URL del evento", r["url"] or "sin dato"),
                ],
                "nota": (
                    "Solo respalda el hecho sísmico. La caja regional del catálogo no equivale "
                    "al territorio de Panamá."
                ),
            }

        r = _fila(
            con, f"SELECT {COLUMNAS_NOTICIA} FROM noticias WHERE id_noticia = ?", [id_evidencia]
        )
        if r is None:
            return _no_encontrada("noticia", id_evidencia)
        return {
            "tipo": "noticia",
            "id": id_evidencia,
            "encontrada": True,
            "campos": [
                ("Titular", r["titulo"]),
                ("Medio", r["medio"]),
                ("Origen", r["origen"]),
                ("URL", r["url"]),
                ("Publicada", _fecha(r["fecha_publicacion"])),
                ("Detectada (GDELT)", _fecha(r["fecha_deteccion"])),
                ("Extraída", _fecha(r["fecha_extraccion"])),
                ("Texto disponible", r["alcance_texto"] or "sin dato"),
            ],
            "nota": "Fecha de publicación y de detección se muestran por separado.",
        }


def _no_encontrada(tipo: str, id_evidencia: str, detalle: str | None = None) -> dict[str, Any]:
    nota = f"No existe evidencia con el ID {id_evidencia} en el snapshot cargado."
    if detalle:
        nota += f" {detalle}."
    return {"tipo": tipo, "id": id_evidencia, "encontrada": False, "campos": [], "nota": nota}


def main(argv: list[str] | None = None) -> None:
    from faro_editorial.settings import get_settings

    parser = argparse.ArgumentParser(
        description="Muestra el registro detrás de un ID de evidencia."
    )
    parser.add_argument(
        "id_evidencia", help="p. ej. BM:PAN:NY.GDP.MKTP.KD.ZG:2023 o USGS:us7000abcd"
    )
    args = parser.parse_args(argv)

    ruta_db = get_settings().processed_dir / NOMBRE_DB
    if not ruta_db.exists():
        raise SystemExit(
            "No hay base cargada: ejecuta antes  uv run python -m faro_editorial.carga"
        )
    resultado = buscar_evidencia(args.id_evidencia, ruta_db)
    print(f"Evidencia {resultado['id']}")
    ancho = max((len(e) for e, _ in resultado["campos"]), default=0)
    for etiqueta, valor in resultado["campos"]:
        print(f"  {etiqueta:<{ancho}}  {valor}")
    print(f"\n{resultado['nota']}")
    if not resultado["encontrada"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
