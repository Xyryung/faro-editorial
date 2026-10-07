"""Matriz T01–T10 (issue #19): lectura de resultados de pytest, estados por caso y salidas."""

import csv
import re
from datetime import UTC, datetime
from pathlib import Path

from faro_editorial.matriz import (
    Caso,
    ConfigMatriz,
    Correccion,
    armar_matriz,
    escribir_matriz,
    leer_junit,
    load_matriz,
)
from faro_editorial.settings import ROOT_DIR

FECHA = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.test_a" name="test_t01_ok" time="0.1"/>
  <testcase classname="tests.test_a" name="test_formatos[2025-01-01]" time="0.1"/>
  <testcase classname="tests.test_a" name="test_formatos[ayer]" time="0.1">
    <failure message="assert ...">detalle</failure>
  </testcase>
  <testcase classname="tests.test_b" name="test_en_vivo" time="0.0">
    <skipped message="online"/>
  </testcase>
</testsuite></testsuites>"""


def test_leer_junit_por_prueba_y_variante():
    assert leer_junit(JUNIT) == {
        "tests/test_a.py::test_t01_ok": "pasa",
        "tests/test_a.py::test_formatos[2025-01-01]": "pasa",
        "tests/test_a.py::test_formatos[ayer]": "falla",
        "tests/test_b.py::test_en_vivo": "omitida",
    }


def _caso(pruebas: list[str], cobertura: str = "completa", **extra) -> Caso:
    return Caso(
        prueba="Prueba",
        esperado="Esperado",
        entrada="Entrada",
        cobertura=cobertura,
        pruebas=pruebas,
        **extra,
    )


def test_estados_de_cada_caso():
    config = ConfigMatriz(
        version="v",
        casos={
            "T01": _caso(["tests/test_a.py::test_t01_"]),
            "T02": _caso(["tests/test_a.py::test_t01_"], cobertura="parcial", falta="agrupar"),
            "T03": _caso(["tests/test_a.py::test_formatos"]),
            "T04": _caso([], cobertura="parcial", falta="contradicciones"),
            "T05": _caso(["tests/test_b.py::test_en_vivo"]),
            "T06": _caso(["tests/test_a.py::test_no_existe"]),
        },
    )
    filas, advertencias = armar_matriz(config, leer_junit(JUNIT), FECHA, "abc1234")
    estados = {f.id: f.estado for f in filas}

    assert estados == {
        "T01": "pasa",
        "T02": "pasa (parcial)",
        "T03": "falla",  # una variante parametrizada falla
        "T04": "pendiente",
        "T05": "omitida",
        "T06": "pendiente",
    }
    t03 = next(f for f in filas if f.id == "T03")
    assert t03.pruebas_total == 2 and t03.pruebas_pasan == 1
    assert "fallan: test_formatos[ayer]" in t03.observado
    assert t03.evidencia == "2026-10-07 12:00 UTC, commit abc1234: 1/2 pruebas"
    assert "Falta: contradicciones" in next(f for f in filas if f.id == "T04").observado
    assert advertencias == ["T06: ninguna prueba coincide con 'tests/test_a.py::test_no_existe'"]


def test_salidas_markdown_csv_y_json(tmp_path: Path):
    config = ConfigMatriz(
        version="v",
        casos={
            "T07": _caso(
                ["tests/test_a.py::test_t01_"],
                issues=["#40"],
                correcciones=[
                    Correccion(fallo="No escapaba </DATOS>", correccion="regex", pr="#40")
                ],
            )
        },
    )
    filas, advertencias = armar_matriz(config, leer_junit(JUNIT), FECHA, "abc1234")
    rutas = escribir_matriz(filas, advertencias, config, FECHA, "abc1234", tmp_path)

    with rutas["csv"].open(encoding="utf-8-sig", newline="") as f:
        (fila,) = list(csv.DictReader(f))
    assert fila["ID"] == "T07" and fila["Estado"] == "pasa"
    assert fila["Corrección"] == "No escapaba </DATOS> → regex (PR #40)"
    md = rutas["md"].read_text(encoding="utf-8")
    assert "commit `abc1234`" in md and "`tests/test_a.py::test_t01_ok`: pasa" in md
    assert '"commit": "abc1234"' in rutas["json"].read_text(encoding="utf-8")


def test_config_real_cubre_t01_a_t10_y_sus_pruebas_existen():
    """Si alguien renombra o borra una prueba, la matriz no debe quedar desactualizada."""
    config = load_matriz()
    assert list(config.casos) == [f"T{i:02d}" for i in range(1, 11)]
    for id_caso, caso in config.casos.items():
        if caso.cobertura == "parcial":
            assert caso.falta, f"{id_caso}: un caso parcial debe decir qué falta"
        for patron in caso.pruebas:
            archivo, prefijo = patron.split("::")
            fuente = (ROOT_DIR / archivo).read_text(encoding="utf-8")
            assert re.search(rf"^def {re.escape(prefijo)}", fuente, re.MULTILINE), (
                f"{id_caso}: no existe ninguna prueba '{patron}'"
            )


def test_cada_prueba_fallida_tiene_su_issue():
    """Criterio del #19: cada prueba que falló tiene su issue de "Prueba fallida" y su PR."""
    for id_caso, caso in load_matriz().casos.items():
        for c in caso.correcciones:
            assert c.issue and c.pr, f"{id_caso}: corrección sin issue o sin PR: {c.fallo}"


def test_correccion_con_issue_en_la_salida(tmp_path: Path):
    caso = _caso(
        ["tests/test_a.py::test_t01_"],
        correcciones=[Correccion(fallo="F", correccion="C", pr="#40", issue="#43")],
    )
    config = ConfigMatriz(version="v", casos={"T07": caso})
    filas, adv = armar_matriz(config, leer_junit(JUNIT), FECHA, "abc1234")
    rutas = escribir_matriz(filas, adv, config, FECHA, "abc1234", tmp_path)
    assert "F → C (issue #43; PR #40)" in rutas["md"].read_text(encoding="utf-8")
