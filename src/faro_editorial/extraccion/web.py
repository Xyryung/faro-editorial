"""Fuentes web: feeds RSS/Atom y sitemaps de noticias.

Se leen titular, enlace, fechas, idioma y la descripción del feed. La descripción se guarda
solo para análisis interno (decisión #35): nunca se copia en borradores, no se sube al repo y
el paquete de entrega la quita. El contenido completo (content:encoded) se ignora.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

from faro_editorial.extraccion.config import FuenteWeb
from faro_editorial.extraccion.http import ClienteHTTP, ErrorExtraccion
from faro_editorial.extraccion.normalizar import (
    ALCANCE_DESCRIPCION,
    ALCANCE_TITULAR,
    ALCANCE_TITULO_IMAGEN,
    FilaNoticia,
    leer_fecha,
    limpiar_descripcion,
    limpiar_titulo,
    normalizar_idioma,
)

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _hijo(elemento: ET.Element, nombre: str) -> ET.Element | None:
    for hijo in elemento:
        if _local(hijo.tag) == nombre:
            return hijo
    return None


def _texto(elemento: ET.Element | None, nombre: str) -> str | None:
    if elemento is None:
        return None
    hijo = _hijo(elemento, nombre)
    if hijo is None or hijo.text is None:
        return None
    return hijo.text.strip() or None


def _raiz(contenido: bytes) -> ET.Element:
    try:
        return ET.fromstring(contenido)
    except ET.ParseError as e:
        raise ErrorExtraccion(f"XML inválido: {e}") from e


# --- Feeds RSS 2.0 / Atom -------------------------------------------------------------


def _enlace(item: ET.Element) -> str | None:
    if enlace := _texto(item, "link"):  # RSS
        return enlace
    for hijo in item:  # Atom: <link rel="alternate" href="..."/>
        if _local(hijo.tag) == "link" and hijo.get("rel", "alternate") == "alternate":
            if href := (hijo.get("href") or "").strip():
                return href
    guid = _texto(item, "guid") or _texto(item, "id")
    return guid if guid and guid.startswith(("http://", "https://")) else None


def parsear_feed(
    contenido: bytes, fuente: FuenteWeb, fecha_extraccion: datetime
) -> tuple[list[FilaNoticia], dict[str, int]]:
    raiz = _raiz(contenido)
    if _local(raiz.tag) not in ("rss", "feed", "RDF"):
        raise ErrorExtraccion(f"no es un feed RSS/Atom (raíz <{_local(raiz.tag)}>)")
    canal = _hijo(raiz, "channel")
    idioma = normalizar_idioma(_texto(canal, "language") or raiz.get(XML_LANG) or fuente.idioma)
    filas: list[FilaNoticia] = []
    items = [e for e in raiz.iter() if _local(e.tag) in ("item", "entry")]
    for item in items:
        titulo, enlace = limpiar_titulo(_texto(item, "title")), _enlace(item)
        if not titulo or not enlace:
            continue
        publicada = (
            _texto(item, "pubDate")
            or _texto(item, "published")
            or _texto(item, "date")  # dc:date
            or _texto(item, "updated")
        )
        descripcion = limpiar_descripcion(_texto(item, "description") or _texto(item, "summary"))
        filas.append(
            FilaNoticia(
                titulo=titulo,
                url=enlace,
                medio=fuente.medio,
                origen=fuente.origen,
                fuente=fuente.id,
                fecha_extraccion=fecha_extraccion,
                idioma=idioma,
                fecha_publicacion=leer_fecha(publicada),
                alcance_texto=ALCANCE_DESCRIPCION if descripcion else ALCANCE_TITULAR,
                descripcion=descripcion,
            )
        )
    return filas, {"items": len(items), "omitidos_sin_titulo_o_enlace": len(items) - len(filas)}


# --- Sitemaps de noticias -------------------------------------------------------------


def parsear_sitemap(
    contenido: bytes, fuente: FuenteWeb, fecha_extraccion: datetime
) -> tuple[list[FilaNoticia], dict[str, int]]:
    """news:title y news:publication_date si existen; si no, image:title y sin fecha."""
    raiz = _raiz(contenido)
    if _local(raiz.tag) == "sitemapindex":
        raise ErrorExtraccion("es un índice de sitemaps: configure el sitemap hijo")
    if _local(raiz.tag) != "urlset":
        raise ErrorExtraccion(f"no es un sitemap (raíz <{_local(raiz.tag)}>)")
    filas: list[FilaNoticia] = []
    entradas = [e for e in raiz if _local(e.tag) == "url"]
    con_imagen = 0
    for entrada in entradas:
        enlace = _texto(entrada, "loc")
        noticia = _hijo(entrada, "news")
        titulo = limpiar_titulo(_texto(noticia, "title"))
        alcance, publicada, idioma = ALCANCE_TITULAR, None, None
        if noticia is not None:
            publicada = _texto(noticia, "publication_date")
            idioma = _texto(_hijo(noticia, "publication"), "language")
        if not titulo:
            titulo = limpiar_titulo(_texto(_hijo(entrada, "image"), "title"))
            alcance = ALCANCE_TITULO_IMAGEN
            con_imagen += bool(titulo)
        if not titulo or not enlace:
            continue
        filas.append(
            FilaNoticia(
                titulo=titulo,
                url=enlace,
                medio=fuente.medio,
                origen=fuente.origen,
                fuente=fuente.id,
                fecha_extraccion=fecha_extraccion,
                idioma=normalizar_idioma(idioma or fuente.idioma),
                fecha_publicacion=leer_fecha(publicada),
                alcance_texto=alcance,
            )
        )
    return filas, {
        "items": len(entradas),
        "omitidos_sin_titulo_o_enlace": len(entradas) - len(filas),
        "titulo_de_imagen": con_imagen,
    }


# --- Extracción -----------------------------------------------------------------------


def meses(desde: datetime, hasta: datetime) -> list[tuple[int, int]]:
    """Meses (año, mes) que tocan la ventana [desde, hasta)."""
    fin = hasta - timedelta(microseconds=1)
    anio, mes, resultado = desde.year, desde.month, []
    while (anio, mes) <= (fin.year, fin.month):
        resultado.append((anio, mes))
        anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return resultado


def extraer_web(
    cliente: ClienteHTTP, fuente: FuenteWeb, desde: datetime, hasta: datetime
) -> tuple[list[FilaNoticia], dict[str, Any]]:
    """Descarga y lee una fuente web. Un sitemap mensual que falla en un mes no detiene
    los demás meses; el error queda en el resumen."""
    parsear = parsear_feed if fuente.tipo == "rss" else parsear_sitemap
    if fuente.tipo == "sitemap_mensual":
        urls = [fuente.url.format(anio=a, mes=m) for a, m in meses(desde, hasta)]
    else:
        urls = [fuente.url]
    filas: list[FilaNoticia] = []
    resumen: dict[str, Any] = {"urls": len(urls), "items": 0, "errores": []}
    for url in urls:
        try:
            respuesta = cliente.get(url, fuente=fuente.id)
            nuevas, detalle = parsear(respuesta.contenido, fuente, respuesta.fecha_utc)
        except ErrorExtraccion as e:
            resumen["errores"].append(f"{url}: {e}")
            continue
        filas.extend(nuevas)
        for clave, valor in detalle.items():
            resumen[clave] = resumen.get(clave, 0) + valor
    if len(resumen["errores"]) == len(urls):
        raise ErrorExtraccion("; ".join(resumen["errores"]))
    return filas, resumen
