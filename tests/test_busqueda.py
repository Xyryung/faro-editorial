"""Búsqueda híbrida y compuerta de abstención (issue #13, T06, CU-04).

Todo corre sin modelo descargado (TF-IDF) y sin Jev: la compuerta Noul en vivo se
verifica en la demo, aquí se prueba el umbral y la abstención explícita.
"""

import pytest

from faro_editorial.agrupacion import RepresentadorTfidf
from faro_editorial.busqueda import Buscador, load_config

DOCS = [
    {"id_noticia": "n1", "titulo": "Lluvias, crecidas y alertas en Chiriquí", "medio": "TVN"},
    {"id_noticia": "n2", "titulo": "Panama tourism arrivals rise", "medio": "example.org"},
    {"id_noticia": "n3", "titulo": "Tránsito por el Canal se mantiene estable", "medio": "TVN"},
]


@pytest.fixture
def buscador() -> Buscador:
    config = load_config()
    config = config.model_copy(update={"umbral": 0.05, "k_recuperados": 3})
    return Buscador(DOCS, config, RepresentadorTfidf())


def test_config_por_defecto():
    config = load_config()
    assert config.version.startswith("busqueda-v")
    assert 0 <= config.alfa <= 1 and config.k_recuperados >= 1


def test_respuesta_con_evidencia(buscador: Buscador):
    r = buscador.responder("lluvias y crecidas en Chiriquí", None)
    assert not r.abstencion
    assert r.citas and r.citas[0].id_noticia == "n1"
    assert all(c.titulo and c.medio for c in r.citas)


def test_t06_sin_respuesta_se_abstiene(buscador: Buscador):
    r = buscador.responder("resultado del partido de fútbol de ayer", None)
    assert r.abstencion
    assert r.citas == []
    assert r.falta and "snapshot" in r.falta
    assert r.motivo


def test_umbral_alto_abstiene_aunque_haya_tema(buscador: Buscador):
    buscador.config = buscador.config.model_copy(update={"umbral": 0.9999})
    r = buscador.responder("lluvias en Chiriquí", None)
    assert r.abstencion


def test_bm25_es_la_linea_base(buscador: Buscador):
    r = buscador.responder("tourism arrivals", None, metodo="bm25")
    assert not r.abstencion
    assert r.citas[0].id_noticia == "n2"
    assert r.metodo == "bm25"


def test_sin_jev_queda_registrado_el_metodo(buscador: Buscador):
    r = buscador.responder("canal estable", None)
    assert not r.abstencion
    assert r.motivo_respaldo and "umbral" in r.motivo_respaldo


def test_t07_la_consulta_muestra_el_titular_malicioso_como_texto():
    """Un titular con Markdown que la búsqueda encuentra no se convierte en enlace ni imagen."""
    from faro_editorial.interfaz import lineas_consulta

    maliciosos = [
        *DOCS,
        {
            "id_noticia": "x1](https://evil.example)",
            "titulo": "Lluvias en Chiriquí ![img](https://evil.example/a.png) [clic](https://evil.example)",
            "medio": "[TVN](https://evil.example)",
        },
    ]
    config = load_config().model_copy(update={"umbral": 0.05, "k_recuperados": 4})
    respuesta = Buscador(maliciosos, config, RepresentadorTfidf()).responder(
        "lluvias Chiriquí", None
    )
    textos = lineas_consulta(respuesta)
    assert not textos["abstencion"] and textos["citas"]
    assert any("evil" in linea for linea in textos["citas"])  # la nota maliciosa sí aparece…
    for linea in textos["citas"]:
        assert "](" not in linea and "![" not in linea  # …pero como texto, sin enlaces


def test_t07_la_abstencion_escapa_sus_motivos():
    from types import SimpleNamespace

    from faro_editorial.interfaz import lineas_consulta

    respuesta = SimpleNamespace(
        abstencion=True,
        motivo="Nada sobre [clic](https://evil.example)",
        falta="Una fuente ![x](https://evil.example/a.png)",
    )
    textos = lineas_consulta(respuesta)
    assert textos["abstencion"]
    assert "](" not in textos["motivo"] and "](" not in textos["falta"]
    assert lineas_consulta(SimpleNamespace(abstencion=True, motivo=None, falta=None))["falta"] == ""


class _RepresentadorContado:
    """Representador "e5" falso: cuenta cuántos textos codifica en cada llamada."""

    metodo = "e5"
    nombre = "falso-e5"

    def __init__(self) -> None:
        self.llamadas: list[int] = []

    def vectores(self, textos):
        import numpy as np

        self.llamadas.append(len(textos))
        filas = []
        for t in textos:
            v = np.array([t.lower().count(c) for c in "aeiouclnst"], dtype=np.float32) + 0.01
            filas.append(v / np.linalg.norm(v))
        return np.vstack(filas)


def test_con_embeddings_el_corpus_se_codifica_una_sola_vez():
    """Antes cada consulta recodificaba todo el corpus (~22 s con ~5000 titulares)."""
    rep = _RepresentadorContado()
    config = load_config().model_copy(update={"umbral": 0.0, "k_recuperados": 3})
    buscador = Buscador(DOCS, config, rep)
    assert buscador.preparar() >= 0 and rep.llamadas == [len(DOCS)]
    for consulta in ("lluvias en Chiriquí", "tránsito del Canal", "turismo"):
        buscador.responder(consulta, None)
    assert rep.llamadas == [len(DOCS), 1, 1, 1]  # luego, solo la consulta


def test_preparar_y_sin_preparar_dan_el_mismo_resultado():
    config = load_config().model_copy(update={"umbral": 0.0, "k_recuperados": 3})
    a = Buscador(DOCS, config, _RepresentadorContado())
    b = Buscador(DOCS, config, _RepresentadorContado())
    a.preparar()
    ra, rb = a.responder("lluvias en Chiriquí", None), b.responder("lluvias en Chiriquí", None)
    assert [c.id_noticia for c in ra.citas] == [c.id_noticia for c in rb.citas]
    assert [c.puntaje for c in ra.citas] == [c.puntaje for c in rb.citas]


def test_t06_sin_jev_no_responde_con_un_titular_que_solo_comparte_una_palabra():
    """Caso real del recorte de la demo: sin Jev, la pregunta sobre ajedrez respondía con los
    resultados de la lotería porque ambos dicen "resultados del"."""
    docs = [
        *DOCS,
        {
            "id_noticia": "l1",
            "titulo": "Resultados del sorteo de la lotería del domingo",
            "medio": "TVN",
        },
    ]
    config = load_config().model_copy(update={"umbral": 0.05, "k_recuperados": 4})
    buscador = Buscador(docs, config, RepresentadorTfidf())
    r = buscador.responder("resultados del campeonato mundial de ajedrez en Noruega", None)
    assert r.abstencion and "palabras clave" in r.motivo
    # Con dos palabras clave hacen falta las dos: "precio de la gasolina" no es "El precio de
    # la historia".
    docs.append(
        {"id_noticia": "h1", "titulo": "El precio de la historia, en televisión", "medio": "TVN"}
    )
    buscador = Buscador(docs, config, RepresentadorTfidf())
    assert buscador.responder("precio de la gasolina", None).abstencion
    # Una pregunta que sí está en el corpus sigue respondiendo, aunque cambie el plural.
    r = buscador.responder("¿hubo tránsitos en el Canal?", None)
    assert not r.abstencion and r.citas[0].id_noticia == "n3"


def test_palabras_clave_y_cobertura():
    from faro_editorial.busqueda import cobertura, palabras_clave

    assert palabras_clave("¿Qué lluvias hubo en Chiriquí?") == ["lluvias", "chiriqui"]
    assert cobertura("lluvias en Chiriquí", "Lluvias, crecidas y alertas en Chiriquí") == 1
    assert cobertura("tránsito del canal", "Tránsitos por el Canal") == 1  # singular y plural
    assert cobertura("ajedrez en Noruega", "Resultados del sorteo de la lotería") == 0
    assert cobertura("¿qué?", "cualquier titular") == 0
