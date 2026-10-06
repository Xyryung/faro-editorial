"""Pruebas mínimas del esqueleto: paquete, configuración segura y reglas del puntaje."""

from pathlib import Path

import pytest

from faro_editorial import __version__
from faro_editorial.rules import load_rules
from faro_editorial.settings import ROOT_DIR, Settings

RULES_PATH = ROOT_DIR / "config" / "rules_v1.yaml"


def test_version_definida():
    assert __version__


def test_por_defecto_funciona_offline(monkeypatch):
    monkeypatch.delenv("OFFLINE", raising=False)
    assert Settings(_env_file=None).offline is True


def test_modelo_jev_fijado_sin_alias():
    assert "latest" not in Settings(_env_file=None).jev_model


def test_claves_no_aparecen_en_repr():
    s = Settings(_env_file=None, openrouter_api_key="sk-or-prueba-123")
    assert "sk-or-prueba-123" not in repr(s)
    assert s.has_openrouter_key is True


def test_env_example_sin_claves_reales():
    lineas = (ROOT_DIR / ".env.example").read_text(encoding="utf-8").splitlines()
    for linea in lineas:
        if "_API_KEY=" in linea and not linea.lstrip().startswith("#"):
            assert linea.split("=", 1)[1].strip() == "", f"Valor en .env.example: {linea}"


def test_pesos_suman_100():
    reglas = load_rules(RULES_PATH)
    assert sum(reglas.pesos.values()) == 100


@pytest.mark.parametrize(
    ("puntaje", "esperado"),
    [(0, "bajo"), (39.99, "bajo"), (40, "medio"), (69.99, "medio"), (70, "alto"), (100, "alto")],
)
def test_bandas_sin_solapamiento(puntaje, esperado):
    assert load_rules(RULES_PATH).banda(puntaje) == esperado


def test_puntaje_fuera_de_rango_falla():
    with pytest.raises(ValueError):
        load_rules(RULES_PATH).banda(101)


def test_app_arranca_sin_errores():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT_DIR / "app" / "main.py")).run(timeout=30)
    assert not app.exception
    assert app.title[0].value == "Faro Editorial"


def test_reglas_invalidas_se_rechazan(tmp_path: Path):
    malo = RULES_PATH.read_text(encoding="utf-8").replace("R: 30", "R: 31")
    archivo = tmp_path / "rules_malas.yaml"
    archivo.write_text(malo, encoding="utf-8")
    with pytest.raises(ValueError, match="sumar 100"):
        load_rules(archivo)
