"""Fixtures compartidas. Los archivos de tests/datos/t01/ son sintéticos (T01)."""

import json
import shutil
from pathlib import Path

import pytest

from faro_editorial.carga import sha256_archivo

DATOS_T01 = Path(__file__).parent / "datos" / "t01"


def _escribir_manifest(raw: Path) -> None:
    # Se calcula al vuelo: Git puede cambiar los fines de línea de los archivos de
    # prueba en Windows, y un hash fijo dejaría de coincidir.
    archivos = {
        p.name: {"sha256": sha256_archivo(p)}
        for p in sorted(raw.iterdir())
        if p.is_file() and p.name != "manifest.json"
    }
    manifest = {
        "version": "sintetico-t01",
        "fecha_corte_utc": "2025-10-01T00:00:00Z",
        "archivos": archivos,
    }
    (raw / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    """Copia del snapshot sintético T01 en tmp_path/raw, con su manifest.json."""
    destino = tmp_path / "raw"
    shutil.copytree(DATOS_T01, destino)
    _escribir_manifest(destino)
    return destino


@pytest.fixture
def escribir_manifest():
    """Para pruebas que modifican el snapshot y necesitan un manifest coherente."""
    return _escribir_manifest
