"""Banco Mundial · Indicators API v2.

- Una consulta por indicador para los seis países y el rango de años (sección 7 del reto).
- La cuadrícula país × indicador × año se completa: una combinación sin observación queda
  con valor vacío. Nunca se usan gapfill ni mrv, que rellenarían años faltantes.
- La unidad viene de la configuración (el campo "unit" de la API suele venir vacío).
- Se consultan los metadatos de cada indicador para registrar su organización de origen,
  que es donde aparecen las excepciones de licencia de terceros.
"""

import csv
from pathlib import Path
from typing import Any

from faro_editorial.extraccion.config import ConfigBancoMundial
from faro_editorial.extraccion.http import ClienteHTTP, ErrorExtraccion
from faro_editorial.extraccion.normalizar import formatear_fecha

FUENTE = "banco_mundial"
COLUMNAS = (
    "pais_iso3",
    "indicador_id",
    "anio",
    "valor",
    "unidad",
    "fuente_url",
    "fecha_extraccion",
    "licencia",
)


def _cuerpo(datos: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """La API responde [metadatos, filas] o, si hay error, [{"message": [...]}]."""
    if not isinstance(datos, list) or not datos or not isinstance(datos[0], dict):
        raise ErrorExtraccion("respuesta inesperada del Banco Mundial")
    if "message" in datos[0]:
        mensajes = datos[0]["message"]
        detalle = "; ".join(str(m.get("value") or m) for m in mensajes) if mensajes else ""
        raise ErrorExtraccion(f"error del Banco Mundial: {detalle}")
    filas = datos[1] if len(datos) > 1 and isinstance(datos[1], list) else []
    return datos[0], filas


def _metadatos(cliente: ClienteHTTP, config: ConfigBancoMundial, indicador: str) -> dict[str, Any]:
    url = f"{config.url}/indicator/{indicador}"
    try:
        _, filas = _cuerpo(
            cliente.get(url, fuente=FUENTE, params={"format": "json"}, respetar_robots=False).json()
        )
    except (ErrorExtraccion, ValueError) as e:
        return {"error": str(e)}
    if not filas:
        return {"error": "sin metadatos"}
    meta = filas[0]
    return {
        "nombre": meta.get("name"),
        "organizacion_origen": (meta.get("sourceOrganization") or "").strip() or None,
        "fuente": (meta.get("source") or {}).get("value"),
    }


def extraer_banco_mundial(
    cliente: ClienteHTTP, config: ConfigBancoMundial
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    paises = ";".join(config.paises)
    anios = range(config.anio_desde, config.anio_hasta + 1)
    filas: list[dict[str, Any]] = []
    resumen: dict[str, Any] = {"indicadores": {}, "combinaciones_completadas_con_nulo": 0}

    for indicador, unidad in config.indicadores.items():
        url = f"{config.url}/country/{paises}/indicator/{indicador}"
        meta = _metadatos(cliente, config, indicador)
        licencia = config.licencia
        if meta.get("organizacion_origen"):
            licencia += f". Organización de origen: {meta['organizacion_origen']}"
        encontradas: dict[tuple[str, int], dict[str, Any]] = {}
        pagina, paginas, ultima = 1, 1, None
        while pagina <= paginas:
            params = {
                "format": "json",
                "date": f"{config.anio_desde}:{config.anio_hasta}",
                "per_page": 1000,
                "page": pagina,
            }
            try:
                respuesta = cliente.get(url, fuente=FUENTE, params=params, respetar_robots=False)
                cabecera, datos = _cuerpo(respuesta.json())
            except ValueError as e:
                raise ErrorExtraccion(f"{indicador}: respuesta no JSON ({e})") from e
            except ErrorExtraccion as e:
                raise ErrorExtraccion(f"{indicador}: {e}") from e
            ultima = respuesta
            paginas = int(cabecera.get("pages") or 1)
            for d in datos:
                pais = d.get("countryiso3code") or ""
                if pais not in config.paises or not str(d.get("date", "")).isdigit():
                    continue
                encontradas[(pais, int(d["date"]))] = d
            pagina += 1

        assert ultima is not None
        fuente_url = f"{url}?format=json&date={config.anio_desde}:{config.anio_hasta}"
        completadas = 0
        for pais in config.paises:
            for anio in anios:
                dato = encontradas.get((pais, anio))
                valor = dato.get("value") if dato else None
                completadas += dato is None
                filas.append(
                    {
                        "pais_iso3": pais,
                        "indicador_id": indicador,
                        "anio": anio,
                        # Se escribe tal como lo entrega la API (sin redondear ni rellenar).
                        "valor": "" if valor is None else str(valor),
                        "unidad": unidad,
                        "fuente_url": fuente_url,
                        "fecha_extraccion": formatear_fecha(ultima.fecha_utc),
                        "licencia": licencia,
                    }
                )
        resumen["combinaciones_completadas_con_nulo"] += completadas
        resumen["indicadores"][indicador] = {
            **meta,
            "unidad": unidad,
            "con_valor": sum(1 for d in encontradas.values() if d.get("value") is not None),
            "combinaciones": len(config.paises) * len(anios),
        }
    return filas, resumen


def escribir_indicadores(filas: list[dict[str, Any]], ruta: Path) -> None:
    with Path(ruta).open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS, lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(filas)
