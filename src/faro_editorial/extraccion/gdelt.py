"""GDELT DOC 2.0 API (modo ArtList).

- Máximo 250 artículos por consulta: la ventana se divide en tramos, y un tramo que
  devuelve 250 (posiblemente truncado) se parte en dos hasta un mínimo de horas.
- La API solo busca en los últimos 3 meses: la ventana se recorta y el recorte se informa.
- GDELT no entrega fecha de publicación. seendate (cuándo GDELT detectó el artículo) va
  a fecha_deteccion y fecha_publicacion queda vacía, como pide la sección 7 del reto.
- A veces responde con un mensaje de texto en lugar de JSON: se registra como error del
  tramo y se continúa con los demás.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from faro_editorial.extraccion.config import ConfigGdelt
from faro_editorial.extraccion.http import ClienteHTTP, ErrorExtraccion
from faro_editorial.extraccion.normalizar import (
    FilaNoticia,
    dominio,
    leer_fecha,
    limpiar_titulo,
    normalizar_idioma,
)

FUENTE = "gdelt"
ORIGEN = "gdelt"
MAX_REGISTROS = 250


def _formato(fecha: datetime) -> str:
    return fecha.astimezone(UTC).strftime("%Y%m%d%H%M%S")


def tramos(desde: datetime, hasta: datetime, dias: float) -> list[tuple[datetime, datetime]]:
    paso, resultado, inicio = timedelta(days=dias), [], desde
    while inicio < hasta:
        fin = min(inicio + paso, hasta)
        resultado.append((inicio, fin))
        inicio = fin
    return resultado


def recortar_ventana(
    desde: datetime, hasta: datetime, ahora: datetime, max_dias_atras: int
) -> tuple[datetime, datetime, str | None]:
    limite = ahora - timedelta(days=max_dias_atras)
    hasta = min(hasta, ahora)
    if desde >= limite:
        return desde, hasta, None
    aviso = (
        f"La DOC API solo busca en los últimos 3 meses: la ventana de GDELT empieza el "
        f"{limite:%Y-%m-%d %H:%M} UTC en lugar del {desde:%Y-%m-%d %H:%M} UTC."
    )
    return limite, hasta, aviso


def _filas(datos: Any, fecha_extraccion: datetime, medios: dict[str, str]) -> list[FilaNoticia]:
    articulos = datos.get("articles") if isinstance(datos, dict) else None
    filas = []
    for articulo in articulos or []:
        url, titulo = articulo.get("url"), limpiar_titulo(articulo.get("title"))
        if not url or not titulo:
            continue
        dom = (articulo.get("domain") or dominio(url)).lower().removeprefix("www.")
        filas.append(
            FilaNoticia(
                titulo=titulo,
                url=url,
                medio=medios.get(dom, dom),
                origen=ORIGEN,
                fuente=FUENTE,
                fecha_extraccion=fecha_extraccion,
                idioma=normalizar_idioma(articulo.get("language")),
                fecha_deteccion=leer_fecha(articulo.get("seendate")),
            )
        )
    return filas


def extraer_gdelt(
    cliente: ClienteHTTP,
    config: ConfigGdelt,
    desde: datetime,
    hasta: datetime,
    medios: dict[str, str],
    ahora: datetime,
) -> tuple[list[FilaNoticia], dict[str, Any]]:
    cliente.fijar_pausa(httpx.URL(config.url).host, config.pausa_s)
    desde, hasta, aviso = recortar_ventana(desde, hasta, ahora, config.max_dias_atras)
    resumen: dict[str, Any] = {
        "ventana": [_formato(desde), _formato(hasta)],
        "consultas": len(config.consultas),
        "llamadas": 0,
        "tramos_partidos": 0,
        "tramos_que_siguen_en_250": 0,
        "errores": [],
        "avisos": [aviso] if aviso else [],
        "articulos_por_consulta": {},
    }
    minimo = timedelta(hours=config.horas_minimas_tramo)
    filas: list[FilaNoticia] = []

    def consultar(consulta: str, inicio: datetime, fin: datetime) -> list[FilaNoticia]:
        params = {
            "query": consulta,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": MAX_REGISTROS,
            "sort": "DateDesc",
            "startdatetime": _formato(inicio),
            "enddatetime": _formato(fin),
        }
        resumen["llamadas"] += 1
        try:
            respuesta = cliente.get(config.url, fuente=FUENTE, params=params, respetar_robots=False)
            datos = respuesta.json()
        except ErrorExtraccion as e:
            resumen["errores"].append(f"{consulta} [{params['startdatetime']}]: {e}")
            return []
        except ValueError:
            texto = respuesta.texto.strip().replace("\n", " ")[:200]
            resumen["errores"].append(f"{consulta} [{params['startdatetime']}]: no JSON: {texto}")
            return []
        nuevas = _filas(datos, respuesta.fecha_utc, medios)
        cantidad = len((datos or {}).get("articles") or []) if isinstance(datos, dict) else 0
        if cantidad >= MAX_REGISTROS:
            if fin - inicio > minimo * 2:
                resumen["tramos_partidos"] += 1
                medio_tramo = inicio + (fin - inicio) / 2
                return consultar(consulta, inicio, medio_tramo) + consultar(
                    consulta, medio_tramo, fin
                )
            resumen["tramos_que_siguen_en_250"] += 1
        return nuevas

    for consulta in config.consultas:
        encontradas: list[FilaNoticia] = []
        for inicio, fin in tramos(desde, hasta, config.dias_por_tramo):
            encontradas.extend(consultar(consulta, inicio, fin))
        resumen["articulos_por_consulta"][consulta] = len(encontradas)
        filas.extend(encontradas)
    if resumen["llamadas"] and len(resumen["errores"]) == resumen["llamadas"]:
        raise ErrorExtraccion("todas las llamadas a GDELT fallaron: " + resumen["errores"][0])
    return filas, resumen
