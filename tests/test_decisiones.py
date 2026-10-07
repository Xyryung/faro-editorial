"""Capa de decisiones y caché (issue #8). T10 · Sin internet durante la demo: con
OFFLINE=1 solo se lee la caché y, si falta una respuesta, hay abstención explícita."""

import json
from pathlib import Path

import pytest

from faro_editorial.decisiones import (
    CacheDecisiones,
    ClienteDecisiones,
    PreguntaChoice,
    PreguntaNoul,
    PreguntaScore,
    RespuestaChoice,
    RespuestaNoul,
    RespuestaScore,
    clave_cache,
)
from faro_editorial.proveedores import ProveedorSimulado

ESTADO = "Tránsito por el Canal de Panamá se mantiene estable"
VERSION = "prueba-v1"

PREGUNTAS = {
    "tema": PreguntaChoice(
        instrucciones="¿De qué tema trata el titular?",
        opciones={"logistica_canal": "Canal, puertos, transporte", "otro": "Otro tema"},
    ),
    "panama": PreguntaNoul(instrucciones="El titular trata sobre Panamá."),
    "impacto": PreguntaScore(
        instrucciones="Impacto potencial para el público panameño",
        niveles=["Bajo", "Medio", "Alto"],
    ),
}

RESPUESTAS_OK = {
    "tema": RespuestaChoice(
        opcion="logistica_canal",
        probabilidades={"logistica_canal": 0.9, "otro": 0.1},
        confianza=0.8,
    ),
    "panama": RespuestaNoul(probabilidad=0.97),
    "impacto": RespuestaScore(valor=1.2, niveles=3),
}


class Reloj:
    """Reloj falso: cada lectura avanza 0.25 s, así la latencia es determinista."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        self.t += 0.25
        return self.t


def cliente(tmp_path: Path, proveedor: ProveedorSimulado, offline: bool = False):
    return ClienteDecisiones(proveedor, tmp_path / "cache", offline=offline, reloj=Reloj())


def registro(tmp_path: Path) -> list[dict]:
    ruta = tmp_path / "cache" / "registro_llamadas.jsonl"
    if not ruta.exists():
        return []
    return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines()]


def test_primera_llamada_en_vivo_y_la_segunda_desde_cache(tmp_path):
    p = ProveedorSimulado(RESPUESTAS_OK, costo_usd=0.00002)
    c = cliente(tmp_path, p)

    primera = c.decidir(ESTADO, PREGUNTAS, VERSION)
    segunda = c.decidir(ESTADO, PREGUNTAS, VERSION)

    assert p.llamadas == 1
    assert not primera.abstencion and not primera.desde_cache
    assert segunda.desde_cache
    assert segunda.respuestas == primera.respuestas
    assert segunda.respuesta("tema").opcion == "logistica_canal"


def test_la_clave_cambia_con_modelo_version_estado_y_texto_de_pregunta():
    base = clave_cache("jev", "typesafe/jev-1.13", VERSION, ESTADO, PREGUNTAS)
    otra_pregunta = dict(PREGUNTAS)
    otra_pregunta["panama"] = PreguntaNoul(instrucciones="El titular menciona Panamá.")

    assert base != clave_cache("jev", "otro-modelo", VERSION, ESTADO, PREGUNTAS)
    assert base != clave_cache("jev", "typesafe/jev-1.13", "prueba-v2", ESTADO, PREGUNTAS)
    assert base != clave_cache("jev", "typesafe/jev-1.13", VERSION, ESTADO + ".", PREGUNTAS)
    assert base != clave_cache("jev", "typesafe/jev-1.13", VERSION, ESTADO, otra_pregunta)
    # Mismo contenido, mismo orden lógico: misma clave (reproducible entre ejecuciones).
    assert base == clave_cache("jev", "typesafe/jev-1.13", VERSION, ESTADO, dict(PREGUNTAS))


def test_t10_offline_sin_cache_se_abstiene_sin_llamar(tmp_path):
    p = ProveedorSimulado(RESPUESTAS_OK)
    decision = cliente(tmp_path, p, offline=True).decidir(ESTADO, PREGUNTAS, VERSION)

    assert p.llamadas == 0
    assert decision.abstencion is True
    assert decision.respuestas == {}
    assert "offline" in decision.motivo.lower()


def test_t10_offline_responde_lo_que_se_guardo_en_linea(tmp_path):
    en_linea = cliente(tmp_path, ProveedorSimulado(RESPUESTAS_OK)).decidir(
        ESTADO, PREGUNTAS, VERSION
    )
    sin_red = ProveedorSimulado(error=ConnectionError("sin internet"))
    offline = cliente(tmp_path, sin_red, offline=True).decidir(ESTADO, PREGUNTAS, VERSION)

    assert sin_red.llamadas == 0
    assert offline.desde_cache and not offline.abstencion
    assert offline.respuestas == en_linea.respuestas


def test_error_del_proveedor_es_abstencion_y_no_se_guarda(tmp_path):
    p = ProveedorSimulado(error=TimeoutError("se agotó el tiempo"))
    c = cliente(tmp_path, p)

    decision = c.decidir(ESTADO, PREGUNTAS, VERSION)
    c.decidir(ESTADO, PREGUNTAS, VERSION)

    assert decision.abstencion and "TimeoutError" in decision.motivo
    assert p.llamadas == 2  # no quedó nada en caché: se vuelve a intentar
    filas = registro(tmp_path)
    assert [f["ok"] for f in filas] == [False, False]
    assert "se agotó el tiempo" in filas[0]["error"]


@pytest.mark.parametrize(
    ("cambio", "fragmento"),
    [
        ({"tema": RespuestaChoice(opcion="deportes")}, "opción desconocida"),
        ({"impacto": RespuestaScore(valor=2.5, niveles=3)}, "fuera de la escala"),
        ({"panama": RespuestaNoul(probabilidad=1.4)}, "fuera de 0-1"),
        ({"tema": RespuestaNoul(probabilidad=0.5)}, "se esperaba choice"),
        ({"extra": RespuestaNoul(probabilidad=0.5)}, "no pedidas"),
    ],
)
def test_respuesta_invalida_es_abstencion_y_no_se_guarda(tmp_path, cambio, fragmento):
    p = ProveedorSimulado({**RESPUESTAS_OK, **cambio})
    c = cliente(tmp_path, p)

    decision = c.decidir(ESTADO, PREGUNTAS, VERSION)

    assert decision.abstencion and fragmento in decision.motivo
    assert not c.cache.ruta(decision.clave).exists()


def test_falta_una_respuesta_es_abstencion(tmp_path):
    incompletas = {k: v for k, v in RESPUESTAS_OK.items() if k != "impacto"}
    decision = cliente(tmp_path, ProveedorSimulado(incompletas)).decidir(ESTADO, PREGUNTAS, VERSION)
    assert decision.abstencion and "impacto: sin respuesta" in decision.motivo


def test_registro_con_modelo_version_costo_y_latencia(tmp_path):
    c = cliente(tmp_path, ProveedorSimulado(RESPUESTAS_OK, costo_usd=0.00002))
    decision = c.decidir(ESTADO, PREGUNTAS, VERSION)

    (fila,) = registro(tmp_path)
    assert fila["ok"] is True
    assert fila["proveedor"] == "simulado"
    assert fila["modelo"] == "simulado-1"
    assert fila["modelo_servido"] == "simulado-1-fijo"
    assert fila["version_prompt"] == VERSION
    assert fila["costo_usd"] == 0.00002
    assert fila["latencia_s"] == 0.25
    assert fila["clave"] == decision.clave
    assert decision.latencia_s == 0.25


def test_las_lecturas_de_cache_no_se_registran_como_llamadas(tmp_path):
    c = cliente(tmp_path, ProveedorSimulado(RESPUESTAS_OK))
    for _ in range(3):
        c.decidir(ESTADO, PREGUNTAS, VERSION)
    assert len(registro(tmp_path)) == 1


def test_cache_danada_se_trata_como_ausente(tmp_path):
    p = ProveedorSimulado(RESPUESTAS_OK)
    c = cliente(tmp_path, p)
    decision = c.decidir(ESTADO, PREGUNTAS, VERSION)
    c.cache.ruta(decision.clave).write_text("{no es json", encoding="utf-8")

    otra = c.decidir(ESTADO, PREGUNTAS, VERSION)

    assert p.llamadas == 2 and not otra.abstencion and not otra.desde_cache


def test_la_cache_guarda_la_solicitud_para_trazabilidad(tmp_path):
    c = cliente(tmp_path, ProveedorSimulado(RESPUESTAS_OK))
    decision = c.decidir(ESTADO, PREGUNTAS, VERSION)

    guardado = json.loads(c.cache.ruta(decision.clave).read_text(encoding="utf-8"))
    assert guardado["solicitud"]["estado"] == ESTADO
    assert set(guardado["solicitud"]["preguntas"]) == set(PREGUNTAS)
    assert CacheDecisiones(tmp_path / "cache" / "decisiones").leer(decision.clave) is not None


def test_acepta_preguntas_como_diccionarios(tmp_path):
    preguntas = {"panama": {"tipo": "noul", "instrucciones": "El titular trata sobre Panamá."}}
    p = ProveedorSimulado({"panama": RespuestaNoul(probabilidad=0.9)})
    decision = cliente(tmp_path, p).decidir(ESTADO, preguntas, VERSION)
    assert decision.respuesta("panama").probabilidad == 0.9


def test_preguntas_mal_formadas_fallan_al_construirlas():
    with pytest.raises(ValueError, match="al menos dos opciones"):
        PreguntaChoice(instrucciones="x", opciones={"solo": "una"})
    with pytest.raises(ValueError, match="entre 2 y 10"):
        PreguntaScore(instrucciones="x", niveles=["uno"])


def test_score_normalizado_para_el_motor_de_puntaje():
    r = RespuestaScore(valor=1.99, niveles=3)
    assert r.nivel == 2
    assert r.normalizado == pytest.approx(0.995)
