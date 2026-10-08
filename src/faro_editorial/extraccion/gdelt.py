"""GDELT DOC 2.0 API (modo ArtList).

- Máximo 250 artículos por consulta: la ventana se recorre en tramos, y un tramo que
  devuelve 250 (posiblemente truncado) se parte en dos hasta un mínimo de horas. El tamaño
  del tramo se adapta por consulta: tras partir un tramo, los siguientes empiezan con el
  tamaño que funcionó; si un tramo trae menos de la mitad del máximo, el siguiente se duplica
  (hasta dias_por_tramo). Así una consulta con mucho volumen no repite llamadas truncadas.
- La API solo busca en los últimos 3 meses: la ventana se recorta y el recorte se informa.
- GDELT no entrega fecha de publicación. seendate (cuándo GDELT detectó el artículo) va
  a fecha_deteccion y fecha_publicacion queda vacía, como pide la sección 7 del reto.
- A veces responde con un mensaje de texto en lugar de JSON: se registra como error del
  tramo y se continúa con los demás.
"""

from collections.abc import Callable
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
    progreso: Callable[[str], None] | None = None,
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

    def consultar(
        consulta: str, inicio: datetime, fin: datetime
    ) -> tuple[list[FilaNoticia], timedelta | None, int]:
        """Devuelve (filas, tamaño de tramo que no llegó al máximo si hubo que partir, cantidad
        de artículos de la respuesta sin partir)."""
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
            return [], None, MAX_REGISTROS
        except ValueError:
            texto = respuesta.texto.strip().replace("\n", " ")[:200]
            resumen["errores"].append(f"{consulta} [{params['startdatetime']}]: no JSON: {texto}")
            return [], None, MAX_REGISTROS
        nuevas = _filas(datos, respuesta.fecha_utc, medios)
        cantidad = len((datos or {}).get("articles") or []) if isinstance(datos, dict) else 0
        if cantidad >= MAX_REGISTROS:
            if fin - inicio > minimo * 2:
                resumen["tramos_partidos"] += 1
                medio_tramo = inicio + (fin - inicio) / 2
                filas_a, tam_a, _ = consultar(consulta, inicio, medio_tramo)
                filas_b, tam_b, _ = consultar(consulta, medio_tramo, fin)
                hojas = [t for t in (tam_a, tam_b) if t is not None] or [medio_tramo - inicio]
                return filas_a + filas_b, min(hojas), cantidad
            resumen["tramos_que_siguen_en_250"] += 1
        return nuevas, None, cantidad

    maximo = timedelta(days=config.dias_por_tramo)
    for n, consulta in enumerate(config.consultas, start=1):
        encontradas: list[FilaNoticia] = []
        tamano, inicio = maximo, desde
        while inicio < hasta:
            fin = min(inicio + tamano, hasta)
            if progreso:
                progreso(
                    f"gdelt {n}/{len(config.consultas)} {consulta} · {inicio:%Y-%m-%d} de "
                    f"{desde:%Y-%m-%d}→{hasta:%Y-%m-%d} · {len(encontradas)} artículos · "
                    f"{len(resumen['errores'])} errores"
                )
            nuevas, tam_hoja, cantidad = consultar(consulta, inicio, fin)
            encontradas.extend(nuevas)
            if tam_hoja is not None:
                tamano = tam_hoja  # hubo que partir: seguir con el tamaño que funcionó
            elif cantidad < MAX_REGISTROS // 2:
                tamano = min(tamano * 2, maximo)  # poco volumen: tramos más largos
            inicio = fin
        resumen["articulos_por_consulta"][consulta] = len(encontradas)
        filas.extend(encontradas)
    if resumen["llamadas"] and len(resumen["errores"]) == resumen["llamadas"]:
        raise ErrorExtraccion("todas las llamadas a GDELT fallaron: " + resumen["errores"][0])
    return filas, resumen
