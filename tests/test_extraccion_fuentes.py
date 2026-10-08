"""Fuentes de la extracción (issue #3): feeds, sitemaps, GDELT, Banco Mundial, USGS y el
descubrimiento de feeds. Sin red: servidor falso con datos sintéticos."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from falsos_http import (
    AHORA,
    ATOM,
    GDELT,
    ROBOTS_ABIERTO,
    RSS,
    RSS_FECHA_DRUPAL,
    SITEMAP_INDICE,
    SITEMAP_MENSUAL_SEP,
    SITEMAP_NOTICIAS,
    Servidor,
    articulo_gdelt,
    bm_datos,
    bm_metadatos,
    cliente_falso,
    config_minima,
    usgs_geojson,
)

from faro_editorial.extraccion.banco_mundial import extraer_banco_mundial
from faro_editorial.extraccion.config import (
    RUTA_CONFIG,
    ConfigExtraccion,
    FuenteWeb,
    cargar_config,
)
from faro_editorial.extraccion.descubrir import bloque_yaml, clasificar, descubrir
from faro_editorial.extraccion.gdelt import extraer_gdelt, recortar_ventana, tramos
from faro_editorial.extraccion.http import ErrorExtraccion
from faro_editorial.extraccion.normalizar import (
    ALCANCE_DESCRIPCION,
    ALCANCE_TITULAR,
    ALCANCE_TITULO_IMAGEN,
)
from faro_editorial.extraccion.usgs import extraer_usgs, parametros
from faro_editorial.extraccion.web import extraer_web, meses, parsear_feed, parsear_sitemap

CONFIG = ConfigExtraccion.model_validate(config_minima())
TVN = CONFIG.web[0]
TELEMETRO = CONFIG.web[1]
DESDE, HASTA = datetime(2026, 9, 7, 5, tzinfo=UTC), AHORA


def utc(*partes: int) -> datetime:
    return datetime(*partes, tzinfo=UTC)


# --- Configuración --------------------------------------------------------------------


def test_la_configuracion_del_repo_es_valida():
    config = cargar_config(RUTA_CONFIG)
    assert config.web and config.gdelt and config.banco_mundial and config.usgs
    bm = config.banco_mundial
    combinaciones = len(bm.paises) * len(bm.indicadores) * (bm.anio_hasta - bm.anio_desde + 1)
    assert combinaciones == 540  # 6 × 6 × 15: el reto dice 1.350, pero no cuadra
    assert all("latest" not in f.url for f in config.web)


def test_configuracion_del_repo_para_la_demo():
    config = cargar_config(RUTA_CONFIG)
    por_id = {f.id: f for f in config.web}
    pa = por_id["panamaamerica_rss"]
    assert (pa.formato_fecha, pa.zona_horaria) == ("%m/%d/%Y - %H:%M", "America/Panama")
    tvn = por_id["tvn_sitemap_mensual"]
    assert "demo" in tvn.perfiles and tvn.fecha_lastmod and tvn.meses_maximos
    assert "/tvmax/" in tvn.excluir_rutas
    assert config.espera_429_s >= 20 and config.gdelt.pausa_s >= 5


def test_cada_origen_configurado_esta_documentado_en_el_catalogo():
    import yaml

    from faro_editorial.catalogo import RUTA_CONFIG as RUTA_CATALOGO

    catalogo = yaml.safe_load(RUTA_CATALOGO.read_text(encoding="utf-8"))
    documentados = {f.get("origen") for f in catalogo["fuentes"].values()}
    configurados = {f.origen for f in cargar_config(RUTA_CONFIG).web} | {"gdelt"}
    assert configurados <= documentados


def test_sitemap_mensual_exige_plantilla():
    with pytest.raises(ValueError, match="anio"):
        FuenteWeb(id="x", tipo="sitemap_mensual", medio="M", origen="o", url="https://x/s.xml")


# --- Feeds y sitemaps -----------------------------------------------------------------


def test_feed_rss_con_descripcion_y_sin_contenido():
    filas, resumen = parsear_feed(RSS.encode(), TVN, AHORA)

    assert resumen == {"items": 4, "omitidos_sin_titulo_o_enlace": 1, "excluidos_por_ruta": 0}
    agua, canal, vieja = filas
    assert agua.titulo == "Plan de agua para Colón & Panamá Oeste"
    assert agua.fecha_publicacion == utc(2026, 10, 5, 14, 30)
    assert canal.titulo == "Tránsito por el Canal se mantiene estable"
    assert canal.fecha_publicacion == utc(2026, 10, 4, 15)  # -0500 convertido a UTC
    assert agua.idioma == "es" and agua.medio == "TVN" and agua.origen == "tvn_rss"
    assert agua.fecha_deteccion is None
    # Decisión #35: la descripción se guarda sin HTML, solo para análisis interno.
    assert agua.descripcion == "Resumen sintético del plan de agua."
    assert agua.alcance_texto == ALCANCE_DESCRIPCION
    assert canal.descripcion is None and canal.alcance_texto == ALCANCE_TITULAR
    assert "CUERPO COMPLETO" not in json.dumps(agua.a_csv())  # content:encoded nunca
    assert agua.a_csv()["tema"] == ""
    assert vieja.fecha_publicacion == utc(2026, 5, 1, 1, 19, 41)


def test_feed_atom():
    fuente = FuenteWeb(
        id="inst",
        tipo="rss",
        medio="Institución",
        origen="oficial_rss",
        url="https://institucion.example/feed",
    )
    (fila,), _ = parsear_feed(ATOM.encode(), fuente, AHORA)
    assert fila.url == "https://institucion.example/comunicados/tarifas"
    assert fila.titulo == "Comunicado sobre tarifas de tránsito"
    assert fila.fecha_publicacion == utc(2026, 10, 2, 14)  # published, no updated
    assert fila.idioma == "es"


def test_sitemap_de_noticias_y_titulo_de_imagen():
    filas, resumen = parsear_sitemap(SITEMAP_NOTICIAS.encode(), TELEMETRO, AHORA)

    noticia, imagen = filas
    assert noticia.titulo == "Proponen regular scooters eléctricos"
    assert noticia.fecha_publicacion == utc(2026, 10, 7, 2, 20)
    assert noticia.alcance_texto == ALCANCE_TITULAR
    assert imagen.titulo == "Título tomado de la imagen"
    assert imagen.fecha_publicacion is None
    assert imagen.alcance_texto == ALCANCE_TITULO_IMAGEN
    assert resumen["omitidos_sin_titulo_o_enlace"] == 1 and resumen["titulo_de_imagen"] == 1


@pytest.mark.parametrize(
    ("contenido", "mensaje"),
    [
        (SITEMAP_INDICE, "índice"),
        ("<html><body>hola</body></html>", "no es un sitemap"),
        ("<urlset><url>", "XML inválido"),
    ],
)
def test_sitemap_invalido(contenido, mensaje):
    with pytest.raises(ErrorExtraccion, match=mensaje):
        parsear_sitemap(contenido.encode(), TELEMETRO, AHORA)


def test_meses_de_la_ventana():
    assert meses(utc(2025, 11, 20), utc(2026, 2, 1)) == [(2025, 11), (2025, 12), (2026, 1)]
    assert meses(utc(2026, 9, 7), utc(2026, 10, 7)) == [(2026, 9), (2026, 10)]
    # Ventana de la demo: termina a medianoche de Panamá (05:00 UTC del 1 de octubre).
    demo = meses(utc(2025, 10, 1, 5), utc(2026, 10, 1, 5))
    assert len(demo) == 12 and demo[0] == (2025, 10) and demo[-1] == (2026, 9)
    assert meses(utc(2026, 9, 23), utc(2026, 10, 1)) == [(2026, 9)]


def test_feed_con_formato_de_fecha_propio():
    fuente = FuenteWeb(
        id="pa",
        tipo="rss",
        medio="Panamá América",
        origen="medios_rss",
        url="https://www.panamaamerica.com.pa/rss/recent/index.xml",
        formato_fecha="%m/%d/%Y - %H:%M",
        zona_horaria="America/Panama",
    )
    (fila,), _ = parsear_feed(RSS_FECHA_DRUPAL.encode(), fuente, AHORA)
    assert fila.fecha_publicacion == utc(2026, 9, 30, 23, 30)  # 18:30 en Panamá


def test_zona_horaria_invalida_falla_al_cargar():
    with pytest.raises(ValueError):
        FuenteWeb(
            id="x",
            tipo="rss",
            medio="M",
            origen="o",
            url="https://x/f",
            zona_horaria="America/Ciudad_Inventada",
        )


TVN_MENSUAL = FuenteWeb(
    id="tvn_mensual",
    tipo="sitemap_mensual",
    medio="TVN",
    origen="tvn_sitemap",
    url="https://www.tvn-2.com/sitemap_{anio}_{mes:02d}.xml",
    excluir_rutas=["/tvmax/", "/videos/"],
    fecha_lastmod=True,
    meses_maximos=2,
)


def test_sitemap_mensual_con_lastmod_y_rutas_excluidas():
    filas, resumen = parsear_sitemap(SITEMAP_MENSUAL_SEP.encode(), TVN_MENSUAL, AHORA, (2026, 9))

    consulta, editada = filas
    assert consulta.fecha_publicacion == utc(2026, 9, 28, 15, 10, 0, 123456)
    assert consulta.alcance_texto == ALCANCE_TITULO_IMAGEN
    # lastmod fuera del mes del sitemap: no se usa como fecha (quedaría muy lejos).
    assert editada.fecha_publicacion is None
    assert resumen["excluidos_por_ruta"] == 2  # /tvmax/ y /videos/
    assert (resumen["fecha_de_lastmod"], resumen["lastmod_fuera_del_mes"]) == (1, 1)


def test_sitemap_sin_fecha_lastmod_no_usa_lastmod():
    fuente = TVN_MENSUAL.model_copy(update={"fecha_lastmod": False})
    filas, _ = parsear_sitemap(SITEMAP_MENSUAL_SEP.encode(), fuente, AHORA, (2026, 9))
    assert all(f.fecha_publicacion is None for f in filas)


def test_sitemap_mensual_solo_los_ultimos_meses(tmp_path):
    servidor = Servidor(
        {
            "www.tvn-2.com/robots.txt": ROBOTS_ABIERTO,
            "www.tvn-2.com/sitemap_2026_08.xml": SITEMAP_MENSUAL_SEP,
            "www.tvn-2.com/sitemap_2026_09.xml": SITEMAP_MENSUAL_SEP,
        }
    )
    filas, resumen = extraer_web(
        cliente_falso(servidor, tmp_path), TVN_MENSUAL, utc(2025, 10, 1, 5), utc(2026, 10, 1, 5)
    )
    pedidos = sorted(r.url.path for r in servidor.pedidas("sitemap_"))
    assert pedidos == ["/sitemap_2026_08.xml", "/sitemap_2026_09.xml"]  # no los 12 meses
    assert resumen["urls"] == 2 and not resumen["errores"]
    # En el archivo de agosto, el lastmod de septiembre queda fuera del mes (más de un día).
    assert resumen["fecha_de_lastmod"] == 1


def test_sitemap_mensual_sigue_aunque_falle_un_mes(tmp_path):
    fuente = FuenteWeb(
        id="tvn_mensual",
        tipo="sitemap_mensual",
        medio="TVN",
        origen="tvn_sitemap",
        perfiles=["entrenamiento"],
        url="https://www.tvn-2.com/sitemap_{anio}_{mes:02d}.xml",
    )
    servidor = Servidor(
        {
            "www.tvn-2.com/robots.txt": ROBOTS_ABIERTO,
            "www.tvn-2.com/sitemap_2026_09.xml": SITEMAP_NOTICIAS,
        }
    )
    filas, resumen = extraer_web(cliente_falso(servidor, tmp_path), fuente, DESDE, HASTA)

    assert len(filas) == 2 and resumen["urls"] == 2
    assert len(resumen["errores"]) == 1 and "sitemap_2026_10" in resumen["errores"][0]


def test_fuente_web_que_falla_por_completo_es_error(tmp_path):
    servidor = Servidor({"www.tvn-2.com/robots.txt": ROBOTS_ABIERTO})
    with pytest.raises(ErrorExtraccion, match="HTTP 404"):
        extraer_web(cliente_falso(servidor, tmp_path, reintentos=0), TVN, DESDE, HASTA)


# --- GDELT ----------------------------------------------------------------------------


def test_tramos_y_recorte_a_tres_meses():
    assert tramos(utc(2026, 9, 1), utc(2026, 9, 8), 3) == [
        (utc(2026, 9, 1), utc(2026, 9, 4)),
        (utc(2026, 9, 4), utc(2026, 9, 7)),
        (utc(2026, 9, 7), utc(2026, 9, 8)),
    ]
    desde, hasta, aviso = recortar_ventana(utc(2026, 1, 1), utc(2026, 12, 1), AHORA, 88)
    assert desde == utc(2026, 7, 11, 5) and hasta == AHORA and "3 meses" in aviso
    assert recortar_ventana(DESDE, HASTA, AHORA, 88)[2] is None


def test_gdelt_campos_y_parametros(tmp_path):
    servidor = Servidor({"api.gdeltproject.org/api/v2/doc/doc": GDELT})
    cliente = cliente_falso(servidor, tmp_path)
    config = CONFIG.gdelt.model_copy(update={"dias_por_tramo": 30})

    filas, resumen = extraer_gdelt(cliente, config, DESDE, HASTA, CONFIG.medios, AHORA)

    (req,) = servidor.solicitudes  # sin robots.txt: es una API documentada
    p = req.url.params
    assert (p["mode"], p["format"], p["maxrecords"]) == ("ArtList", "json", "250")
    assert (p["startdatetime"], p["enddatetime"]) == ("20260907050000", "20261007050000")
    assert p["query"] == "sourcecountry:panama"
    canal, inflacion, _ = filas
    assert canal.medio == "TVN" and inflacion.medio == "La Prensa"
    assert inflacion.titulo == 'Inflación "moderada" en septiembre'
    assert canal.fecha_deteccion == utc(2026, 10, 4, 16)
    assert canal.fecha_publicacion is None  # GDELT no informa la publicación
    assert canal.idioma == "es" and canal.origen == "gdelt"
    assert resumen["articulos_por_consulta"] == {"sourcecountry:panama": 3}
    assert cliente._pausas["api.gdeltproject.org"] == 5


def test_gdelt_parte_los_tramos_que_llegan_a_250(tmp_path):
    def respuesta_segun_tramo(request: httpx.Request) -> httpx.Response:
        inicio = datetime.strptime(request.url.params["startdatetime"], "%Y%m%d%H%M%S")
        fin = datetime.strptime(request.url.params["enddatetime"], "%Y%m%d%H%M%S")
        horas = (fin - inicio).total_seconds() / 3600
        n = 250 if horas > 24 else 10  # tramos de más de un día vienen truncados
        articulos = [
            articulo_gdelt(
                f"https://x.example/{inicio:%Y%m%d%H}/{i}",
                f"Titular {i}",
                f"{inicio:%Y%m%dT%H%M%S}Z",
                "x.example",
            )
            for i in range(n)
        ]
        return httpx.Response(200, json={"articles": articulos})

    servidor = Servidor({"api.gdeltproject.org/api/v2/doc/doc": respuesta_segun_tramo})
    config = CONFIG.gdelt.model_copy(update={"dias_por_tramo": 4})
    filas, resumen = extraer_gdelt(
        cliente_falso(servidor, tmp_path),
        config,
        utc(2026, 10, 1),
        utc(2026, 10, 5),
        {},
        AHORA,
    )
    # 4 días -> 2 días + 2 días (siguen en 250) -> 4 tramos de 1 día con 10 cada uno.
    assert resumen["tramos_partidos"] == 3
    assert len(filas) == 40 and resumen["llamadas"] == 7


def test_gdelt_adapta_el_tamano_del_tramo(tmp_path):
    """Una consulta con unos 200 artículos por día: tras partir el primer tramo, los demás
    empiezan con el tamaño que funcionó y no repiten llamadas truncadas."""
    por_dia = 200

    def respuesta_por_volumen(request: httpx.Request) -> httpx.Response:
        inicio = datetime.strptime(request.url.params["startdatetime"], "%Y%m%d%H%M%S")
        fin = datetime.strptime(request.url.params["enddatetime"], "%Y%m%d%H%M%S")
        n = min(int(por_dia * (fin - inicio).total_seconds() / 86400), 250)
        articulos = [
            articulo_gdelt(
                f"https://x.example/{inicio:%Y%m%d%H%M}/{i}",
                f"T {i}",
                f"{inicio:%Y%m%dT%H%M%S}Z",
                "x.example",
            )
            for i in range(n)
        ]
        return httpx.Response(200, json={"articles": articulos})

    servidor = Servidor({"api.gdeltproject.org/api/v2/doc/doc": respuesta_por_volumen})
    config = CONFIG.gdelt.model_copy(update={"dias_por_tramo": 8, "horas_minimas_tramo": 6})
    filas, resumen = extraer_gdelt(
        cliente_falso(servidor, tmp_path), config, utc(2026, 9, 1), utc(2026, 9, 17), {}, AHORA
    )
    # 16 días a 200 por día: 3200 artículos sin pérdidas (cada tramo final < 250).
    assert len(filas) == 3200 and resumen["tramos_que_siguen_en_250"] == 0
    # Sin adaptación serían 2 tramos de 8 días × 15 llamadas = 30. Con adaptación: el primer
    # tramo de 8 días cuesta 15 y los 8 días restantes van directo en tramos de 1 día.
    assert resumen["llamadas"] == 15 + 8


def test_gdelt_respuesta_de_texto_es_error_del_tramo(tmp_path):
    respuestas = iter(
        [
            httpx.Response(200, text="Please limit requests to one every 5 seconds"),
            httpx.Response(200, json=GDELT),
        ]
    )
    servidor = Servidor({"api.gdeltproject.org/api/v2/doc/doc": lambda r: next(respuestas)})
    filas, resumen = extraer_gdelt(
        cliente_falso(servidor, tmp_path), CONFIG.gdelt, DESDE, HASTA, CONFIG.medios, AHORA
    )
    assert len(filas) == 3
    assert "no JSON: Please limit" in resumen["errores"][0]


def test_gdelt_todo_falla_es_error(tmp_path):
    servidor = Servidor({"api.gdeltproject.org/api/v2/doc/doc": (500, "caído")})
    with pytest.raises(ErrorExtraccion, match="todas las llamadas"):
        extraer_gdelt(
            cliente_falso(servidor, tmp_path, reintentos=0),
            CONFIG.gdelt,
            DESDE,
            HASTA,
            CONFIG.medios,
            AHORA,
        )


# --- Banco Mundial --------------------------------------------------------------------

GDP = "NY.GDP.MKTP.KD.ZG"


def test_banco_mundial_completa_la_cuadricula_con_nulos(tmp_path):
    servidor = Servidor(
        {
            f"api.worldbank.org/v2/indicator/{GDP}": bm_metadatos(GDP),
            f"api.worldbank.org/v2/country/PAN;CRI/indicator/{GDP}": bm_datos(
                GDP, [("PAN", "2024", None), ("PAN", "2023", 7.3), ("CRI", "2023", 5.1)]
            ),
        }
    )
    filas, resumen = extraer_banco_mundial(cliente_falso(servidor, tmp_path), CONFIG.banco_mundial)

    por_clave = {(f["pais_iso3"], f["anio"]): f for f in filas}
    assert len(filas) == 4  # 2 países × 1 indicador × 2 años
    assert por_clave[("PAN", 2023)]["valor"] == "7.3"
    assert por_clave[("PAN", 2024)]["valor"] == ""  # la API dijo null: se conserva
    assert por_clave[("CRI", 2024)]["valor"] == ""  # la API no lo trajo: se completa
    assert por_clave[("PAN", 2023)]["unidad"] == "% anual"
    assert "Organización ficticia" in por_clave[("PAN", 2023)]["licencia"]
    assert por_clave[("PAN", 2023)]["fuente_url"].startswith("https://api.worldbank.org/v2/")
    assert resumen["combinaciones_completadas_con_nulo"] == 1
    assert resumen["indicadores"][GDP]["con_valor"] == 2
    pedida = servidor.pedidas("/country/")[0].url.params
    assert pedida["date"] == "2023:2024" and "gapfill" not in pedida and "mrv" not in pedida


def test_banco_mundial_recorre_paginas(tmp_path):
    def paginada(request):
        pagina = int(request.url.params["page"])
        filas = [("PAN", "2023", 7.3)] if pagina == 1 else [("CRI", "2024", 4.0)]
        return httpx.Response(200, json=bm_datos(GDP, filas, paginas=2))

    servidor = Servidor(
        {
            f"api.worldbank.org/v2/indicator/{GDP}": bm_metadatos(GDP),
            f"api.worldbank.org/v2/country/PAN;CRI/indicator/{GDP}": paginada,
        }
    )
    filas, _ = extraer_banco_mundial(cliente_falso(servidor, tmp_path), CONFIG.banco_mundial)
    con_valor = {(f["pais_iso3"], f["anio"]) for f in filas if f["valor"]}
    assert con_valor == {("PAN", 2023), ("CRI", 2024)}


def test_banco_mundial_mensaje_de_error(tmp_path):
    error = [{"message": [{"id": "120", "key": "Invalid value", "value": "Parámetro inválido"}]}]
    servidor = Servidor({f"api.worldbank.org/v2/country/PAN;CRI/indicator/{GDP}": error})
    with pytest.raises(ErrorExtraccion, match="Parámetro inválido"):
        extraer_banco_mundial(cliente_falso(servidor, tmp_path), CONFIG.banco_mundial)


# --- USGS -----------------------------------------------------------------------------


def test_usgs_cuenta_y_luego_consulta(tmp_path):
    servidor = Servidor(
        {
            "earthquake.usgs.gov/fdsnws/event/1/count": {"count": 2, "maxAllowed": 20000},
            "earthquake.usgs.gov/fdsnws/event/1/query": usgs_geojson(2),
        }
    )
    respuesta, resumen = extraer_usgs(cliente_falso(servidor, tmp_path), CONFIG.usgs, DESDE, HASTA)

    conteo, consulta = servidor.solicitudes
    assert conteo.url.path.endswith("/count") and consulta.url.path.endswith("/query")
    p = consulta.url.params
    assert (p["starttime"], p["endtime"], p["minmagnitude"]) == (
        "2026-09-07T05:00:00",
        "2026-10-07T04:59:59",
        "3.0",
    )
    assert conteo.url.params["endtime"] == p["endtime"]  # mismo filtro en /count y /query
    assert resumen["eventos_recibidos"] == 2 and "aviso" not in resumen
    assert len(json.loads(respuesta.contenido)["features"]) == 2


def test_usgs_demasiados_eventos_no_consulta(tmp_path):
    servidor = Servidor({"earthquake.usgs.gov/fdsnws/event/1/count": {"count": 25000}})
    with pytest.raises(ErrorExtraccion, match="dividir el período"):
        extraer_usgs(cliente_falso(servidor, tmp_path), CONFIG.usgs, DESDE, HASTA)
    assert servidor.pedidas("/query") == []


def test_usgs_conteo_distinto_genera_aviso(tmp_path):
    servidor = Servidor(
        {
            "earthquake.usgs.gov/fdsnws/event/1/count": {"count": 3, "maxAllowed": 20000},
            "earthquake.usgs.gov/fdsnws/event/1/query": usgs_geojson(2),
        }
    )
    _, resumen = extraer_usgs(cliente_falso(servidor, tmp_path), CONFIG.usgs, DESDE, HASTA)
    assert "informó 3" in resumen["aviso"]


def test_usgs_con_periodo_fijo_y_tipo_de_evento():
    config = CONFIG.usgs.model_copy(
        update={
            "inicio": "2024-01-01T00:00:00",
            "fin": "2024-12-31T23:59:59",
            "tipo_evento": "earthquake",
        }
    )
    params = parametros(config, DESDE, HASTA)
    assert (params["starttime"], params["endtime"]) == (
        "2024-01-01T00:00:00",
        "2024-12-31T23:59:59",
    )
    assert params["eventtype"] == "earthquake"


def test_usgs_sin_periodo_fijo_usa_la_ventana_de_las_noticias():
    config = CONFIG.usgs
    desde = datetime(2025, 10, 1, 5, tzinfo=UTC)  # medianoche de Panamá en UTC
    hasta = datetime(2026, 10, 1, 5, tzinfo=UTC)
    params = parametros(config, desde, hasta)
    assert params["starttime"] == "2025-10-01T05:00:00"
    assert params["endtime"] == "2026-10-01T04:59:59"  # endtime es inclusivo en USGS
    assert "eventtype" not in params  # la consulta del contrato no filtra por tipo


# --- Descubrimiento -------------------------------------------------------------------


def test_descubrir_feeds_y_sitemaps(tmp_path):
    portada = (
        '<html><head><link rel="alternate" type="application/rss+xml" href="/rss/portada">'
        '<link rel="stylesheet" href="/x.css"></head><body>Portada</body></html>'
    )
    servidor = Servidor(
        {
            "www.medio.example/robots.txt": "User-agent: *\nDisallow:\n"
            "Sitemap: https://www.medio.example/sitemap-news.xml\n"
            "Sitemap: https://www.medio.example/sitemap-index.xml\n",
            "www.medio.example/": portada,
            "www.medio.example/rss/portada": RSS,
            "www.medio.example/feed/": ATOM,
            "www.medio.example/sitemap-news.xml": SITEMAP_NOTICIAS,
            "www.medio.example/sitemap-index.xml": SITEMAP_INDICE,
        }
    )
    resultados = descubrir(cliente_falso(servidor, tmp_path), "https://www.medio.example")
    por_url = {r["url"]: r for r in resultados}

    assert por_url["https://www.medio.example/rss/portada"]["via"] == "portada"
    assert por_url["https://www.medio.example/rss/portada"]["items"] == 4
    assert por_url["https://www.medio.example/sitemap-news.xml"]["tipo"] == "sitemap"
    assert por_url["https://www.medio.example/sitemap-index.xml"]["tipo"] == "indice"
    assert por_url["https://www.medio.example/feed/"]["via"] == "ruta habitual"
    assert "https://www.medio.example/rss.xml" not in por_url  # 404 habitual: no se informa
    bloque = bloque_yaml("https://www.medio.example", resultados, {"medio.example": "Medio"})
    assert "tipo: sitemap" in bloque and "medio: Medio" in bloque
    assert "url: https://www.medio.example/rss/portada" in bloque


def test_clasificar():
    assert clasificar(RSS.encode())["tipo"] == "rss"
    assert clasificar(SITEMAP_NOTICIAS.encode())["con_news_title"] == 1
    assert clasificar(b"<html>")["tipo"] == "no_xml"
