"""Fuentes web: feeds RSS/Atom y sitemaps de noticias.

Se leen titular, enlace, fechas, idioma y la descripción del feed. La descripción se guarda
solo para análisis interno (decisión #35): nunca se copia en borradores, no se sube al repo y
el paquete de entrega la quita. El contenido completo (content:encoded) se ignora.
"""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

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


def excluida(url: str, fuente: FuenteWeb) -> bool:
    """La ruta de la URL empieza con un prefijo de excluir_rutas (sección sin interés)."""
    ruta = urlsplit(url).path
    return any(ruta.startswith(prefijo) for prefijo in fuente.excluir_rutas)


def _fecha(fuente: FuenteWeb, texto: str | None):
    return leer_fecha(texto, fuente.formato_fecha, fuente.zona_horaria)


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
    por_ruta = 0
    for item in items:
        titulo, enlace = limpiar_titulo(_texto(item, "title")), _enlace(item)
        if not titulo or not enlace:
            continue
        if excluida(enlace, fuente):
            por_ruta += 1
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
                fecha_publicacion=_fecha(fuente, publicada),
                alcance_texto=ALCANCE_DESCRIPCION if descripcion else ALCANCE_TITULAR,
                descripcion=descripcion,
            )
        )
    return filas, {
        "items": len(items),
        "omitidos_sin_titulo_o_enlace": len(items) - len(filas) - por_ruta,
        "excluidos_por_ruta": por_ruta,
    }


# --- Sitemaps de noticias -------------------------------------------------------------


def _dentro_del_mes(fecha: datetime, mes: tuple[int, int] | None) -> bool:
    """Tolerancia de un día a cada lado: no se sabe en qué zona horaria agrupa el sitio."""
    if mes is None:
        return True
    anio, numero = mes
    inicio = datetime(anio, numero, 1, tzinfo=UTC)
    fin = datetime(anio + (numero == 12), numero % 12 + 1, 1, tzinfo=UTC)
    return inicio - timedelta(days=1) <= fecha < fin + timedelta(days=1)


def parsear_sitemap(
    contenido: bytes,
    fuente: FuenteWeb,
    fecha_extraccion: datetime,
    mes: tuple[int, int] | None = None,
) -> tuple[list[FilaNoticia], dict[str, int]]:
    """news:title y news:publication_date si existen; si no, image:title. Sin
    publication_date, con fecha_lastmod la fecha es el lastmod (aproximación: es igual o
    posterior a la publicación) si cae dentro del mes del sitemap; si no, queda vacía."""
    raiz = _raiz(contenido)
    if _local(raiz.tag) == "sitemapindex":
        raise ErrorExtraccion("es un índice de sitemaps: configure el sitemap hijo")
    if _local(raiz.tag) != "urlset":
        raise ErrorExtraccion(f"no es un sitemap (raíz <{_local(raiz.tag)}>)")
    filas: list[FilaNoticia] = []
    entradas = [e for e in raiz if _local(e.tag) == "url"]
    con_imagen = por_ruta = de_lastmod = lastmod_fuera = 0
    for entrada in entradas:
        enlace = _texto(entrada, "loc")
        if enlace and excluida(enlace, fuente):
            por_ruta += 1
            continue
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
        fecha = _fecha(fuente, publicada)
        if fecha is None and fuente.fecha_lastmod:
            lastmod = _fecha(fuente, _texto(entrada, "lastmod"))
            if isinstance(lastmod, datetime) and _dentro_del_mes(lastmod, mes):
                fecha = lastmod
                de_lastmod += 1
            elif lastmod is not None:
                lastmod_fuera += 1
        filas.append(
            FilaNoticia(
                titulo=titulo,
                url=enlace,
                medio=fuente.medio,
                origen=fuente.origen,
                fuente=fuente.id,
                fecha_extraccion=fecha_extraccion,
                idioma=normalizar_idioma(idioma or fuente.idioma),
                fecha_publicacion=fecha,
                alcance_texto=alcance,
            )
        )
    return filas, {
        "items": len(entradas),
        "omitidos_sin_titulo_o_enlace": len(entradas) - len(filas) - por_ruta,
        "excluidos_por_ruta": por_ruta,
        "titulo_de_imagen": con_imagen,
        "fecha_de_lastmod": de_lastmod,
        "lastmod_fuera_del_mes": lastmod_fuera,
    }


# --- Extracción -----------------------------------------------------------------------


def meses(desde: datetime, hasta: datetime) -> list[tuple[int, int]]:
    """Meses (año, mes) que tocan la ventana [desde, hasta). Un mes que la ventana toca solo
    en su primer día no cuenta: la ventana de la demo termina a medianoche de Panamá
    (05:00 UTC del día 1), y octubre no debe ocupar uno de los meses_maximos."""
    margen = timedelta(days=1) if hasta - desde > timedelta(days=1) else timedelta(microseconds=1)
    fin = hasta - margen
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
    objetivos: list[tuple[str, tuple[int, int] | None]]
    if fuente.tipo == "sitemap_mensual":
        lista = meses(desde, hasta)
        if fuente.meses_maximos:
            lista = lista[-fuente.meses_maximos :]
        objetivos = [(fuente.url.format(anio=a, mes=m), (a, m)) for a, m in lista]
    else:
        objetivos = [(fuente.url, None)]
    filas: list[FilaNoticia] = []
    resumen: dict[str, Any] = {"urls": len(objetivos), "items": 0, "errores": []}
    for url, mes in objetivos:
        try:
            respuesta = cliente.get(url, fuente=fuente.id)
            if fuente.tipo == "rss":
                nuevas, detalle = parsear_feed(respuesta.contenido, fuente, respuesta.fecha_utc)
            else:
                nuevas, detalle = parsear_sitemap(
                    respuesta.contenido, fuente, respuesta.fecha_utc, mes
                )
        except ErrorExtraccion as e:
            resumen["errores"].append(f"{url}: {e}")
            continue
        filas.extend(nuevas)
        for clave, valor in detalle.items():
            resumen[clave] = resumen.get(clave, 0) + valor
    if len(resumen["errores"]) == len(objetivos):
        raise ErrorExtraccion("; ".join(resumen["errores"]))
    return filas, resumen
