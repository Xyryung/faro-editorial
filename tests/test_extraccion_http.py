"""Cliente HTTP de la extracción y normalización (issue #3). Sin red: servidor falso."""

import re
from datetime import UTC, datetime

import httpx
import pytest
from falsos_http import ROBOTS_ABIERTO, UA, Reloj, Servidor, cliente_falso, respuesta

from faro_editorial.extraccion.http import BloqueadoPorRobots, ErrorExtraccion
from faro_editorial.extraccion.normalizar import (
    clave_url,
    formatear_fecha,
    id_noticia,
    leer_fecha,
    limpiar_titulo,
    normalizar_idioma,
)

# --- Cliente HTTP ---------------------------------------------------------------------


def test_envia_user_agent_y_guarda_la_respuesta_cruda(tmp_path):
    servidor = Servidor({"medio.example/robots.txt": ROBOTS_ABIERTO, "medio.example/rss": "<rss/>"})
    cliente = cliente_falso(servidor, tmp_path)

    r = cliente.get("https://medio.example/rss", fuente="medio_rss")

    assert servidor.solicitudes[-1].headers["user-agent"] == UA
    assert r.contenido == b"<rss/>" and r.fecha_utc == datetime(2026, 10, 7, 5, tzinfo=UTC)
    guardado = tmp_path / r.archivo
    assert guardado.read_bytes() == b"<rss/>"
    assert r.archivo.startswith("_respuestas/medio_rss/") and r.archivo.endswith(".xml")
    (consulta,) = cliente.consultas
    assert consulta["estado"] == 200 and consulta["sha256"] == r.sha256
    assert consulta["fecha_utc"] == "2026-10-07T05:00:00Z"


def test_robots_prohibido_no_hace_la_solicitud(tmp_path):
    servidor = Servidor({"medio.example/robots.txt": ROBOTS_ABIERTO, "medio.example/api/x": "{}"})
    cliente = cliente_falso(servidor, tmp_path)

    with pytest.raises(BloqueadoPorRobots, match="no permite /api/x"):
        cliente.get("https://medio.example/api/x", fuente="f")

    assert servidor.pedidas("/api/x") == []
    assert "no permite" in cliente.consultas[-1]["error"]


@pytest.mark.parametrize(
    ("estado", "permitido", "fragmento"),
    [(404, True, "sin reglas"), (403, False, "todo prohibido"), (503, False, "se asume")],
)
def test_robots_segun_su_estado_http(tmp_path, estado, permitido, fragmento):
    servidor = Servidor({"medio.example/robots.txt": (estado, "x"), "medio.example/rss": "<rss/>"})
    cliente = cliente_falso(servidor, tmp_path, reintentos=0)

    if permitido:
        cliente.get("https://medio.example/rss", fuente="f")
    else:
        with pytest.raises(BloqueadoPorRobots):
            cliente.get("https://medio.example/rss", fuente="f")
    assert fragmento in cliente.robots["medio.example"]


def test_robots_se_lee_una_sola_vez_por_host(tmp_path):
    servidor = Servidor({"medio.example/robots.txt": ROBOTS_ABIERTO, "medio.example/a": "a"})
    cliente = cliente_falso(servidor, tmp_path)
    for _ in range(3):
        cliente.get("https://medio.example/a", fuente="f")
    assert len(servidor.pedidas("robots.txt")) == 1


def test_las_apis_no_consultan_robots(tmp_path):
    servidor = Servidor({"api.example/v2/datos": {"ok": True}})
    cliente_falso(servidor, tmp_path).get(
        "https://api.example/v2/datos", fuente="api", respetar_robots=False
    )
    assert servidor.pedidas("robots.txt") == []


def test_pausa_entre_llamadas_al_mismo_host_y_crawl_delay(tmp_path):
    robots_lento = "User-agent: *\nCrawl-delay: 10\nDisallow:\n"
    servidor = Servidor(
        {
            "normal.example/robots.txt": ROBOTS_ABIERTO,
            "normal.example/b": "b",
            "lento.example/robots.txt": robots_lento,
            "lento.example/a": "a",
        }
    )
    reloj = Reloj()
    cliente = cliente_falso(servidor, tmp_path, reloj=reloj)

    cliente.get("https://normal.example/b", fuente="f")  # robots.txt y luego /b: 1 s
    cliente.get("https://normal.example/b", fuente="f")  # mismo host: 1 s
    cliente.get("https://lento.example/a", fuente="f")  # otro host, sin espera; Crawl-delay 10

    assert reloj.esperas == [1.0, 1.0, 10.0]
    assert "Crawl-delay 10" in cliente.robots["lento.example"]


def test_429_espera_larga_y_creciente(tmp_path):
    # GDELT sigue rechazando si se reintenta a los pocos segundos: 20 s, luego 40 s.
    intentos = iter([respuesta(429, "limite"), respuesta(429, "limite"), respuesta(200, "{}")])
    servidor = Servidor({"api.example/x": lambda r: next(intentos)})
    reloj = Reloj()
    cliente = cliente_falso(servidor, tmp_path, reloj=reloj, espera_429_s=20)

    cliente.get("https://api.example/x", fuente="api", respetar_robots=False)

    assert reloj.esperas[:2] == [20.0, 40.0]
    assert cliente.consultas[-1]["intentos"] == 3


def test_429_respeta_un_retry_after_mayor(tmp_path):
    intentos = iter([respuesta(429, "espere", {"retry-after": "45"}), respuesta(200, "{}")])
    servidor = Servidor({"api.example/x": lambda r: next(intentos)})
    reloj = Reloj()
    cliente_falso(servidor, tmp_path, reloj=reloj, espera_429_s=20).get(
        "https://api.example/x", fuente="api", respetar_robots=False
    )
    assert reloj.esperas[0] == 45.0


def test_se_rinde_tras_los_reintentos_y_lo_registra(tmp_path):
    servidor = Servidor({"api.example/x": (503, "caído")})
    reloj = Reloj()
    cliente = cliente_falso(servidor, tmp_path, reloj=reloj, reintentos=2)

    with pytest.raises(ErrorExtraccion, match="HTTP 503"):
        cliente.get("https://api.example/x", fuente="api", respetar_robots=False)

    assert len(servidor.solicitudes) == 3
    assert reloj.esperas[:2] == [2.0, 4.0]  # espera creciente
    assert cliente.consultas[-1]["estado"] == 503


def test_fallo_de_red_se_reintenta_y_luego_es_error(tmp_path):
    def caido(request):
        raise httpx.ConnectError("sin conexión", request=request)

    cliente = cliente_falso(Servidor({"api.example/x": caido}), tmp_path, reintentos=1)
    with pytest.raises(ErrorExtraccion, match="fallo de red"):
        cliente.get("https://api.example/x", fuente="api", respetar_robots=False)
    assert "ConnectError" in cliente.consultas[-1]["error"]


def test_respuesta_demasiado_grande_es_error(tmp_path):
    servidor = Servidor({"api.example/x": "x" * 50})
    cliente = cliente_falso(servidor, tmp_path, max_bytes=10)
    with pytest.raises(ErrorExtraccion, match="supera el máximo"):
        cliente.get("https://api.example/x", fuente="api", respetar_robots=False)


def test_parametros_quedan_en_la_url_registrada(tmp_path):
    servidor = Servidor({"api.example/x": "{}"})
    cliente = cliente_falso(servidor, tmp_path)
    cliente.get(
        "https://api.example/x",
        fuente="api",
        params={"q": "canal de panama"},
        respetar_robots=False,
    )
    assert cliente.consultas[-1]["url"] == "https://api.example/x?q=canal+de+panama"


# --- Normalización --------------------------------------------------------------------


def test_clave_url_reconoce_la_misma_noticia():
    base = clave_url("https://tvn-2.com/nacionales/canal_1_2.html")
    for variante in (
        "http://www.tvn-2.com/nacionales/canal_1_2.html",
        "https://WWW.TVN-2.com/nacionales/canal_1_2.html/",
        "https://tvn-2.com/nacionales/canal_1_2.html?utm_source=rss&fbclid=abc#comentarios",
    ):
        assert clave_url(variante) == base
    assert clave_url("https://tvn-2.com/nacionales/otra_1_3.html") != base
    assert clave_url("https://x.example/n?id=1") != clave_url("https://x.example/n?id=2")


def test_id_estable_con_el_formato_del_contrato():
    a = id_noticia("https://www.tvn-2.com/nacionales/canal_1_2.html?utm_medium=rss")
    assert a == id_noticia("https://tvn-2.com/nacionales/canal_1_2.html")
    assert re.fullmatch(r"tvn2-[0-9a-f]{12}", a)  # prefijo del medio + hash (CONTRATO.md)
    assert id_noticia("https://www.prensa.com/x").startswith("prensa-")


def test_limpiar_titulo():
    assert limpiar_titulo("Inflación &quot;moderada&quot;") == 'Inflación "moderada"'
    assert limpiar_titulo("Canal &amp;amp; puertos") == "Canal & puertos"
    assert limpiar_titulo("  Uno <b>dos</b>\n tres ") == "Uno dos tres"
    assert limpiar_titulo(None) == ""


def test_idiomas_y_fechas():
    assert normalizar_idioma("Spanish") == "es"
    assert normalizar_idioma("es-PA") == "es"
    assert normalizar_idioma("") is None
    assert leer_fecha("Sun, 04 Oct 2026 10:00:00 -0500") == datetime(2026, 10, 4, 15, tzinfo=UTC)
    assert leer_fecha("20261004T160000Z") == datetime(2026, 10, 4, 16, tzinfo=UTC)
    # Una fecha ilegible se conserva como texto para que la carga la rechace con motivo (T01).
    assert leer_fecha("ayer en la tarde") == "ayer en la tarde"


def test_fecha_con_formato_propio_y_zona():
    # Formato de Panamá América: mes primero, sin zona (hora de Panamá, UTC-5).
    formato, zona = "%m/%d/%Y - %H:%M", "America/Panama"
    esperado = datetime(2026, 10, 7, 5, 0, tzinfo=UTC)
    assert leer_fecha("Wed, 10/07/2026 - 00:00", formato, zona) == esperado
    assert leer_fecha("10/07/2026 - 00:00", formato, zona) == esperado
    # Sin zona configurada se asume UTC, como en el contrato.
    assert leer_fecha("10/07/2026 - 00:00", formato) == datetime(2026, 10, 7, tzinfo=UTC)
    # Si no calza con el formato, se intenta el análisis estándar.
    assert leer_fecha("2026-10-07T00:00:00Z", formato, zona) == datetime(2026, 10, 7, tzinfo=UTC)
    # Y si tampoco, queda como texto para que la carga lo rechace con su motivo.
    assert leer_fecha("13/45/2026 - 99:00", formato, zona) == "13/45/2026 - 99:00"
    assert formatear_fecha(datetime(2026, 10, 4, 15, tzinfo=UTC)) == "2026-10-04T15:00:00Z"
    assert formatear_fecha(None) == ""
