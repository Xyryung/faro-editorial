"""Extracción de punta a punta (issue #3): lo que escribe el extractor lo acepta la carga.
Servidor falso con datos sintéticos; ninguna llamada sale a la red."""

import csv
import hashlib
import json
from datetime import UTC, datetime

import pytest
from falsos_http import AHORA, Servidor, cliente_falso, config_minima, rutas_completas

from faro_editorial.carga import cargar_snapshot
from faro_editorial.extraccion.config import ConfigExtraccion
from faro_editorial.extraccion.normalizar import COLUMNAS_NOTICIAS, FilaNoticia, id_noticia
from faro_editorial.extraccion.snapshot import (
    calcular_ventana,
    combinar,
    extraer,
    filtrar_ventana,
)

DESDE, HASTA = datetime(2026, 9, 7, 5, tzinfo=UTC), AHORA
ARCHIVOS = ("noticias.csv", "fuentes.json", "indicadores.csv", "eventos.geojson")


def ejecutar(tmp_path, rutas=None, config=None, **opciones):
    servidor = Servidor(rutas or rutas_completas())
    config = config or ConfigExtraccion.model_validate(config_minima())
    salida = tmp_path / "raw"
    cliente = cliente_falso(servidor, salida, reintentos=0)
    resultado = extraer(
        config, "demo", DESDE, HASTA, salida, cliente=cliente, ahora=AHORA, **opciones
    )
    return resultado, servidor


def leer_noticias(salida):
    with (salida / "noticias.csv").open(encoding="utf-8", newline="") as f:
        return {fila["url"]: fila for fila in csv.DictReader(f)}


def test_la_carga_acepta_todo_lo_que_escribe_el_extractor(tmp_path):
    resultado, _ = ejecutar(tmp_path)
    salida = resultado.salida

    assert resultado.errores == []
    carga = cargar_snapshot(salida, tmp_path / "processed")
    assert carga.integridad["ok"] is True
    assert carga.rechazos == []
    assert len(carga.noticias.validos) == 5
    assert len(carga.indicadores.validos) == 4  # cuadrícula 2 × 1 × 2
    assert len(carga.eventos.validos) == 2


def test_con_la_misma_ventana_la_carga_no_rechaza_nada(tmp_path):
    resultado, _ = ejecutar(tmp_path)
    carga = cargar_snapshot(resultado.salida, tmp_path / "processed", DESDE, HASTA)
    assert carga.rechazos == []
    with (resultado.salida / "noticias.csv").open(encoding="utf-8") as f:
        assert next(csv.reader(f)) == list(COLUMNAS_NOTICIAS)  # incluye descripcion


def test_manifest_con_hashes_consultas_y_transformaciones(tmp_path):
    resultado, servidor = ejecutar(tmp_path)
    manifest = json.loads((resultado.salida / "manifest.json").read_text(encoding="utf-8"))

    for nombre in ARCHIVOS:
        sha = hashlib.sha256((resultado.salida / nombre).read_bytes()).hexdigest()
        assert manifest["archivos"][nombre]["sha256"] == sha
        assert manifest["archivos"][nombre]["licencia"]
    assert manifest["fecha_corte_utc"] == "2026-10-07T05:00:00Z"
    assert manifest["fecha_extraccion_utc"] == "2026-10-07T05:00:00Z"
    assert manifest["version"] == "panama-senales-evidencias-v1"
    assert manifest["ventana"] == {
        "desde": "2026-09-07T05:00:00Z",
        "hasta": "2026-10-07T05:00:00Z",
        "aplica_a": ["noticias.csv", "eventos.geojson"],
    }
    consultas = manifest["consultas"]
    assert len(consultas) == len(servidor.solicitudes) - 2  # los robots.txt no se listan
    assert all((resultado.salida / c["archivo"]).exists() for c in consultas if "archivo" in c)
    assert manifest["robots_txt"]["www.tvn-2.com"] == "reglas aplicadas"
    assert any("tema queda vacío" in t for t in manifest["transformaciones"])


def test_fusion_ventana_y_campos_de_noticias(tmp_path):
    resultado, _ = ejecutar(tmp_path)
    noticias = leer_noticias(resultado.salida)
    por_titulo = {f["titulo"]: f for f in noticias.values()}

    # RSS + GDELT: una sola fila, publicación del RSS y detección de GDELT.
    canal = por_titulo["Tránsito por el Canal se mantiene estable"]
    assert canal["fecha_publicacion"] == "2026-10-04T15:00:00Z"
    assert canal["fecha_deteccion"] == "2026-10-04T16:00:00Z"
    assert canal["origen"] == "tvn_rss"
    assert canal["id_noticia"] == id_noticia("https://tvn-2.com/nacionales/canal_1_1000002.html")

    # Misma regla que la carga: manda la publicación. La nota de mayo queda fuera aunque
    # GDELT la haya detectado dentro de la ventana.
    assert "Nota antigua de mayo" not in por_titulo

    agua = por_titulo["Plan de agua para Colón & Panamá Oeste"]
    assert agua["descripcion"] == "Resumen sintético del plan de agua."
    assert agua["alcance_texto"] == "titular_descripcion"
    assert agua["id_noticia"].startswith("tvn2-")

    imagen = por_titulo["Título tomado de la imagen"]
    assert imagen["alcance_texto"] == "titulo_imagen_sitemap" and imagen["fecha_publicacion"] == ""

    assert all(f["tema"] == "" for f in noticias.values())
    assert "CUERPO COMPLETO" not in (resultado.salida / "noticias.csv").read_text(encoding="utf-8")
    estadisticas = resultado.manifest["resumen_fuentes"]["noticias"]
    assert estadisticas["duplicados_fusionados"] == 2
    assert estadisticas["fuera_de_ventana"] == 1
    assert estadisticas["noticias_tvn"] == 2


def test_fuentes_json_una_entrada_por_fuente_consultada(tmp_path):
    resultado, _ = ejecutar(tmp_path)
    fuentes = json.loads((resultado.salida / "fuentes.json").read_text(encoding="utf-8"))
    por_id = {f["fuente"]: f for f in fuentes}
    assert set(por_id) == {"tvn_rss", "telemetro_news", "gdelt"}
    assert por_id["tvn_rss"] | {"registros": 0} == {
        "fuente": "tvn_rss",
        "origen": "tvn_rss",
        "medio": "TVN",
        "url": "https://www.tvn-2.com/rss/",
        "consulta": "rss",
        "registros": 0,
    }
    assert por_id["tvn_rss"]["registros"] == 2
    assert por_id["telemetro_news"]["registros"] == 2
    assert por_id["gdelt"]["registros_por_medio"] == {"La Prensa": 1}
    assert por_id["gdelt"]["consulta"] == "sourcecountry:panama"


def test_el_paquete_de_entrega_quita_la_descripcion(tmp_path):
    from zipfile import ZipFile

    from faro_editorial.paquete import construir_paquete

    resultado, _ = ejecutar(tmp_path)
    ruta = construir_paquete(resultado.salida, tmp_path / "processed", tmp_path / "dist")
    with ZipFile(ruta) as z:
        nombres = z.namelist()
        noticias = z.read("raw/noticias.csv").decode("utf-8")
    assert "descripcion" not in noticias.splitlines()[0]
    assert "Resumen sintético" not in noticias
    assert not any("_respuestas" in n for n in nombres)  # los crudos no van en el paquete


def test_no_pisa_un_snapshot_sin_permiso_y_los_ids_son_estables(tmp_path):
    primero, _ = ejecutar(tmp_path)
    ids = {f["id_noticia"] for f in leer_noticias(primero.salida).values()}

    with pytest.raises(FileExistsError, match="--sobrescribir"):
        ejecutar(tmp_path)

    segundo, _ = ejecutar(tmp_path, sobrescribir=True)
    assert {f["id_noticia"] for f in leer_noticias(segundo.salida).values()} == ids
    anteriores = list((segundo.salida / "_anteriores").iterdir())
    assert len(anteriores) == 1 and (anteriores[0] / "noticias.csv").exists()


def test_refrescar_solo_noticias_conserva_lo_demas(tmp_path):
    primero, _ = ejecutar(tmp_path)
    indicadores_antes = (primero.salida / "indicadores.csv").read_bytes()

    segundo, servidor = ejecutar(tmp_path, familias=["noticias"], sobrescribir=True)

    assert servidor.pedidas("worldbank") == [] and servidor.pedidas("usgs") == []
    assert (segundo.salida / "indicadores.csv").read_bytes() == indicadores_antes
    archivos = segundo.manifest["archivos"]
    assert archivos["indicadores.csv"]["generado_en_esta_ejecucion"] is False
    assert archivos["indicadores.csv"]["cantidad"] == 4
    assert archivos["noticias.csv"]["generado_en_esta_ejecucion"] is True
    assert cargar_snapshot(segundo.salida, tmp_path / "processed").integridad["ok"] is True


def test_una_fuente_caida_no_detiene_la_extraccion(tmp_path):
    rutas = rutas_completas()
    rutas["www.telemetro.com/sitemap-news.xml"] = (500, "caído")
    resultado, _ = ejecutar(tmp_path, rutas=rutas)

    assert any(e.startswith("telemetro_news:") and "HTTP 500" in e for e in resultado.errores)
    assert "error" in resultado.manifest["resumen_fuentes"]["telemetro_news"]
    assert len(resultado.noticias) == 3
    assert cargar_snapshot(resultado.salida, tmp_path / "processed").integridad["ok"] is True


def test_robots_que_prohibe_omite_la_fuente_sin_pedirla(tmp_path):
    rutas = rutas_completas()
    rutas["www.telemetro.com/robots.txt"] = "User-agent: *\nDisallow: /\n"
    resultado, servidor = ejecutar(tmp_path, rutas=rutas)

    assert servidor.pedidas("sitemap-news.xml") == []
    assert any("robots.txt" in e for e in resultado.errores)


def test_perfil_entrenamiento_no_corre_en_demo(tmp_path):
    datos = config_minima()
    datos["web"].append(
        {
            "id": "tvn_mensual",
            "tipo": "sitemap_mensual",
            "medio": "TVN",
            "origen": "tvn_sitemap",
            "url": "https://www.tvn-2.com/sitemap_{anio}_{mes:02d}.xml",
            "perfiles": ["entrenamiento"],
        }
    )
    resultado, servidor = ejecutar(tmp_path, config=ConfigExtraccion.model_validate(datos))
    assert servidor.pedidas("sitemap_2026") == []
    assert "tvn_mensual" not in resultado.manifest["resumen_fuentes"]


# --- Funciones puras ------------------------------------------------------------------


def fila(
    url,
    fuente="a",
    origen="o",
    publicada=None,
    detectada=None,
    alcance="titular_metadatos",
    descripcion=None,
):
    return FilaNoticia(
        titulo="t",
        url=url,
        medio="M",
        origen=origen,
        fuente=fuente,
        fecha_extraccion=AHORA,
        fecha_publicacion=publicada,
        fecha_deteccion=detectada,
        alcance_texto=alcance,
        descripcion=descripcion,
    )


def test_combinar_prefiere_fecha_de_publicacion_y_titular():
    d = datetime(2026, 10, 1, tzinfo=UTC)
    filas = [
        fila("https://x.example/n", fuente="gdelt", origen="gdelt", detectada=d),
        fila("https://www.x.example/n/", fuente="rss", origen="rss", publicada=d),
        fila("https://x.example/m", fuente="rss", alcance="titulo_imagen_sitemap"),
        fila("https://x.example/m", fuente="gdelt"),
        fila("https://x.example/k", fuente="gdelt", origen="gdelt", detectada=d),
        fila("https://x.example/k", fuente="rss", alcance="titular_descripcion", descripcion="R"),
    ]
    unicas, estadisticas = combinar(filas, ["rss", "gdelt"])
    n, m, k = unicas
    assert n.origen == "rss" and n.fecha_publicacion == d and n.fecha_deteccion == d
    assert m.alcance_texto == "titular_metadatos"
    # La descripción del RSS no se pierde aunque la fila principal sea otra.
    assert k.descripcion == "R" and k.fecha_deteccion == d
    assert k.alcance_texto == "titular_descripcion"
    assert estadisticas["duplicados_fusionados"] == 3


def test_filtrar_ventana():
    dentro = datetime(2026, 9, 20, tzinfo=UTC)
    antes = datetime(2026, 5, 1, tzinfo=UTC)
    filas = [
        fila("https://x/1", publicada=dentro),
        fila("https://x/2", publicada=antes),
        fila("https://x/3", publicada=antes, detectada=dentro),
        fila("https://x/4"),
        fila("https://x/5", publicada="fecha rota"),  # la carga la rechazará con motivo
        fila("https://x/6", publicada=HASTA),  # límite superior abierto
    ]
    conservadas, estadisticas = filtrar_ventana(filas, DESDE, HASTA)
    # x/3: la publicación manda, igual que en la carga, aunque la detección caiga dentro.
    assert [f.url for f in conservadas] == ["https://x/1", "https://x/4", "https://x/5"]
    assert estadisticas == {"fuera_de_ventana": 3, "sin_fecha": 2}


def test_calcular_ventana():
    env = (datetime(2025, 10, 1, 5, tzinfo=UTC), datetime(2026, 10, 1, 5, tzinfo=UTC))
    # Sin .env ni argumentos: los últimos días por defecto.
    assert calcular_ventana(None, 30, None, None, AHORA) == (DESDE, AHORA)
    # Demo: la ventana de .env, la misma que aplica la carga.
    assert calcular_ventana(None, 30, None, None, AHORA, env) == env
    # --dias cuenta hacia atrás desde el fin de la ventana.
    assert calcular_ventana(10, 30, None, None, AHORA, env)[0] == datetime(
        2026, 9, 21, 5, tzinfo=UTC
    )
    # --desde y --hasta mandan.
    assert calcular_ventana(None, 30, DESDE, HASTA, AHORA, env) == (DESDE, HASTA)
    with pytest.raises(ValueError, match="vacía"):
        calcular_ventana(None, 30, AHORA, DESDE, AHORA)


def test_el_corte_es_el_fin_de_la_ventana_y_no_la_extraccion(tmp_path):
    """data/CONTRATO.md: fecha_corte_utc es el fin de la ventana. La bandeja mide la urgencia
    desde ahí; con la hora de extracción, todo lo de la ventana tendría urgencia 0."""
    from faro_editorial.bandeja import fecha_referencia

    fin_ventana = datetime(2026, 10, 1, 5, tzinfo=UTC)  # medianoche de Panamá
    servidor = Servidor(rutas_completas())
    salida = tmp_path / "raw"
    resultado = extraer(
        ConfigExtraccion.model_validate(config_minima()),
        "demo",
        DESDE,
        fin_ventana,
        salida,
        cliente=cliente_falso(servidor, salida, reintentos=0),
        ahora=AHORA,  # extraído una semana después del fin de la ventana
    )
    assert resultado.manifest["fecha_corte_utc"] == "2026-10-01T05:00:00Z"
    assert resultado.manifest["fecha_extraccion_utc"] == "2026-10-07T05:00:00Z"

    carga = cargar_snapshot(salida, tmp_path / "processed", DESDE, fin_ventana)
    referencia, origen = fecha_referencia(tmp_path / "processed", carga.noticias.validos)
    assert referencia == fin_ventana and origen.startswith("corte del snapshot")
