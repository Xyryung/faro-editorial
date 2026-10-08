"""Servidor HTTP falso y datos sintéticos para las pruebas de la extracción.

Todos los titulares y URLs de este archivo son inventados: el repositorio es público y no
debe contener contenido real de los medios.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from faro_editorial.extraccion.http import ClienteHTTP

AHORA = datetime(2026, 10, 7, 5, 0, tzinfo=UTC)
UA = "faro-editorial/0.1 (+https://github.com/Xyryung/faro-editorial)"

Ruta = bytes | str | dict | list | tuple | Callable[[httpx.Request], httpx.Response]


class Servidor:
    """Responde según "host/ruta" (sin parámetros) y guarda cada solicitud recibida.
    Valor: cuerpo (200), (estado, cuerpo), o una función que recibe la solicitud."""

    def __init__(self, rutas: dict[str, Ruta]) -> None:
        self.rutas = dict(rutas)
        self.solicitudes: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.solicitudes.append(request)
        ruta = self.rutas.get(f"{request.url.host}{request.url.path}")
        if ruta is None:
            return httpx.Response(404, text="no existe")
        if callable(ruta):
            return ruta(request)
        estado, cuerpo = ruta if isinstance(ruta, tuple) else (200, ruta)
        return respuesta(estado, cuerpo)

    def pedidas(self, fragmento: str) -> list[httpx.Request]:
        return [r for r in self.solicitudes if fragmento in str(r.url)]


def respuesta(estado: int, cuerpo: Any, cabeceras: dict[str, str] | None = None):
    if isinstance(cuerpo, dict | list):
        return httpx.Response(estado, json=cuerpo, headers=cabeceras)
    if isinstance(cuerpo, str):
        cuerpo = cuerpo.encode("utf-8")
    tipo = "application/xml" if cuerpo.lstrip().startswith(b"<") else "text/plain"
    return httpx.Response(
        estado, content=cuerpo, headers={"content-type": tipo, **(cabeceras or {})}
    )


class Reloj:
    """El tiempo solo avanza cuando el cliente duerme: las pausas son deterministas."""

    def __init__(self) -> None:
        self.t = 100.0
        self.esperas: list[float] = []

    def __call__(self) -> float:
        return self.t

    def dormir(self, segundos: float) -> None:
        self.esperas.append(round(segundos, 3))
        self.t += segundos


def cliente_falso(
    servidor: Servidor, tmp_path: Path, reloj: Reloj | None = None, **opciones: Any
) -> ClienteHTTP:
    reloj = reloj or Reloj()
    http = httpx.Client(transport=httpx.MockTransport(servidor), follow_redirects=True)
    return ClienteHTTP(
        UA,
        tmp_path / "_respuestas",
        http=http,
        dormir=reloj.dormir,
        reloj=reloj,
        ahora=lambda: AHORA,
        **opciones,
    )


# --- Datos sintéticos -----------------------------------------------------------------

ROBOTS_ABIERTO = "User-agent: *\nDisallow: /api/\n"

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>Medio Ficticio - Portada</title>
  <link>https://www.tvn-2.com</link>
  <language>es</language>
  <item>
    <title><![CDATA[Plan de agua para Colón &amp; Panamá Oeste]]></title>
    <link>https://www.tvn-2.com/nacionales/plan-agua_1_1000001.html</link>
    <description><![CDATA[<p>Resumen sintético del <b>plan</b> de agua.</p>]]></description>
    <content:encoded><![CDATA[<p>CUERPO COMPLETO QUE NUNCA SE GUARDA</p>]]></content:encoded>
    <pubDate>Mon, 05 Oct 2026 14:30:00 +0000</pubDate>
    <media:content url="https://static.example/x.jpg"/>
  </item>
  <item>
    <title>Tránsito por el Canal   se mantiene estable</title>
    <link>https://www.tvn-2.com/nacionales/canal_1_1000002.html?utm_source=rss</link>
    <pubDate>Sun, 04 Oct 2026 10:00:00 -0500</pubDate>
  </item>
  <item>
    <title>Nota antigua de mayo</title>
    <link>https://www.tvn-2.com/nacionales/vieja_1_0900000.html</link>
    <pubDate>Fri, 01 May 2026 01:19:41 +0000</pubDate>
  </item>
  <item>
    <title></title>
    <link>https://www.tvn-2.com/nacionales/sin-titulo_1_1000003.html</link>
  </item>
</channel>
</rss>
"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="es-PA">
  <title>Institución Ficticia</title>
  <entry>
    <title>Comunicado sobre &lt;b&gt;tarifas&lt;/b&gt; de tránsito</title>
    <link rel="alternate" href="https://institucion.example/comunicados/tarifas"/>
    <link rel="enclosure" href="https://institucion.example/archivo.pdf"/>
    <published>2026-10-02T09:00:00-05:00</published>
    <updated>2026-10-03T09:00:00-05:00</updated>
  </entry>
</feed>
"""

SITEMAP_NOTICIAS = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url>
    <loc>https://www.telemetro.com/nacionales/scooters-n1001</loc>
    <news:news>
      <news:publication><news:name>Ficticio</news:name><news:language>es</news:language>
      </news:publication>
      <news:publication_date>2026-10-06T21:20:00-05:00</news:publication_date>
      <news:title>Proponen regular scooters eléctricos</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://www.telemetro.com/nacionales/solo-imagen-n1002</loc>
    <image:image>
      <image:loc>https://static.example/img.jpg</image:loc>
      <image:title>Título tomado de la imagen</image:title>
    </image:image>
  </url>
  <url><loc>https://www.telemetro.com/sin-nada-n1003</loc></url>
</urlset>
"""

SITEMAP_INDICE = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.telemetro.com/sitemap-news.xml</loc></sitemap>
</sitemapindex>
"""


def articulo_gdelt(url: str, titulo: str, visto: str, dominio: str, idioma: str = "Spanish"):
    return {
        "url": url,
        "url_mobile": "",
        "title": titulo,
        "seendate": visto,
        "socialimage": "",
        "domain": dominio,
        "language": idioma,
        "sourcecountry": "Panama",
    }


GDELT = {
    "articles": [
        articulo_gdelt(
            "https://tvn-2.com/nacionales/canal_1_1000002.html",
            "Tránsito por el Canal se mantiene estable",
            "20261004T160000Z",
            "tvn-2.com",
        ),
        articulo_gdelt(
            "https://www.prensa.com/economia/inflacion-septiembre/",
            "Inflación &quot;moderada&quot; en septiembre",
            "20261003T120000Z",
            "prensa.com",
        ),
        # Nota antigua del RSS que GDELT detectó dentro de la ventana (T03).
        articulo_gdelt(
            "https://www.tvn-2.com/nacionales/vieja_1_0900000.html",
            "Nota antigua de mayo",
            "20261005T080000Z",
            "tvn-2.com",
        ),
    ]
}


def bm_datos(indicador: str, filas: list[tuple[str, str, float | None]], paginas: int = 1):
    return [
        {"page": 1, "pages": paginas, "per_page": 1000, "total": len(filas), "sourceid": "2"},
        [
            {
                "indicator": {"id": indicador, "value": "Nombre del indicador"},
                "country": {"id": pais[:2], "value": pais},
                "countryiso3code": pais,
                "date": anio,
                "value": valor,
                "unit": "",
                "obs_status": "",
                "decimal": 1,
            }
            for pais, anio, valor in filas
        ],
    ]


def bm_metadatos(indicador: str, organizacion: str = "Organización ficticia de cuentas"):
    return [
        {"page": 1, "pages": 1, "per_page": 50, "total": 1},
        [
            {
                "id": indicador,
                "name": "Nombre del indicador",
                "unit": "",
                "source": {"id": "2", "value": "World Development Indicators"},
                "sourceOrganization": organizacion,
            }
        ],
    ]


def usgs_geojson(n: int = 2) -> dict:
    return {
        "type": "FeatureCollection",
        "metadata": {"count": n},
        "features": [
            {
                "type": "Feature",
                "id": f"us7000z{i:03d}",
                "properties": {
                    "mag": 3.5 + i / 10,
                    "place": "Lugar ficticio",
                    "time": 1789862400000 + i * 86_400_000,  # 2026-09-20 en adelante (UTC)
                    "updated": 1790294400000,
                    "status": "reviewed",
                    "url": f"https://earthquake.usgs.gov/earthquakes/eventpage/us7000z{i:03d}",
                },
                "geometry": {"type": "Point", "coordinates": [-80.0, 8.0, 10.0]},
            }
            for i in range(n)
        ],
    }


def config_minima(**cambios: Any) -> dict:
    """Configuración pequeña para pruebas: 2 fuentes web, 1 consulta GDELT, 1 indicador."""
    base = {
        "version": "extraccion-prueba",
        "user_agent": UA,
        "pausa_s": 1.0,
        "reintentos": 2,
        "medios": {"tvn-2.com": "TVN", "prensa.com": "La Prensa", "telemetro.com": "Telemetro"},
        "web": [
            {
                "id": "tvn_rss",
                "tipo": "rss",
                "medio": "TVN",
                "origen": "tvn_rss",
                "url": "https://www.tvn-2.com/rss/",
            },
            {
                "id": "telemetro_news",
                "tipo": "sitemap",
                "medio": "Telemetro",
                "origen": "medios_sitemap",
                "url": "https://www.telemetro.com/sitemap-news.xml",
            },
        ],
        "gdelt": {
            "url": "https://api.gdeltproject.org/api/v2/doc/doc",
            "pausa_s": 5,
            "dias_por_tramo": 15,
            "consultas": ["sourcecountry:panama"],
        },
        "banco_mundial": {
            "url": "https://api.worldbank.org/v2",
            "paises": ["PAN", "CRI"],
            "anio_desde": 2023,
            "anio_hasta": 2024,
            "indicadores": {"NY.GDP.MKTP.KD.ZG": "% anual"},
            "licencia": "CC BY 4.0 (Banco Mundial)",
        },
        "usgs": {
            "url": "https://earthquake.usgs.gov/fdsnws/event/1",
            "min_latitud": 5,
            "max_latitud": 12,
            "min_longitud": -86,
            "max_longitud": -76,
            "min_magnitud": 3,
        },
    }
    base.update(cambios)
    return base


def gdelt_por_tramo(request: httpx.Request) -> httpx.Response:
    """Devuelve los artículos solo en el tramo que contiene sus fechas de detección."""
    inicio = request.url.params["startdatetime"]
    cuerpo = GDELT if inicio >= "20260922" else {}
    return httpx.Response(
        200, content=json.dumps(cuerpo).encode(), headers={"content-type": "application/json"}
    )


def rutas_completas() -> dict[str, Ruta]:
    """Todas las fuentes de config_minima respondiendo bien."""
    gdp = "NY.GDP.MKTP.KD.ZG"
    return {
        "www.tvn-2.com/robots.txt": ROBOTS_ABIERTO,
        "www.tvn-2.com/rss/": RSS,
        "www.telemetro.com/robots.txt": (404, "no hay"),
        "www.telemetro.com/sitemap-news.xml": SITEMAP_NOTICIAS,
        "api.gdeltproject.org/api/v2/doc/doc": gdelt_por_tramo,
        f"api.worldbank.org/v2/indicator/{gdp}": bm_metadatos(gdp),
        f"api.worldbank.org/v2/country/PAN;CRI/indicator/{gdp}": bm_datos(
            gdp, [("PAN", "2024", None), ("PAN", "2023", 7.3), ("CRI", "2023", 5.1)]
        ),
        "earthquake.usgs.gov/fdsnws/event/1/count": {"count": 2, "maxAllowed": 20000},
        "earthquake.usgs.gov/fdsnws/event/1/query": usgs_geojson(2),
    }
