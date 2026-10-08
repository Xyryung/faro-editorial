"""USGS · FDSN Event Web Service.

Primero /count con los mismos parámetros (la API rechaza consultas de más de 20 000
eventos con HTTP 400) y luego /query en GeoJSON. El GeoJSON se guarda tal como llega:
la carga ya aplana cada Feature. La caja regional no equivale al territorio de Panamá.

Período: el mismo de las noticias (data/CONTRATO.md), salvo que la configuración fije
inicio y fin. endtime es inclusivo en USGS y la ventana es [desde, hasta): por eso se pide
hasta un segundo antes de "hasta".
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from faro_editorial.extraccion.config import ConfigUSGS
from faro_editorial.extraccion.http import ClienteHTTP, ErrorExtraccion, Respuesta

FUENTE = "usgs"
MAX_EVENTOS = 20_000


def _hora_utc(fecha: datetime) -> str:
    return fecha.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def parametros(config: ConfigUSGS, desde: datetime, hasta: datetime) -> dict[str, Any]:
    params: dict[str, Any] = {
        "format": "geojson",
        "starttime": config.inicio or _hora_utc(desde),
        "endtime": config.fin or _hora_utc(hasta - timedelta(seconds=1)),
        "minlatitude": config.min_latitud,
        "maxlatitude": config.max_latitud,
        "minlongitude": config.min_longitud,
        "maxlongitude": config.max_longitud,
        "minmagnitude": config.min_magnitud,
    }
    if config.tipo_evento:
        params["eventtype"] = config.tipo_evento
    return params


def extraer_usgs(
    cliente: ClienteHTTP, config: ConfigUSGS, desde: datetime, hasta: datetime
) -> tuple[Respuesta, dict[str, Any]]:
    params = parametros(config, desde, hasta)
    try:
        conteo = cliente.get(
            f"{config.url}/count", fuente=FUENTE, params=params, respetar_robots=False
        ).json()
        esperado = int(conteo["count"])
        maximo = int(conteo.get("maxAllowed") or MAX_EVENTOS)
    except (KeyError, TypeError, ValueError) as e:
        raise ErrorExtraccion(f"conteo de USGS ilegible: {e}") from e
    if esperado > maximo:
        raise ErrorExtraccion(
            f"USGS reporta {esperado} eventos y el máximo por consulta es {maximo}: "
            "dividir el período en varias consultas"
        )

    respuesta = cliente.get(
        f"{config.url}/query",
        fuente=FUENTE,
        params={**params, "orderby": "time-asc"},
        respetar_robots=False,
    )
    try:
        recibidos = len(respuesta.json()["features"])
    except (KeyError, TypeError, ValueError) as e:
        raise ErrorExtraccion(f"GeoJSON de USGS ilegible: {e}") from e
    resumen = {"conteo_esperado": esperado, "eventos_recibidos": recibidos, "parametros": params}
    if recibidos != esperado:
        resumen["aviso"] = (
            f"/count informó {esperado} y /query devolvió {recibidos}: el catálogo pudo "
            "cambiar entre ambas llamadas"
        )
    return respuesta, resumen
