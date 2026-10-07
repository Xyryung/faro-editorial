"""Paquete de datos para la entrega (issue #23): contenido completo, sin la descripción del RSS
de TVN (decisión #35) y con hashes que coinciden con lo empaquetado."""

import csv
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from faro_editorial.paquete import construir_paquete

DESCRIPCION = "Texto del RSS que no se redistribuye"


@pytest.fixture
def raw_con_descripcion(raw: Path, escribir_manifest) -> Path:
    with (raw / "noticias.csv").open(encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    columnas = [*filas[0].keys(), "descripcion"]
    with (raw / "noticias.csv").open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=columnas)
        escritor.writeheader()
        for fila in filas:
            escritor.writerow({**fila, "descripcion": DESCRIPCION})
    escribir_manifest(raw)
    return raw


@pytest.fixture
def paquete(raw_con_descripcion: Path, tmp_path: Path) -> ZipFile:
    benchmark = tmp_path / "benchmark"
    benchmark.mkdir()
    (benchmark / "benchmark_dev.jsonl").write_text('{"id": "q01"}\n', encoding="utf-8")
    ruta = construir_paquete(
        raw_con_descripcion, tmp_path / "processed", tmp_path / "dist", benchmark_dir=benchmark
    )
    with ZipFile(ruta) as z:
        yield z


def test_contiene_lo_que_pide_el_reto(paquete: ZipFile):
    nombres = set(paquete.namelist())
    assert {
        "raw/noticias.csv",
        "raw/indicadores.csv",
        "raw/eventos.geojson",
        "raw/fuentes.json",
        "CONTRATO.md",
        "licencias_y_condiciones.yaml",
        "processed/catalogo_datos.csv",
        "processed/reporte_calidad.json",
        "processed/rechazos.jsonl",
        "benchmark/benchmark_dev.jsonl",
        "LEEME.md",
        "manifest_paquete.json",
    } <= nombres
    # El manifest original del snapshot se conserva para trazabilidad.
    assert "raw/manifest.json" in nombres


def test_la_descripcion_del_rss_no_se_redistribuye(paquete: ZipFile):
    noticias = paquete.read("raw/noticias.csv").decode("utf-8")
    columnas = next(csv.reader(io.StringIO(noticias)))
    assert "descripcion" not in columnas
    assert "titulo" in columnas
    for nombre in paquete.namelist():
        assert DESCRIPCION not in paquete.read(nombre).decode("utf-8", errors="ignore"), nombre
    assert "decisión #35" in paquete.read("LEEME.md").decode("utf-8")


def test_manifest_del_paquete_coincide_con_lo_empaquetado(paquete: ZipFile):
    manifest = json.loads(paquete.read("manifest_paquete.json"))
    assert manifest["integridad_snapshot_original"] is True
    assert manifest["columnas_excluidas"] == {"noticias.csv": ["descripcion"]}
    for nombre, info in manifest["archivos"].items():
        assert hashlib.sha256(paquete.read(nombre)).hexdigest() == info["sha256"], nombre
