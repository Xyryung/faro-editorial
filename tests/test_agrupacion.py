"""Agrupación de noticias del mismo evento (issue #9). T02 · Tres registros del mismo evento:
agrupar sin perder fuentes. Titulares sintéticos; el método por defecto en pruebas es TF-IDF
(sin modelo ni internet). La prueba con e5 se marca `model` y se omite en CI."""

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from faro_editorial import agrupacion
from faro_editorial.agrupacion import (
    ConfigAgrupacion,
    RepresentadorTfidf,
    agrupar,
    crear_representador,
    ejecutar_agrupacion,
    id_grupo,
    load_config,
    main,
)
from faro_editorial.bandeja import generar_bandeja
from faro_editorial.carga import cargar_snapshot
from faro_editorial.contrato import Noticia

BASE = datetime(2026, 9, 20, 12, tzinfo=UTC)
CONFIG = ConfigAgrupacion(
    version="prueba",
    umbral={"e5": 0.9, "tfidf": 0.55},
    margen_promedio=0.05,
    ventana_horas=72,
    max_horas_grupo=168,
    max_tamano=40,
)

# Tres medios cuentan el mismo evento con redacciones distintas, más dos notas de otros temas.
MISMO_EVENTO = [
    (
        "e1",
        "TVN",
        "Asamblea aprueba en tercer debate la reforma a la ley de la Caja de Seguro Social",
    ),
    ("e2", "La Prensa", "Asamblea aprueba en tercer debate reforma a la ley de la CSS"),
    (
        "e3",
        "Telemetro",
        "Aprueban en tercer debate la reforma a la ley de la Caja de Seguro Social",
    ),
]
OTROS = [
    ("o1", "TVN", "Canal de Panamá anuncia nuevas tarifas de tránsito para 2027"),
    ("o2", "Crítica", "Fuertes lluvias provocan inundaciones en Chiriquí"),
]


def noticia(id_, medio, titulo, horas=0.0, sin_fecha=False):
    return Noticia(
        id_noticia=id_,
        titulo=titulo,
        url=f"https://{medio.lower().replace(' ', '')}.example/{id_}",
        medio=medio,
        fecha_publicacion=None if sin_fecha else BASE + timedelta(hours=horas),
        fecha_extraccion=datetime(2026, 10, 7, tzinfo=UTC),
        origen="prueba",
    )


def con_tfidf(noticias, config=CONFIG):
    vectores = RepresentadorTfidf().vectores([n.titulo for n in noticias])
    return agrupar(noticias, vectores, config.umbral["tfidf"], config)


def ids(noticias, grupos):
    return sorted(sorted(noticias[i].id_noticia for i in g) for g in grupos)


# --- Agrupación con TF-IDF ------------------------------------------------------------


def test_t02_tres_registros_del_mismo_evento_quedan_en_un_grupo():
    noticias = [noticia(i, m, t, horas=k * 3) for k, (i, m, t) in enumerate(MISMO_EVENTO + OTROS)]
    grupos, stats = con_tfidf(noticias)

    assert ids(noticias, grupos) == [["e1", "e2", "e3"], ["o1"], ["o2"]]
    # No se pierde ninguna fuente: cada noticia está en exactamente un grupo.
    assert sorted(i for g in grupos for i in g) == list(range(len(noticias)))
    assert stats["uniones"] == 2


def test_no_une_temas_distintos_que_comparten_palabras():
    noticias = [
        noticia("a", "TVN", "Mulino veta proyecto de ley sobre beneficios a jubilados"),
        noticia("b", "La Prensa", "Mulino inaugura hospital en la provincia de Colón", horas=2),
    ]
    grupos, _ = con_tfidf(noticias)
    assert ids(noticias, grupos) == [["a"], ["b"]]


def test_mismo_titular_fuera_de_la_ventana_no_se_une():
    # Un titular recurrente diez días después es otro evento.
    t = "Ministerio de Salud reporta nuevos casos de dengue en el país"
    noticias = [noticia("a", "TVN", t), noticia("b", "TVN", t, horas=240)]
    grupos, _ = con_tfidf(noticias)
    assert ids(noticias, grupos) == [["a"], ["b"]]


def test_noticia_sin_fecha_se_compara_igual():
    noticias = [noticia(i, m, t) for i, m, t in MISMO_EVENTO[:2]]
    noticias.append(noticia(*MISMO_EVENTO[2], sin_fecha=True))
    grupos, _ = con_tfidf(noticias)
    assert ids(noticias, grupos) == [["e1", "e2", "e3"]]


def test_lista_vacia_y_una_sola_noticia():
    assert agrupar([], np.zeros((0, 3)), 0.9, CONFIG) == ([], {"pares_candidatos": 0})
    (g,), _ = agrupar([noticia("a", "TVN", "x")], np.array([[1.0, 0, 0]]), 0.9, CONFIG)
    assert g == [0]


# --- Reglas contra grupos falsos (vectores fijos: resultado exacto) -------------------


def _unitarios(filas):
    m = np.array(filas, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def test_no_encadena_temas_distintos():
    # A~B (0.71) y B~C (0.71), pero A y C no tienen nada en común (0.0).
    noticias = [noticia(x, "M", x, horas=k) for k, x in enumerate("abc")]
    vectores = _unitarios([[1, 0, 0], [1, 1, 0], [0, 1, 0]])
    grupos, stats = agrupar(noticias, vectores, 0.7, CONFIG)
    assert sorted(len(g) for g in grupos) == [1, 2]
    assert stats["rechazados_por_promedio"] == 1


def test_tamano_maximo():
    noticias = [noticia(f"n{k}", "M", "t", horas=k) for k in range(5)]
    config = CONFIG.model_copy(update={"max_tamano": 3})
    grupos, stats = agrupar(noticias, _unitarios([[1, 0, 0]] * 5), 0.9, config)
    assert sorted(len(g) for g in grupos) == [2, 3]
    assert stats["rechazados_por_tamano"] >= 1


def test_duracion_maxima_del_grupo():
    # 0 h, 60 h y 120 h: cada par vecino está en la ventana de 72 h, pero el grupo
    # completo abarcaría 120 h y el máximo es 100 h.
    noticias = [noticia(f"n{h}", "M", "t", horas=h) for h in (0, 60, 120)]
    config = CONFIG.model_copy(update={"max_horas_grupo": 100})
    grupos, stats = agrupar(noticias, _unitarios([[1, 0, 0]] * 3), 0.9, config)
    assert sorted(len(g) for g in grupos) == [1, 2]
    assert stats["rechazados_por_duracion"] == 1


def test_resultado_determinista_y_ids_estables():
    noticias = [noticia(i, m, t, horas=k) for k, (i, m, t) in enumerate(MISMO_EVENTO + OTROS)]
    assert con_tfidf(noticias) == con_tfidf(noticias)
    assert id_grupo(["e2", "e1", "e3"]) == id_grupo(["e3", "e2", "e1"])
    assert id_grupo(["e1", "e2"]) != id_grupo(["e1", "e3"])
    assert id_grupo(["e1"]).startswith("g-") and len(id_grupo(["e1"])) == 14


# --- De la base cargada a la bandeja ---------------------------------------------------


@pytest.fixture
def processed(tmp_path: Path, escribir_manifest) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    columnas = [
        "id_noticia",
        "titulo",
        "url",
        "medio",
        "idioma",
        "fecha_publicacion",
        "fecha_deteccion",
        "fecha_extraccion",
        "tema",
        "origen",
        "alcance_texto",
    ]
    with (raw / "noticias.csv").open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=columnas)
        escritor.writeheader()
        for k, (i, medio, titulo) in enumerate(MISMO_EVENTO + OTROS):
            escritor.writerow(
                {
                    "id_noticia": i,
                    "titulo": titulo,
                    "url": f"https://m{k}.example/{i}",
                    "medio": medio,
                    "idioma": "es",
                    "fecha_publicacion": (BASE + timedelta(hours=k * 3)).isoformat(),
                    "fecha_deteccion": "",
                    "fecha_extraccion": "2026-10-07T00:00:00Z",
                    "tema": "",
                    "origen": "prueba",
                    "alcance_texto": "titular_metadatos",
                }
            )
    escribir_manifest(raw)
    destino = tmp_path / "processed"
    cargar_snapshot(raw, destino)
    return destino


def test_t02_la_bandeja_recibe_un_solo_tema_con_las_tres_fuentes(processed: Path):
    resumen = ejecutar_agrupacion(processed, CONFIG, RepresentadorTfidf())

    lineas = (processed / "grupos.jsonl").read_text(encoding="utf-8").splitlines()
    (grupo,) = [json.loads(x) for x in lineas]  # solo grupos de 2 o más notas
    assert sorted(grupo["ids_noticias"]) == ["e1", "e2", "e3"]
    assert grupo["id_grupo"] == id_grupo(["e1", "e2", "e3"])
    assert resumen["grupos_con_2_o_mas"] == 1 and resumen["grupos_con_varios_medios"] == 1
    assert resumen["metodo"] == "tfidf" and resumen["umbral"] == 0.55

    bandeja = generar_bandeja(processed)
    assert bandeja["agrupacion"].startswith("grupos.jsonl")
    assert bandeja["resumen"]["grupos"] == 3  # 5 noticias -> 1 grupo de 3 + 2 sueltas
    tema = next(t for t in bandeja["temas"] if t["id_grupo"] == grupo["id_grupo"])
    assert sorted(n["id_noticia"] for n in tema["noticias"]) == ["e1", "e2", "e3"]
    assert len(tema["procedencias"]) == 3  # TVN, La Prensa y Telemetro conservados


def test_resumen_para_revisar_los_grupos(processed: Path):
    ejecutar_agrupacion(processed, CONFIG, RepresentadorTfidf())
    resumen = json.loads((processed / "agrupacion.json").read_text(encoding="utf-8"))
    (mayor,) = resumen["mayores"]
    assert mayor["noticias"] == 3 and mayor["medios"] == ["La Prensa", "TVN", "Telemetro"]
    assert resumen["tamanos"] == {"3": 1}
    assert resumen["noticias"] == 5 and resumen["noticias_agrupadas"] == 3


def test_comando(processed: Path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(processed.parent))
    from faro_editorial.settings import get_settings

    get_settings.cache_clear()
    try:
        main(["--metodo", "tfidf", "--muestra", "1"])
    finally:
        get_settings.cache_clear()
    salida = capsys.readouterr().out
    assert "Método: tfidf" in salida
    assert "5 noticias → 3 grupos; 1 con 2 o más notas" in salida
    assert "Asamblea aprueba" in salida
    assert (processed / "grupos.jsonl").exists()


# --- Elección del método --------------------------------------------------------------


def test_auto_cae_a_tfidf_si_e5_no_esta_disponible(monkeypatch):
    def sin_modelo(modelo):
        raise OSError("modelo no descargado y sin internet")

    monkeypatch.setattr(agrupacion, "RepresentadorE5", sin_modelo)
    representador, aviso = crear_representador("auto", "intfloat/multilingual-e5-small")
    assert representador.metodo == "tfidf"
    assert "OSError" in aviso and "TF-IDF" in aviso
    with pytest.raises(OSError):
        crear_representador("e5", "intfloat/multilingual-e5-small")


def test_config_del_repo():
    config = load_config()
    assert config.metodo == "auto"
    assert 0 < config.umbral["tfidf"] < config.umbral["e5"] < 1
    assert config.ventana_horas <= config.max_horas_grupo


@pytest.mark.model
def test_e5_agrupa_el_mismo_evento():
    """Requiere el modelo descargado: uv run pytest -m model tests/test_agrupacion.py"""
    from faro_editorial.agrupacion import RepresentadorE5
    from faro_editorial.settings import get_settings

    noticias = [noticia(i, m, t, horas=k * 3) for k, (i, m, t) in enumerate(MISMO_EVENTO + OTROS)]
    vectores = RepresentadorE5(get_settings().embedding_model).vectores(
        [n.titulo for n in noticias]
    )
    grupos, _ = agrupar(noticias, vectores, load_config().umbral["e5"], CONFIG)
    assert ["e1", "e2", "e3"] in ids(noticias, grupos)
