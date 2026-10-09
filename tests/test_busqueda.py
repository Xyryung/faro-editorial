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
