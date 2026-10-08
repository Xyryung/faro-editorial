"""Mostrar el registro detrás de un ID de evidencia (#22): "Muéstrame de dónde proviene esta
cifra y de qué año es"."""

from pathlib import Path

import pytest

from faro_editorial.carga import cargar_snapshot
from faro_editorial.evidencia import buscar_evidencia, main


@pytest.fixture
def db(raw: Path, tmp_path: Path) -> Path:
    return cargar_snapshot(raw, tmp_path / "processed").rutas["db"]


def _campos(resultado: dict) -> dict[str, str]:
    return dict(resultado["campos"])


def test_indicador_muestra_fuente_anio_unidad_y_url(db: Path):
    r = buscar_evidencia("BM:PAN:NY.GDP.MKTP.KD.ZG:2023", db)
    campos = _campos(r)
    assert r["encontrada"] is True
    assert campos["País"] == "Panamá (PAN)"
    assert campos["Año del dato"] == "2023"
    assert campos["Valor"] == "7.3"
    assert campos["Unidad"] == "% anual"
    assert campos["URL de la consulta"].startswith("https://api.worldbank.org/")
    assert campos["Licencia"] == "CC BY 4.0"
    assert "no la situación de hoy" in r["nota"]


def test_anio_sin_dato_no_se_muestra_como_cero(db: Path):
    campos = _campos(buscar_evidencia("BM:PAN:NY.GDP.MKTP.KD.ZG:2024", db))
    assert campos["Valor"] == "sin dato publicado"


def test_sismo_con_hora_de_panama_y_advertencia(db: Path):
    r = buscar_evidencia("USGS:us7000aaaa", db)
    campos = _campos(r)
    assert campos["Magnitud"] == "4.6"
    assert campos["Hora"] == "2024-01-01 00:00 UTC (2023-12-31 19:00 hora de Panamá)"
    assert campos["URL del evento"].startswith("https://earthquake.usgs.gov/")
    assert "no equivale al territorio de Panamá" in r["nota"]


def test_noticia_con_fechas_separadas(db: Path):
    campos = _campos(buscar_evidencia("n002", db))
    assert campos["Publicada"] == "sin dato"
    assert campos["Detectada (GDELT)"].startswith("2025-09-16 12:00 UTC")


@pytest.mark.parametrize(
    "id_evidencia", ["BM:PAN:FP.CPI.TOTL.ZG:2024", "BM:PAN:sin-anio", "USGS:no-existe", "zzz"]
)
def test_id_inexistente_no_inventa(db: Path, id_evidencia: str):
    r = buscar_evidencia(id_evidencia, db)
    assert r["encontrada"] is False
    assert r["campos"] == []
    assert "No existe evidencia" in r["nota"]


def test_comando(db: Path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(db.parent.parent))
    from faro_editorial.settings import get_settings

    get_settings.cache_clear()
    try:
        main(["BM:PAN:NY.GDP.MKTP.KD.ZG:2023"])
        with pytest.raises(SystemExit):
            main(["USGS:no-existe"])
    finally:
        get_settings.cache_clear()
    salida = capsys.readouterr().out
    assert "Año del dato" in salida and "2023" in salida
    assert "No existe evidencia con el ID USGS:no-existe" in salida
