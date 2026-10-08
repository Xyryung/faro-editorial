"""Descubre feeds RSS/Atom y sitemaps de noticias de un sitio para agregarlos a
config/extraccion_v1.yaml. Respeta robots.txt y no descarga artículos: solo lee
robots.txt, la portada (etiquetas <link rel="alternate">) y rutas de feed habituales.

Uso:
    uv run python -m faro_editorial.extraccion.descubrir https://www.telemetro.com
"""

import argparse
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from faro_editorial.extraccion.config import RUTA_CONFIG, cargar_config
from faro_editorial.extraccion.http import ClienteHTTP, ErrorExtraccion

RUTAS_COMUNES = (
    "/feed/",
    "/rss/",
    "/rss.xml",
    "/feed.xml",
    "/index.xml",
    "/arc/outboundfeeds/rss/?outputType=xml",
)
TIPOS_FEED = ("application/rss+xml", "application/atom+xml")
MAX_SITEMAPS = 10


class _EnlacesAlternos(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.feeds: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower().split()
        if tag == "link" and "alternate" in rel and a.get("type", "").lower() in TIPOS_FEED:
            if a.get("href"):
                self.feeds.append(a["href"])


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def clasificar(contenido: bytes) -> dict[str, Any]:
    """Qué es un documento XML: feed, sitemap de noticias, sitemap general o índice."""
    try:
        raiz = ET.fromstring(contenido)
    except ET.ParseError:
        return {"tipo": "no_xml"}
    nombre = _local(raiz.tag)
    if nombre in ("rss", "feed", "RDF"):
        items = [e for e in raiz.iter() if _local(e.tag) in ("item", "entry")]
        return {"tipo": "rss", "items": len(items)}
    if nombre == "urlset":
        entradas = [e for e in raiz if _local(e.tag) == "url"]
        con_noticia = sum(any(_local(h.tag) == "news" for h in e) for e in entradas)
        tipo = "sitemap" if con_noticia else "sitemap_general"
        return {"tipo": tipo, "items": len(entradas), "con_news_title": con_noticia}
    if nombre == "sitemapindex":
        hijos = [
            (h.text or "").strip()
            for e in raiz
            for h in e
            if _local(e.tag) == "sitemap" and _local(h.tag) == "loc"
        ]
        return {"tipo": "indice", "items": len(hijos), "hijos": hijos}
    return {"tipo": f"xml <{nombre}>"}


def descubrir(cliente: ClienteHTTP, sitio: str) -> list[dict[str, Any]]:
    partes = urlsplit(sitio if "://" in sitio else f"https://{sitio}")
    base = f"{partes.scheme}://{partes.netloc}"
    candidatos: dict[str, str] = {}  # url -> cómo se encontró

    try:
        robots = cliente.get(f"{base}/robots.txt", fuente="descubrir", respetar_robots=False)
        for linea in robots.texto.splitlines():
            if linea.lower().startswith("sitemap:"):
                candidatos.setdefault(linea.split(":", 1)[1].strip(), "robots.txt")
    except ErrorExtraccion:
        pass
    try:
        portada = cliente.get(f"{base}/", fuente="descubrir")
        lector = _EnlacesAlternos()
        lector.feed(portada.texto)
        for href in lector.feeds:
            candidatos.setdefault(urljoin(f"{base}/", href), "portada")
    except ErrorExtraccion:
        pass
    for ruta in RUTAS_COMUNES:
        candidatos.setdefault(f"{base}{ruta}", "ruta habitual")

    resultados, sitemaps = [], 0
    for url, via in list(candidatos.items()):
        if via == "robots.txt":
            sitemaps += 1
            if sitemaps > MAX_SITEMAPS:
                continue
        try:
            respuesta = cliente.get(url, fuente="descubrir")
        except ErrorExtraccion as e:
            if via != "ruta habitual":  # las rutas habituales que no existen no se informan
                resultados.append({"url": url, "via": via, "tipo": "error", "error": str(e)})
            continue
        resultados.append({"url": url, "via": via, **clasificar(respuesta.contenido)})
    return resultados


def bloque_yaml(sitio: str, resultados: list[dict[str, Any]], medios: dict[str, str]) -> str:
    host = (urlsplit(sitio if "://" in sitio else f"https://{sitio}").hostname or sitio).lower()
    corto = host.removeprefix("www.")
    medio = medios.get(corto, corto)
    clave = corto.split(".")[0].replace("-", "_")
    lineas = []
    for r in resultados:
        if r["tipo"] not in ("rss", "sitemap") or not r.get("items"):
            continue
        sufijo = "rss" if r["tipo"] == "rss" else "news"
        lineas += [
            f"  - id: {clave}_{sufijo}",
            f"    tipo: {r['tipo']}",
            f"    medio: {medio}",
            f"    origen: medios_{'rss' if r['tipo'] == 'rss' else 'sitemap'}",
            f"    url: {r['url']}",
        ]
    return "\n".join(lineas)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m faro_editorial.extraccion.descubrir")
    parser.add_argument("sitios", nargs="+", help="Ej.: https://www.telemetro.com")
    parser.add_argument("--config", type=Path, default=RUTA_CONFIG)
    args = parser.parse_args(argv)
    config = cargar_config(args.config)
    with tempfile.TemporaryDirectory() as temporal:
        cliente = ClienteHTTP(
            config.user_agent, Path(temporal), timeout_s=config.timeout_s, pausa_s=config.pausa_s
        )
        for sitio in args.sitios:
            print(f"\n== {sitio}  (robots.txt: se consulta antes de cada página)")
            resultados = descubrir(cliente, sitio)
            for r in resultados:
                detalle = r.get("error") or f"{r.get('items', '')} elementos"
                print(f"  [{r['tipo']}] {r['url']}  ({r['via']}; {detalle})")
                for hijo in r.get("hijos", [])[:15]:
                    print(f"      hijo: {hijo}")
            bloque = bloque_yaml(sitio, resultados, config.medios)
            if bloque:
                print("\n  Para config/extraccion_v1.yaml (revisar medio y origen antes de pegar):")
                print(bloque)
            else:
                print("  Sin feeds ni sitemaps de noticias utilizables.")
            host = urlsplit(sitio if "://" in sitio else f"https://{sitio}").hostname or ""
            if robots := cliente.robots.get(host):
                print(f"  robots.txt: {robots}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
