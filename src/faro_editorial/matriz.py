"""Matriz de pruebas de aceptación T01–T10 (sección 9 del reto, issue #19).

Ejecuta las pruebas automáticas listadas en config/matriz_pruebas.yaml, agrupa los resultados
por caso y escribe la matriz en evaluacion/ (Markdown para leer, CSV para importar en la
página "Pruebas y métricas" de Notion y JSON con el detalle). Cada fila trae caso, entrada,
resultado esperado, resultado observado, evidencia de ejecución (fecha, commit y pruebas) y
las correcciones de pruebas que fallaron.

Uso:  uv run python -m faro_editorial.matriz
"""

import csv
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from faro_editorial.settings import ROOT_DIR

RUTA_CONFIG = ROOT_DIR / "config" / "matriz_pruebas.yaml"
DIR_SALIDA = ROOT_DIR / "evaluacion"

Resultado = Literal["pasa", "falla", "omitida"]
Estado = Literal["pasa", "pasa (parcial)", "falla", "pendiente", "omitida"]


class Correccion(BaseModel):
    fallo: str
    correccion: str
    pr: str
    issue: str | None = None  # issue de "Prueba fallida" que lo registra


class Caso(BaseModel):
    prueba: str
    esperado: str
    entrada: str
    cobertura: Literal["completa", "parcial"]
    falta: str | None = None
    issues: list[str] = []
    pruebas: list[str] = []
    correcciones: list[Correccion] = []


class ConfigMatriz(BaseModel):
    version: str
    casos: dict[str, Caso]


class FilaMatriz(BaseModel):
    id: str
    prueba: str
    entrada: str
    esperado: str
    observado: str
    estado: Estado
    pruebas_pasan: int
    pruebas_total: int
    pruebas: dict[str, Resultado]
    evidencia: str
    correcciones: list[Correccion]
    issues: list[str]


def load_matriz(path: Path = RUTA_CONFIG) -> ConfigMatriz:
    with Path(path).open(encoding="utf-8") as f:
        return ConfigMatriz.model_validate(yaml.safe_load(f))


# --- Ejecución ------------------------------------------------------------------------


def leer_junit(texto: str) -> dict[str, Resultado]:
    """Resultados por prueba ("tests/archivo.py::nombre[param]") desde un JUnit XML de pytest."""
    resultados: dict[str, Resultado] = {}
    for caso in ET.fromstring(texto).iter("testcase"):
        archivo = caso.get("classname", "").replace(".", "/") + ".py"
        nodo = f"{archivo}::{caso.get('name', '')}"
        if caso.find("failure") is not None or caso.find("error") is not None:
            resultados[nodo] = "falla"
        elif caso.find("skipped") is not None:
            resultados[nodo] = "omitida"
        else:
            resultados[nodo] = "pasa"
    return resultados


def ejecutar_pruebas(archivos: list[str], raiz: Path = ROOT_DIR) -> dict[str, Resultado]:
    """Corre pytest en un proceso aparte (sin red: respeta los marcadores por defecto)."""
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"]
            + sorted(archivos),
            cwd=raiz,
            capture_output=True,
            check=False,  # una prueba que falla también es un resultado de la matriz
        )
        if not junit.exists():
            raise RuntimeError(
                "pytest no generó resultados: revisa que las pruebas se puedan importar"
            )
        return leer_junit(junit.read_text(encoding="utf-8"))


def commit_actual(raiz: Path = ROOT_DIR) -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=raiz, capture_output=True, text=True
        ).stdout.strip()
        cambios = subprocess.run(
            ["git", "status", "--porcelain"], cwd=raiz, capture_output=True, text=True
        ).stdout.strip()
    except OSError:
        return "sin git"
    if not sha:
        return "sin git"
    return f"{sha} con cambios locales" if cambios else sha


# --- Matriz ---------------------------------------------------------------------------


def _coincide(nodo: str, patron: str) -> bool:
    # Prefijo de "archivo::prueba"; las variantes parametrizadas test_x[...] cuentan como test_x.
    return nodo.split("[", 1)[0].startswith(patron)


def armar_matriz(
    config: ConfigMatriz, resultados: dict[str, Resultado], fecha: datetime, commit: str
) -> tuple[list[FilaMatriz], list[str]]:
    filas: list[FilaMatriz] = []
    advertencias: list[str] = []
    for id_caso, caso in config.casos.items():
        pruebas: dict[str, Resultado] = {}
        for patron in caso.pruebas:
            encontradas = {n: r for n, r in resultados.items() if _coincide(n, patron)}
            if not encontradas:
                advertencias.append(f"{id_caso}: ninguna prueba coincide con '{patron}'")
            pruebas.update(encontradas)

        pasan = sum(r == "pasa" for r in pruebas.values())
        fallan = sum(r == "falla" for r in pruebas.values())
        if not pruebas:
            estado: Estado = "pendiente"
        elif fallan:
            estado = "falla"
        elif pasan == 0:
            estado = "omitida"
        else:
            estado = "pasa" if caso.cobertura == "completa" else "pasa (parcial)"

        if not pruebas:
            observado = f"Sin pruebas automáticas todavía. Falta: {caso.falta or 'definir'}"
        else:
            observado = f"{pasan} de {len(pruebas)} pruebas pasan"
            if fallan:
                nombres = ", ".join(n.split("::")[1] for n, r in pruebas.items() if r == "falla")
                observado += f"; fallan: {nombres}"
            if caso.falta:
                observado += f". Falta: {caso.falta}"

        filas.append(
            FilaMatriz(
                id=id_caso,
                prueba=caso.prueba,
                entrada=caso.entrada,
                esperado=caso.esperado,
                observado=observado,
                estado=estado,
                pruebas_pasan=pasan,
                pruebas_total=len(pruebas),
                pruebas=dict(sorted(pruebas.items())),
                evidencia=(
                    f"{fecha:%Y-%m-%d %H:%M} UTC, commit {commit}: {pasan}/{len(pruebas)} pruebas"
                    if pruebas
                    else "—"
                ),
                correcciones=caso.correcciones,
                issues=caso.issues,
            )
        )
    return filas, advertencias


# --- Salidas --------------------------------------------------------------------------


def _correcciones_texto(fila: FilaMatriz) -> str:
    if not fila.correcciones:
        return "—"
    return " / ".join(
        f"{c.fallo} → {c.correccion} ("
        + (f"issue {c.issue}; PR {c.pr}" if c.issue else f"PR {c.pr}")
        + ")"
        for c in fila.correcciones
    )


def _celda(texto: str) -> str:
    return texto.replace("|", "\\|").replace("\n", " ")


def escribir_matriz(
    filas: list[FilaMatriz],
    advertencias: list[str],
    config: ConfigMatriz,
    fecha: datetime,
    commit: str,
    destino: Path = DIR_SALIDA,
) -> dict[str, Path]:
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    rutas = {
        "md": destino / "matriz_pruebas.md",
        "csv": destino / "matriz_pruebas.csv",
        "json": destino / "matriz_pruebas.json",
    }
    columnas = [
        "ID",
        "Prueba",
        "Entrada",
        "Resultado esperado",
        "Resultado observado",
        "Estado",
        "Evidencia de ejecución",
        "Corrección",
        "Issues",
    ]

    def valores(f: FilaMatriz) -> list[str]:
        return [
            f.id,
            f.prueba,
            f.entrada,
            f.esperado,
            f.observado,
            f.estado,
            f.evidencia,
            _correcciones_texto(f),
            ", ".join(f.issues),
        ]

    with rutas["csv"].open("w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(columnas)
        escritor.writerows(valores(f) for f in filas)

    total = sum(f.pruebas_total for f in filas)
    pasan = sum(f.pruebas_pasan for f in filas)
    lineas = [
        "# Matriz de pruebas de aceptación (T01–T10)",
        "",
        f"Generada el {fecha:%Y-%m-%d %H:%M} UTC sobre el commit `{commit}` con "
        "`uv run python -m faro_editorial.matriz` "
        f"({config.version}; casos en `config/matriz_pruebas.yaml`).",
        "",
        f"**{sum(f.estado == 'pasa' for f in filas)} casos completos**, "
        f"{sum(f.estado == 'pasa (parcial)' for f in filas)} parciales, "
        f"{sum(f.estado == 'falla' for f in filas)} con fallos y "
        f"{sum(f.estado == 'pendiente' for f in filas)} pendientes. "
        f"Pruebas automáticas: {pasan} de {total} pasan.",
        "",
        "| " + " | ".join(columnas) + " |",
        "|" + "---|" * len(columnas),
    ]
    lineas += ["| " + " | ".join(_celda(v) for v in valores(f)) + " |" for f in filas]
    lineas += ["", "## Pruebas por caso", ""]
    for f in filas:
        lineas.append(f"**{f.id} · {f.prueba}**")
        lineas += [f"- `{n}`: {r}" for n, r in f.pruebas.items()] or ["- Sin pruebas todavía."]
        lineas.append("")
    if advertencias:
        lineas += ["## Advertencias", ""] + [f"- {a}" for a in advertencias] + [""]
    rutas["md"].write_text("\n".join(lineas), encoding="utf-8")

    rutas["json"].write_text(
        json.dumps(
            {
                "version": config.version,
                "generado_utc": fecha.isoformat(timespec="seconds"),
                "commit": commit,
                "advertencias": advertencias,
                "casos": [f.model_dump() for f in filas],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return rutas


def main() -> None:
    config = load_matriz()
    archivos = sorted({p.split("::", 1)[0] for c in config.casos.values() for p in c.pruebas})
    fecha = datetime.now(UTC)
    commit = commit_actual()
    resultados = ejecutar_pruebas(archivos)
    filas, advertencias = armar_matriz(config, resultados, fecha, commit)
    rutas = escribir_matriz(filas, advertencias, config, fecha, commit)

    for f in filas:
        print(f"{f.id}  {f.estado:<15} {f.pruebas_pasan:>2}/{f.pruebas_total:<2}  {f.prueba}")
    for a in advertencias:
        print(f"Aviso: {a}")
    print(f"\nMatriz: {rutas['md']}\nPara Notion: {rutas['csv']}")
    if any(f.estado == "falla" for f in filas):
        sys.exit(1)


if __name__ == "__main__":
    main()
