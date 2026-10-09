"""Evaluación de la agrupación, intervalos de Wilson y ahorro de tiempo (sección 9.1)."""

import csv
import json
from pathlib import Path

import pytest
from test_agrupacion import CONFIG, processed  # noqa: F401 (fixture)

from faro_editorial.agrupacion import RepresentadorTfidf, ejecutar_agrupacion
from faro_editorial.evaluacion_agrupacion import (
    COLUMNAS,
    NOMBRE_CSV,
    NOMBRE_PREDICCIONES,
    evaluar,
    pares_para_etiquetar,
)
from faro_editorial.metricas import (
    COLUMNAS_AHORRO,
    _con_intervalos,
    intervalo_wilson,
    metrica_agrupacion,
    metrica_ahorro_tiempo,
)

# --- Intervalo de Wilson ----------------------------------------------------------------


def test_wilson_valores_conocidos():
    assert intervalo_wilson(8, 10) == pytest.approx([0.4902, 0.9433], abs=1e-4)
    assert intervalo_wilson(30, 32) == pytest.approx([0.7985, 0.9827], abs=1e-4)


def test_wilson_no_colapsa_en_los_extremos_ni_se_sale_de_0_1():
    bajo, alto = intervalo_wilson(0, 5)
    assert bajo == 0.0 and 0.4 < alto < 0.5
    bajo, alto = intervalo_wilson(5, 5)
    assert 0.5 < bajo < 0.6 and alto == 1.0
    assert intervalo_wilson(0, 0) is None


def test_el_reporte_agrega_el_intervalo_a_cada_proporcion():
    reporte = _con_intervalos(
        {"a": {"numerador": 1, "denominador": 5, "x": [{"numerador": 2, "denominador": 4}]}}
    )
    assert reporte["a"]["ic95"] == intervalo_wilson(1, 5)
    assert reporte["a"]["x"][0]["ic95"] == intervalo_wilson(2, 4)


# --- Agrupación contra etiquetas humanas ------------------------------------------------


@pytest.fixture
def con_grupos(processed: Path) -> Path:  # noqa: F811
    """Snapshot agrupado; agrupacion.json dice e5, como en la ejecución real."""
    ejecutar_agrupacion(processed, CONFIG, RepresentadorTfidf())
    info = json.loads((processed / "agrupacion.json").read_text(encoding="utf-8"))
    info["metodo"] = "e5"
    (processed / "agrupacion.json").write_text(json.dumps(info), encoding="utf-8")
    return processed


def _etiquetar(carpeta: Path, mismo_evento: set[frozenset[str]]) -> None:
    predicciones = json.loads((carpeta / NOMBRE_PREDICCIONES).read_text(encoding="utf-8"))
    ruta = carpeta / NOMBRE_CSV
    with ruta.open(encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    for fila in filas:
        ids = frozenset(predicciones["pares"][fila["id_par"]]["ids"])
        fila["mismo_evento"] = "si" if ids in mismo_evento else "no"
        fila["etiquetador"] = "Prueba"
    with ruta.open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS)
        escritor.writeheader()
        escritor.writerows(filas)


def test_el_csv_es_ciego_y_las_predicciones_quedan_aparte(con_grupos: Path, tmp_path: Path):
    carpeta = tmp_path / "evaluacion"
    ruta, n = pares_para_etiquetar(con_grupos, carpeta, n=6)

    with ruta.open(encoding="utf-8", newline="") as f:
        lector = csv.DictReader(f)
        assert lector.fieldnames == COLUMNAS
        filas = list(lector)
    assert n == len(filas) >= 1
    assert all(not x["mismo_evento"] for x in filas)  # nada prellenado
    predicciones = json.loads((carpeta / NOMBRE_PREDICCIONES).read_text(encoding="utf-8"))
    assert predicciones["metodo_snapshot"] == "e5"
    estratos = {p["estrato"] for p in predicciones["pares"].values()}
    assert "agrupado_snapshot" in estratos
    with pytest.raises(FileExistsError):
        pares_para_etiquetar(con_grupos, carpeta, n=6)


def test_evaluar_cuenta_aciertos_y_errores_de_cada_metodo(con_grupos: Path, tmp_path: Path):
    carpeta = tmp_path / "evaluacion"
    pares_para_etiquetar(con_grupos, carpeta, n=6)
    mismo = {frozenset(p) for p in (("e1", "e2"), ("e1", "e3"), ("e2", "e3"))}
    _etiquetar(carpeta, mismo)

    r = evaluar(carpeta)
    assert r["etiquetadores"] == ["Prueba"] and not r["sin_etiquetar"]
    e5 = r["metodos"]["e5"]
    # El grupo del snapshot es justo el evento real: todo lo que agrupó es correcto.
    assert e5["fp"] == 0 and e5["precision"]["valor"] == 1.0
    assert e5["recall"]["numerador"] == r["mismo_evento"]
    assert e5["precision"]["ic95"] == intervalo_wilson(e5["tp"], e5["tp"] + e5["fp"])
    assert set(r["metodos"]) == {"e5", "tfidf"}

    m = metrica_agrupacion(carpeta / "evaluacion_agrupacion.json")
    assert m["estado"] == "medida" and m["metodos"]["e5"]["f1"] == e5["f1"]


def test_agrupacion_sin_etiquetas_queda_pendiente(tmp_path: Path):
    m = metrica_agrupacion(tmp_path / "no-existe.json")
    assert m["estado"] == "pendiente" and "evaluacion_agrupacion" in m["comando"]


# --- Ahorro de tiempo -----------------------------------------------------------------


def _ahorro(ruta: Path, filas: list[dict]) -> Path:
    with ruta.open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS_AHORRO)
        escritor.writeheader()
        escritor.writerows(filas)
    return ruta


def test_ahorro_compara_medianas_y_no_cuenta_las_incompletas(tmp_path: Path):
    ruta = _ahorro(
        tmp_path / "ahorro.csv",
        [
            {"tarea": "T1", "modo": "manual", "persona": "Ana", "minutos": "20", "completa": "si"},
            {"tarea": "T2", "modo": "manual", "persona": "Ana", "minutos": "30", "completa": "si"},
            {
                "tarea": "T1",
                "modo": "asistido",
                "persona": "Luis",
                "minutos": "5",
                "completa": "si",
            },
            {
                "tarea": "T2",
                "modo": "asistido",
                "persona": "Luis",
                "minutos": "2",
                "completa": "no",
            },
        ],
    )
    m = metrica_ahorro_tiempo(ruta)
    assert m["estado"] == "medida"
    assert m["mediana_manual_min"] == 25 and m["mediana_asistido_min"] == 5
    assert m["ahorro"] == 0.8 and m["incompletas"] == 1
    assert m["n_manual"] == 2 and m["n_asistido"] == 1


def test_ahorro_sin_las_dos_modalidades_queda_pendiente(tmp_path: Path):
    ruta = _ahorro(
        tmp_path / "ahorro.csv",
        [{"tarea": "T1", "modo": "manual", "persona": "Ana", "minutos": "20", "completa": "si"}],
    )
    assert metrica_ahorro_tiempo(ruta)["estado"] == "pendiente"
    assert metrica_ahorro_tiempo(tmp_path / "no-existe.csv")["estado"] == "pendiente"
