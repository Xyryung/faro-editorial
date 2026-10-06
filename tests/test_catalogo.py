"""Catálogo de datos para Notion (issue #4): una fila por fuente con los campos de la sección 5."""

import csv
from pathlib import Path

import pytest

from faro_editorial.carga import cargar_snapshot
from faro_editorial.catalogo import COLUMNAS, escribir_catalogo, generar_catalogo


@pytest.fixture
def resultado(raw: Path, tmp_path: Path):
    return cargar_snapshot(raw, tmp_path / "processed")


@pytest.fixture
def filas(resultado):
    return {f["fuente"]: f for f in generar_catalogo(resultado)}


def test_una_fila_por_fuente_documentada(filas):
    assert sorted(filas) == [
        "Banco Mundial · Indicators API v2",
        "GDELT · DOC 2.0 API",
        "TVN · feed RSS público",
        "USGS · catálogo sísmico (FDSN Event)",
    ]
    for fila in filas.values():
        assert set(fila) == set(COLUMNAS)
        assert fila["licencia"] and fila["url"].startswith("https://")


def test_noticias_se_separan_por_origen(filas):
    tvn, gdelt = filas["TVN · feed RSS público"], filas["GDELT · DOC 2.0 API"]
    assert (tvn["registros"], gdelt["registros"]) == (2, 1)
    assert tvn["cobertura"].startswith("2025-09-15 a 2025-09-20")
    # Los 5 rechazos de noticias son de TVN; GDELT no tiene excluidos.
    assert tvn["excluidos"].startswith("5: ")
    assert "id duplicado: n001 (1)" in tvn["excluidos"]
    assert gdelt["excluidos"] == "0"


def test_cobertura_y_hash(filas, resultado):
    bm = filas["Banco Mundial · Indicators API v2"]
    assert bm["cobertura"] == (
        "Países: PAN; 2 indicador(es); años 2023–2024; 2 de 3 combinaciones con valor"
    )
    assert bm["fecha_extraccion"] == "2025-10-01 00:00"
    esperado = resultado.integridad["archivos"]["indicadores.csv"]["sha256_calculado"]
    assert bm["hash"] == esperado

    usgs = filas["USGS · catálogo sísmico (FDSN Event)"]
    assert usgs["cobertura"] == "2024-01-01 a 2024-02-01; 2 sismos; magnitud 3.4–4.6"
    # eventos.geojson no trae fecha de extracción por fila: se usa el corte del manifest.
    assert usgs["fecha_extraccion"] == "2025-10-01 00:00 (corte del manifest)"


def test_origen_no_documentado_aparece_igual(raw: Path, tmp_path: Path):
    with (raw / "noticias.csv").open("a", encoding="utf-8") as f:
        f.write("n050,Otra fuente,https://otra.org/x,Otra,es,2025-09-18,,2025-10-01,,scraper,\n")
    filas = generar_catalogo(cargar_snapshot(raw, tmp_path / "processed2"))
    otra = next(f for f in filas if f["fuente"] == "Noticias · origen scraper")
    assert otra["registros"] == 1
    assert otra["licencia"] == "por documentar"


def test_sin_manifest_el_hash_lo_indica(raw: Path, tmp_path: Path):
    (raw / "manifest.json").unlink()
    filas = generar_catalogo(cargar_snapshot(raw, tmp_path / "processed"))
    assert all(f["hash"] == "sin dato (manifest ausente)" for f in filas)


def test_csv_importable_en_notion(resultado, tmp_path: Path):
    rutas = escribir_catalogo(generar_catalogo(resultado), tmp_path / "salida")
    with rutas["csv"].open(encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f))
    assert len(filas) == 4
    assert list(filas[0]) == list(COLUMNAS.values())
    assert rutas["md"].read_text(encoding="utf-8").startswith("# Catálogo de datos")
