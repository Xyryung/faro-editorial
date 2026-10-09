"""Métricas de la sección 9.1 (issue #19): numerador, denominador y fallos; nunca un número
inventado cuando falta la entrada. Sin red: borradores con el LLM simulado de test_borradores."""

import copy
import csv
import json
from pathlib import Path

import pytest
from test_borradores import CONFIG, SALIDA_VALIDA, TEMA, FalsoLLM, _generador, _jev, _salida

from faro_editorial.agrupacion import RepresentadorTfidf
from faro_editorial.borradores import generar_ficha, guardar_borradores
from faro_editorial.busqueda import Buscador
from faro_editorial.busqueda import load_config as load_config_busqueda
from faro_editorial.metricas import (
    COLUMNAS_PARES,
    NOMBRE_PARES,
    NOMBRE_RESULTADOS_CONSULTAS,
    NOMBRE_SELECCION,
    ConjuntoConsultas,
    _percentil,
    ejecutar_consultas,
    generar_reporte,
    leer_pares,
    load_consultas,
    metrica_abstencion,
    metrica_cobertura,
    metrica_precision5,
    metrica_sustento,
    reporte_markdown,
    seleccion_para_editor,
)


def _borradores(tmp_path: Path, *salidas: dict) -> dict:
    jev, _ = _jev(tmp_path, {SALIDA_VALIDA["afirmaciones"][1]["texto"]: 0.1})
    fichas = {}
    for i, salida in enumerate(salidas):
        tema = {**copy.deepcopy(TEMA), "id_grupo": f"G{i}"}
        llm = FalsoLLM(salida, salida)
        fichas[tema["id_grupo"]] = generar_ficha(
            tema, _generador(tmp_path / f"c{i}", llm), jev, CONFIG
        )
    return fichas


# --- Cobertura ------------------------------------------------------------------------


def test_cobertura_cuenta_las_rechazadas_como_fallo(tmp_path):
    con_error = copy.deepcopy(SALIDA_VALIDA)
    con_error["afirmaciones"].append(
        {
            "id": "a5",
            "tipo": "hecho",
            "texto": "El Gobierno confirmó la cifra.",
            "citas": [{"id_evidencia": "N999", "campo": "titulo"}],
        }
    )
    m = metrica_cobertura(_borradores(tmp_path, SALIDA_VALIDA, con_error))
    # 3 factuales por borrador (la hipótesis no cuenta) + 1 rechazada en el segundo.
    assert (m["numerador"], m["denominador"]) == (6, 7)
    assert m["rechazadas"] == 1 and not m["cumple"]
    assert any("G1:a5" in f and "rechazada" in f for f in m["fallos"])


def test_cobertura_sin_borradores_queda_pendiente():
    m = metrica_cobertura({})
    assert m["estado"] == "pendiente" and "valor" not in m and "borradores" in m["comando"]


# --- Validez de sustento --------------------------------------------------------------


def _escribir_pares(ruta: Path, filas: list[dict]) -> None:
    with ruta.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_PARES)
        w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in COLUMNAS_PARES})


def test_sustento_humano_y_acuerdo_con_jev(tmp_path):
    borradores = _borradores(tmp_path, SALIDA_VALIDA)  # Jev: a1 sí, a2 no (0.1), a3 sí
    ruta = tmp_path / NOMBRE_PARES
    _escribir_pares(
        ruta,
        [
            {"id_caso": "G0", "id_afirmacion": "a1", "sustento_humano": "respaldada"},
            {"id_caso": "G0", "id_afirmacion": "a2", "sustento_humano": "Respaldada "},
            {"id_caso": "G0", "id_afirmacion": "a3", "sustento_humano": "no_respaldada"},
            {"id_caso": "G0", "id_afirmacion": "a4", "sustento_humano": ""},
            {"id_caso": "G0", "id_afirmacion": "a9", "sustento_humano": "quizás"},
        ],
    )
    m = metrica_sustento(ruta, borradores)
    assert (m["numerador"], m["denominador"]) == (2, 3)
    assert m["no_respaldadas"] == ["G0:a3"]
    assert m["sin_etiquetar"] == 2 and not m["suficientes_pares"] and not m["cumple"]
    a = m["acuerdo_jev"]
    assert (a["numerador"], a["denominador"]) == (1, 3)
    assert any("G0:a2" in d for d in a["desacuerdos"])


def test_sustento_sin_archivo_queda_pendiente(tmp_path):
    m = metrica_sustento(tmp_path / NOMBRE_PARES, {})
    assert m["estado"] == "pendiente" and "pares" in m["comando"]


def test_pares_para_etiquetar_a_ciegas_y_sin_pisar_etiquetas(tmp_path, monkeypatch):
    import faro_editorial.interfaz as interfaz
    from faro_editorial.metricas import pares_para_etiquetar

    processed = tmp_path / "processed"
    processed.mkdir()
    guardar_borradores(list(_borradores(tmp_path, SALIDA_VALIDA).values()), processed)
    temas = [{**copy.deepcopy(TEMA), "id_grupo": "G0"}]
    monkeypatch.setattr(interfaz, "cargar_bandeja", lambda _: ({"temas": temas}, None))

    ruta, n = pares_para_etiquetar(processed, tmp_path / "ev" / NOMBRE_PARES)
    assert n == 3  # a1, a2, a3; la hipótesis a4 no se etiqueta
    filas, avisos = leer_pares(ruta)
    assert filas == [] and len(avisos) == 3  # todavía sin etiquetar
    with ruta.open(encoding="utf-8-sig") as f:
        contenido = f.read()
    assert "7.3" in contenido and "BM:PAN" in contenido  # el texto de la evidencia citada
    assert "respaldada (0." not in contenido and "verificacion" not in contenido  # sin Jev
    with pytest.raises(FileExistsError):
        pares_para_etiquetar(processed, ruta)


# --- Abstención -----------------------------------------------------------------------

DOCS = [
    {"id_noticia": "n1", "titulo": "Lluvias, crecidas y alertas en Chiriquí", "medio": "TVN"},
    {
        "id_noticia": "n2",
        "titulo": "Canal de Panamá aumenta a 33 los tránsitos diarios",
        "medio": "TVN",
    },
    {"id_noticia": "n3", "titulo": "Exportaciones caen más de $151 millones", "medio": "TVN"},
]
CONJUNTO = ConjuntoConsultas.model_validate(
    {
        "version": "consultas-prueba",
        "consultas": [
            {
                "id": "R1",
                "pregunta": "lluvias y crecidas en Chiriquí",
                "etiqueta": "respondible",
                "evidencia_esperada": ["n1"],
            },
            {
                "id": "R2",
                "pregunta": "tránsitos diarios del Canal",
                "etiqueta": "respondible",
                "evidencia_esperada": ["n3"],
            },  # recupera n2: respondida, pero sin la esperada
            {
                "id": "N1",
                "pregunta": "resultado del partido de fútbol de ayer",
                "etiqueta": "no_respondible",
            },
            {
                "id": "N2",
                "pregunta": "precio del boleto del metro de Tokio",
                "etiqueta": "no_respondible",
                "dificil": True,
            },
        ],
    }
)


def test_abstencion_con_numerador_denominador_y_abstenciones_incorrectas(tmp_path):
    config = load_config_busqueda().model_copy(update={"umbral": 0.05, "k_recuperados": 3})
    buscador = Buscador(DOCS, config, RepresentadorTfidf())
    tiempos = iter(range(100))
    datos = ejecutar_consultas(buscador, None, CONJUNTO, reloj=lambda: next(tiempos))
    ruta = tmp_path / NOMBRE_RESULTADOS_CONSULTAS
    ruta.write_text(json.dumps(datos), encoding="utf-8")

    m = metrica_abstencion(ruta)
    assert (m["numerador"], m["denominador"]) == (2, 2) and m["cumple"]
    assert m["dificiles_rechazadas"] == {"numerador": 1, "denominador": 1}
    assert m["abstenciones_incorrectas"]["numerador"] == 0
    rc = m["respondidas_con_evidencia_esperada"]
    assert (rc["numerador"], rc["denominador"]) == (1, 2) and rc["fallos"] == ["R2"]
    assert m["con_jev"] is False
    assert all(r["latencia_s"] == 1 for r in datos["resultados"])


def test_el_conjunto_real_esta_bien_formado():
    conjunto = load_consultas()
    etiquetas = [c.etiqueta for c in conjunto.consultas]
    assert etiquetas.count("respondible") >= 10 and etiquetas.count("no_respondible") >= 10
    assert all(c.evidencia_esperada for c in conjunto.consultas if c.etiqueta == "respondible")


def test_una_respondible_sin_evidencia_esperada_es_invalida():
    with pytest.raises(ValueError, match="evidencia_esperada"):
        ConjuntoConsultas.model_validate(
            {"version": "x", "consultas": [{"id": "R", "pregunta": "?", "etiqueta": "respondible"}]}
        )


# --- Precision@5 ----------------------------------------------------------------------


def test_precision5_a_ciegas(tmp_path):
    bandeja = {
        "temas": [
            {"id_grupo": f"T{i}", "titulo": f"Tema {i}", "puntaje": 100 - i, "procedencias": []}
            for i in range(1, 16)
        ]
    }
    ruta = seleccion_para_editor(bandeja, tmp_path / NOMBRE_SELECCION)
    with ruta.open(encoding="utf-8-sig") as f:
        filas = list(csv.DictReader(f))
    assert len(filas) == 15 and "puntaje" not in filas[0]
    assert [r["id_grupo"] for r in filas] != [f"T{i}" for i in range(1, 16)]  # barajado
    assert metrica_precision5(bandeja, ruta)["estado"] == "pendiente"  # sin elegir aún

    elegidos = {"T1", "T2", "T3", "T9", "T12"}
    for r in filas:
        r["elegido"] = "x" if r["id_grupo"] in elegidos else ""
        r["evaluador"] = "Rafael"
    with ruta.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
    m = metrica_precision5(bandeja, ruta)
    assert (m["numerador"], m["denominador"]) == (3, 5) and m["exploratoria"]
    assert m["solo_sistema"] == ["T4", "T5"] and sorted(m["solo_persona"]) == ["T12", "T9"]
    with pytest.raises(FileExistsError):
        seleccion_para_editor(bandeja, ruta)


# --- Reporte --------------------------------------------------------------------------


def test_reporte_sin_entradas_no_inventa_numeros(tmp_path):
    r = generar_reporte(tmp_path)
    for clave in ("cobertura_citas", "validez_sustento", "abstencion", "clasificacion"):
        assert r[clave]["estado"] == "pendiente"
    md = reporte_markdown(r)
    filas = [linea.split("|") for linea in md.splitlines() if linea.startswith("| ")][1:]
    resultados = [f[2].strip() for f in filas]
    assert resultados and all(r == "pendiente" for r in resultados), resultados


def test_reporte_con_borradores_muestra_fallos_e_ids_sin_titulares(tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    rara = _salida(brief=SALIDA_VALIDA["brief"])
    guardar_borradores(list(_borradores(tmp_path, rara).values()), processed)
    r = generar_reporte(tmp_path)
    assert r["cobertura_citas"]["estado"] == "medida"
    assert r["eficiencia"]["borradores"]["n"] == 1
    md = reporte_markdown(r)
    assert "3/3 (100.0 %)" in md
    assert TEMA["noticias"][0]["titulo"] not in md  # el reporte versionado no lleva titulares


def test_percentil():
    assert _percentil([], 95) is None
    assert _percentil([4.0], 95) == 4.0
    assert _percentil([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 9.55
