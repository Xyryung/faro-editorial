"""Métricas de la ejecución final (issue #19, sección 9.1 del reto).

El reto pide reportar numerador, denominador y fallos, sin esconder errores tras un promedio.
Este módulo calcula cada métrica desde las salidas guardadas del sistema y de las etiquetas
humanas; si falta una entrada, la métrica queda "pendiente" con el comando que la produce.
Nunca se rellena un número.

| Métrica (9.1)          | Fuente                                                         |
|------------------------|----------------------------------------------------------------|
| Cobertura de citas     | data/processed/borradores.jsonl (#15)                          |
| Validez de sustento    | data/evaluacion/pares_sustento.csv, etiquetado por personas    |
| Abstención             | config/consultas_v1.yaml corrido con la búsqueda (#13)         |
| Clasificación          | data/evaluacion/evaluacion_tema.json (clasificacion evaluar)   |
| Agrupación             | data/evaluacion/evaluacion_agrupacion.json (pares a ciegas)    |
| Precision@5            | data/evaluacion/seleccion_editor.csv, selección a ciegas       |
| Eficiencia             | tiempos y costos de consultas y borradores                     |
| Ahorro de tiempo       | data/evaluacion/ahorro_tiempo.csv, tarea manual y asistida     |

Cada proporción lleva su intervalo de confianza de Wilson al 95 %: con muestras de 5 a 60
casos, el intervalo dice cuánto podría cambiar el resultado con otra muestra.

Las entradas con titulares viven en data/evaluacion/ (no se versiona, decisión #28). El
reporte, en evaluacion/metricas.md y .json, solo lleva conteos e IDs.

Comandos (desde la raíz del repo, después de la bandeja y los borradores):
    uv run python -m faro_editorial.metricas pares       # CSV de afirmación-evidencia a etiquetar
    uv run python -m faro_editorial.metricas seleccion   # temas para la selección a ciegas (P@5)
    uv run python -m faro_editorial.metricas consultas   # corre el conjunto de consultas
    uv run python -m faro_editorial.metricas reporte     # escribe evaluacion/metricas.md
"""

import argparse
import csv
import json
import random
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from faro_editorial.borradores import (
    TIPOS_CON_CITA,
    evidencia_del_tema,
    leer_borradores,
)
from faro_editorial.borradores import (
    load_config as load_config_borradores,
)
from faro_editorial.decisiones import NOMBRE_REGISTRO
from faro_editorial.settings import ROOT_DIR

RUTA_CONSULTAS = ROOT_DIR / "config" / "consultas_v1.yaml"
CARPETA_SALIDA = ROOT_DIR / "evaluacion"
NOMBRE_PARES = "pares_sustento.csv"
NOMBRE_SELECCION = "seleccion_editor.csv"
NOMBRE_RESULTADOS_CONSULTAS = "consultas_resultados.json"
NOMBRE_EVALUACION_TEMA = "evaluacion_tema.json"
NOMBRE_EVALUACION_AGRUPACION = "evaluacion_agrupacion.json"
NOMBRE_AHORRO = "ahorro_tiempo.csv"
COLUMNAS_AHORRO = ["tarea", "modo", "persona", "minutos", "completa", "comentario"]
NOMBRE_METRICAS = "metricas"

# Metas orientativas de la sección 9.1 (no son resultados).
META_COBERTURA = 1.0
META_SUSTENTO = 0.9
MIN_PARES_SUSTENTO = 30
META_ABSTENCION = 0.8
META_MEDIANA_S = 15.0

SUSTENTO_VALIDO = ("respaldada", "no_respaldada")
COLUMNAS_PARES = [
    "id_caso",
    "id_afirmacion",
    "tipo",
    "afirmacion",
    "evidencia_citada",
    "sustento_humano",
    "etiquetador",
    "comentario",
]
COLUMNAS_SELECCION = [
    "orden",
    "id_grupo",
    "titulo",
    "medios",
    "fecha_original_panama",
    "elegido",
    "evaluador",
]
SI = {"x", "si", "sí", "1", "true", "s"}


def _ratio(numerador: int, denominador: int) -> float | None:
    return round(numerador / denominador, 4) if denominador else None


def intervalo_wilson(numerador: int, denominador: int, z: float = 1.96) -> list[float] | None:
    """Intervalo de Wilson al 95 % para una proporción. A diferencia del intervalo normal,
    no se sale de [0, 1] ni colapsa a un punto con 0/n o n/n, que es lo común en muestras
    chicas."""
    if not denominador:
        return None
    p = numerador / denominador
    z2 = z * z
    centro = (p + z2 / (2 * denominador)) / (1 + z2 / denominador)
    margen = (
        z
        * ((p * (1 - p) / denominador + z2 / (4 * denominador * denominador)) ** 0.5)
        / (1 + z2 / denominador)
    )
    return [round(max(0.0, centro - margen), 4), round(min(1.0, centro + margen), 4)]


def _con_intervalos(dato: Any) -> Any:
    """Agrega ic95 a cada proporción (dict con numerador y denominador) del reporte."""
    if isinstance(dato, dict):
        nuevo = {k: _con_intervalos(v) for k, v in dato.items()}
        num, den = dato.get("numerador"), dato.get("denominador")
        if isinstance(num, int) and isinstance(den, int):
            nuevo.setdefault("ic95", intervalo_wilson(num, den))
        return nuevo
    if isinstance(dato, list):
        return [_con_intervalos(x) for x in dato]
    return dato


def _pendiente(motivo: str, comando: str) -> dict[str, Any]:
    return {"estado": "pendiente", "motivo": motivo, "comando": comando}


def _percentil(valores: list[float], p: float) -> float | None:
    """Percentil con interpolación lineal (p entre 0 y 100)."""
    if not valores:
        return None
    orden = sorted(valores)
    if len(orden) == 1:
        return round(orden[0], 3)
    posicion = (len(orden) - 1) * p / 100
    bajo = int(posicion)
    alto = min(bajo + 1, len(orden) - 1)
    return round(orden[bajo] + (orden[alto] - orden[bajo]) * (posicion - bajo), 3)


# --- Cobertura de citas ---------------------------------------------------------------


def metrica_cobertura(borradores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Afirmaciones factuales (hecho, declaración, inferencia) que enlazan una evidencia
    identificable del tema. Las rechazadas (citaban un ID o campo que no existe) cuentan en
    el denominador: se emitieron y fallaron, aunque el texto las marque [aN: sin respaldo]."""
    usados = [b for b in borradores.values() if not b.get("abstencion")]
    if not usados:
        return _pendiente(
            "No hay borradores generados.",
            "uv run python -m faro_editorial.borradores --top 5",
        )
    con_cita, rechazadas, fallos = 0, 0, []
    for b in usados:
        fuentes = set(b["ids_fuente"])
        for a in b["afirmaciones"]:
            if a["tipo"] not in TIPOS_CON_CITA:
                continue
            if a["citas"] and all(c["id_evidencia"] in fuentes for c in a["citas"]):
                con_cita += 1
            else:
                fallos.append(f"{b['id_caso']}:{a['id']} (sin cita identificable)")
        for r in b.get("afirmaciones_rechazadas", []):
            rechazadas += 1
            fallos.append(f"{b['id_caso']}:{r['id']} (rechazada: {r['motivo']})")
    total = con_cita + rechazadas + sum(1 for f in fallos if "sin cita" in f)
    marcas = [
        b["id_caso"]
        for b in usados
        for c in b.get("comprobaciones", [])
        if c["nombre"] == "marcas" and not c["ok"]
    ]
    return {
        "estado": "medida",
        "borradores": len(usados),
        "abstenciones": len(borradores) - len(usados),
        "numerador": con_cita,
        "denominador": total,
        "valor": _ratio(con_cita, total),
        "meta": META_COBERTURA,
        "cumple": total > 0 and con_cita == total,
        "rechazadas": rechazadas,
        "borradores_con_marcas_invalidas": marcas,
        "fallos": fallos,
        "metodo": "Afirmaciones factuales emitidas con al menos una cita a un ID de la "
        "evidencia recuperada del tema / afirmaciones factuales emitidas (incluye las "
        "rechazadas). Las hipótesis no cuentan: se investigan, no se afirman.",
    }


# --- Validez de sustento (pares afirmación-evidencia) ---------------------------------


def pares_para_etiquetar(
    processed_dir: Path, destino: Path, forzar: bool = False
) -> tuple[Path, int]:
    """CSV con cada afirmación factual y el texto exacto de la evidencia que cita, para que
    una persona marque si la evidencia la respalda. No incluye el veredicto de Jev: se
    etiqueta a ciegas, como la clasificación (docs/etiquetado.md)."""
    destino = Path(destino)
    if destino.exists() and not forzar:
        raise FileExistsError(
            f"{destino} ya existe (puede tener etiquetas). Usa --forzar para regenerarlo."
        )
    from faro_editorial.interfaz import cargar_bandeja

    borradores = leer_borradores(processed_dir)
    temas = {t["id_grupo"]: t for t in cargar_bandeja(processed_dir)[0]["temas"]}
    config = load_config_borradores()
    filas, sin_tema = [], []
    for id_caso, b in borradores.items():
        if b.get("abstencion"):
            continue
        if id_caso not in temas:
            sin_tema.append(id_caso)
            continue
        evidencias = {e.id: e for e in evidencia_del_tema(temas[id_caso], config)}
        for a in b["afirmaciones"]:
            if a["tipo"] not in TIPOS_CON_CITA:
                continue
            citada = []
            for c in a["citas"]:
                e = evidencias.get(c["id_evidencia"])
                valor = e.campos.get(c["campo"], "(campo no disponible)") if e else "(sin ID)"
                citada.append(f"[{c['id_evidencia']} · {c['campo']}] {valor}")
            filas.append(
                {
                    "id_caso": id_caso,
                    "id_afirmacion": a["id"],
                    "tipo": a["tipo"],
                    "afirmacion": a["texto"],
                    "evidencia_citada": " | ".join(citada),
                    "sustento_humano": "",
                    "etiquetador": "",
                    "comentario": "",
                }
            )
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: Excel
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS_PARES)
        escritor.writeheader()
        escritor.writerows(filas)
    if sin_tema:
        print(
            f"Aviso: {len(sin_tema)} borrador(es) de temas que ya no están en la bandeja "
            f"({', '.join(sin_tema)}): se omiten. ¿Se regeneró la bandeja?"
        )
    return destino, len(filas)


def leer_pares(ruta: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Filas etiquetadas válidas y avisos (sin etiqueta o con un valor desconocido)."""
    filas, avisos = [], []
    with Path(ruta).open(encoding="utf-8-sig", newline="") as f:
        for numero, fila in enumerate(csv.DictReader(f), start=2):
            valor = (fila.get("sustento_humano") or "").strip().lower()
            if not valor:
                avisos.append(f"línea {numero}: sin etiquetar")
            elif valor not in SUSTENTO_VALIDO:
                avisos.append(f"línea {numero}: valor desconocido {valor!r}")
            else:
                fila["sustento_humano"] = valor
                filas.append(fila)
    return filas, avisos


def metrica_sustento(ruta_pares: Path, borradores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Validez de sustento según revisión humana y acuerdo de Jev con esa revisión."""
    if not Path(ruta_pares).exists():
        return _pendiente(
            "No hay pares afirmación-evidencia etiquetados.",
            "uv run python -m faro_editorial.metricas pares  (y completar sustento_humano)",
        )
    filas, avisos = leer_pares(ruta_pares)
    if not filas:
        return _pendiente(
            f"{ruta_pares.name} no tiene filas etiquetadas.",
            "completar sustento_humano con respaldada / no_respaldada",
        )
    respaldadas = sum(f["sustento_humano"] == "respaldada" for f in filas)
    jev = {
        (id_caso, a["id"]): a["verificacion"]["estado"]
        for id_caso, b in borradores.items()
        for a in b.get("afirmaciones", [])
    }
    acuerdo, comparables, desacuerdos = 0, 0, []
    for f in filas:
        estado = jev.get((f["id_caso"], f["id_afirmacion"]))
        if estado not in ("respaldada", "no_respaldada", "dudosa"):
            continue
        comparables += 1
        jev_dice = "respaldada" if estado == "respaldada" else "no_respaldada"
        if jev_dice == f["sustento_humano"]:
            acuerdo += 1
        else:
            desacuerdos.append(
                f"{f['id_caso']}:{f['id_afirmacion']} (persona: {f['sustento_humano']}, "
                f"Jev: {estado})"
            )
    etiquetadores = sorted({f.get("etiquetador", "").strip() for f in filas} - {""})
    return {
        "estado": "medida",
        "numerador": respaldadas,
        "denominador": len(filas),
        "valor": _ratio(respaldadas, len(filas)),
        "meta": META_SUSTENTO,
        "minimo_pares": MIN_PARES_SUSTENTO,
        "cumple": len(filas) >= MIN_PARES_SUSTENTO and (respaldadas / len(filas)) >= META_SUSTENTO,
        "suficientes_pares": len(filas) >= MIN_PARES_SUSTENTO,
        "no_respaldadas": [
            f"{f['id_caso']}:{f['id_afirmacion']}"
            for f in filas
            if f["sustento_humano"] == "no_respaldada"
        ],
        "etiquetadores": etiquetadores,
        "sin_etiquetar": len(avisos),
        "acuerdo_jev": {
            "numerador": acuerdo,
            "denominador": comparables,
            "valor": _ratio(acuerdo, comparables),
            "desacuerdos": desacuerdos,
            "metodo": "Jev 'respaldada' frente a la persona; 'dudosa' y 'no_respaldada' "
            "cuentan como no respaldada.",
        },
        "metodo": "Afirmaciones que la persona marca 'respaldada' por la evidencia citada / "
        "pares etiquetados. Etiquetado a ciegas: el CSV no muestra el veredicto de Jev.",
    }


# --- Abstención (conjunto de consultas) -----------------------------------------------


class Consulta(BaseModel):
    id: str
    pregunta: str
    etiqueta: Literal["respondible", "no_respondible"]
    evidencia_esperada: list[str] = Field(default_factory=list)
    dificil: bool = False

    @model_validator(mode="after")
    def _evidencia_segun_etiqueta(self) -> "Consulta":
        if self.etiqueta == "respondible" and not self.evidencia_esperada:
            raise ValueError(f"{self.id}: una consulta respondible necesita evidencia_esperada")
        if self.etiqueta == "no_respondible" and self.evidencia_esperada:
            raise ValueError(f"{self.id}: una consulta no respondible no lleva evidencia")
        return self


class ConjuntoConsultas(BaseModel):
    version: str
    revisado_por: str = ""
    consultas: list[Consulta]

    @model_validator(mode="after")
    def _ids_unicos(self) -> "ConjuntoConsultas":
        ids = [c.id for c in self.consultas]
        if len(ids) != len(set(ids)):
            raise ValueError("IDs de consulta repetidos")
        return self


def load_consultas(path: Path = RUTA_CONSULTAS) -> ConjuntoConsultas:
    with Path(path).open(encoding="utf-8") as f:
        return ConjuntoConsultas.model_validate(yaml.safe_load(f))


def _lineas_registro(ruta: Path) -> int:
    if not ruta.exists():
        return 0
    with ruta.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


def ejecutar_consultas(
    buscador: Any,
    cliente: Any,
    conjunto: ConjuntoConsultas,
    ruta_registro: Path | None = None,
    reloj: Any = time.perf_counter,
) -> dict[str, Any]:
    """Corre cada consulta por la búsqueda con su compuerta y guarda lo observado."""
    antes = _lineas_registro(ruta_registro) if ruta_registro else 0
    resultados = []
    for c in conjunto.consultas:
        inicio = reloj()
        r = buscador.responder(c.pregunta, cliente)
        latencia = reloj() - inicio
        ids = [cita.id_noticia for cita in r.citas]
        resultados.append(
            {
                "id": c.id,
                "etiqueta": c.etiqueta,
                "dificil": c.dificil,
                "abstencion": r.abstencion,
                "ids_citados": ids,
                "acierta_evidencia": bool(set(ids) & set(c.evidencia_esperada)),
                "metodo": r.metodo,
                "motivo": r.motivo,
                "nota": r.motivo_respaldo,
                "latencia_s": round(latencia, 4),
            }
        )
    costo, tokens_e, tokens_s, llamadas = 0.0, 0, 0, 0
    if ruta_registro and ruta_registro.exists():
        with ruta_registro.open(encoding="utf-8") as f:
            for numero, linea in enumerate(f):
                if numero < antes:
                    continue
                fila = json.loads(linea)
                llamadas += 1
                costo += fila.get("costo_usd") or 0.0
                tokens_e += fila.get("tokens_entrada") or 0
                tokens_s += fila.get("tokens_salida") or 0
    return {
        "version": conjunto.version,
        "revisado_por": conjunto.revisado_por,
        "generado_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "con_jev": cliente is not None,
        "llamadas_en_vivo": llamadas,
        "costo_usd_en_vivo": round(costo, 6),
        "tokens_entrada": tokens_e,
        "tokens_salida": tokens_s,
        "resultados": resultados,
    }


def metrica_abstencion(ruta_resultados: Path) -> dict[str, Any]:
    if not Path(ruta_resultados).exists():
        return _pendiente(
            "No se corrió el conjunto de consultas.",
            "uv run python -m faro_editorial.metricas consultas",
        )
    datos = json.loads(Path(ruta_resultados).read_text(encoding="utf-8"))
    res = datos["resultados"]
    no_resp = [r for r in res if r["etiqueta"] == "no_respondible"]
    resp = [r for r in res if r["etiqueta"] == "respondible"]
    bien_rechazadas = [r for r in no_resp if r["abstencion"]]
    mal_abstenidas = [r for r in resp if r["abstencion"]]
    con_evidencia = [r for r in resp if not r["abstencion"] and r["acierta_evidencia"]]
    dificiles = [r for r in no_resp if r["dificil"]]
    return {
        "estado": "medida",
        "numerador": len(bien_rechazadas),
        "denominador": len(no_resp),
        "valor": _ratio(len(bien_rechazadas), len(no_resp)),
        "meta": META_ABSTENCION,
        "cumple": bool(no_resp) and len(bien_rechazadas) / len(no_resp) >= META_ABSTENCION,
        "no_respondibles_respondidas": [r["id"] for r in no_resp if not r["abstencion"]],
        "dificiles_rechazadas": {
            "numerador": sum(r["abstencion"] for r in dificiles),
            "denominador": len(dificiles),
        },
        "abstenciones_incorrectas": {
            "numerador": len(mal_abstenidas),
            "denominador": len(resp),
            "valor": _ratio(len(mal_abstenidas), len(resp)),
            "ids": [r["id"] for r in mal_abstenidas],
        },
        "respondidas_con_evidencia_esperada": {
            "numerador": len(con_evidencia),
            "denominador": len(resp),
            "valor": _ratio(len(con_evidencia), len(resp)),
            "fallos": [r["id"] for r in resp if r not in con_evidencia],
        },
        "con_jev": datos["con_jev"],
        "conjunto": datos["version"],
        "revisado_por": datos.get("revisado_por") or "sin completar",
        "metodo": "No respondibles rechazadas / no respondibles del conjunto. Una respondible "
        "solo cuenta como bien respondida si cita al menos una evidencia esperada.",
    }


# --- Precision@5 (selección a ciegas) -------------------------------------------------


def seleccion_para_editor(
    bandeja: dict[str, Any], destino: Path, n: int = 15, semilla: int = 7, forzar: bool = False
) -> Path:
    """Los n temas mejor puntuados, en orden aleatorio y sin puntaje, para que una persona
    elija a ciegas los 5 que revisaría primero."""
    destino = Path(destino)
    if destino.exists() and not forzar:
        raise FileExistsError(f"{destino} ya existe. Usa --forzar para regenerarlo.")
    temas = list(bandeja["temas"][:n])
    random.Random(semilla).shuffle(temas)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS_SELECCION)
        escritor.writeheader()
        for i, t in enumerate(temas, 1):
            escritor.writerow(
                {
                    "orden": i,
                    "id_grupo": t["id_grupo"],
                    "titulo": t["titulo"],
                    "medios": len(t.get("procedencias") or []),
                    "fecha_original_panama": t.get("fecha_original_panama") or "",
                    "elegido": "",
                    "evaluador": "",
                }
            )
    return destino


def metrica_precision5(bandeja: dict[str, Any] | None, ruta_seleccion: Path) -> dict[str, Any]:
    comando = "uv run python -m faro_editorial.metricas seleccion  (y marcar 5 con x)"
    if bandeja is None:
        return _pendiente("No hay bandeja.", "uv run python -m faro_editorial.bandeja")
    if not Path(ruta_seleccion).exists():
        return _pendiente("No hay selección humana para comparar.", comando)
    with Path(ruta_seleccion).open(encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f))
    elegidos = [r["id_grupo"] for r in filas if (r.get("elegido") or "").strip().lower() in SI]
    if len(elegidos) != 5:
        return _pendiente(f"Se esperaban 5 temas elegidos y hay {len(elegidos)}.", comando)
    top5 = [t["id_grupo"] for t in bandeja["temas"][:5]]
    aciertos = sorted(set(elegidos) & set(top5))
    evaluadores = sorted({(r.get("evaluador") or "").strip() for r in filas} - {""})
    return {
        "estado": "medida",
        "numerador": len(aciertos),
        "denominador": 5,
        "valor": _ratio(len(aciertos), 5),
        "coinciden": aciertos,
        "solo_sistema": [i for i in top5 if i not in aciertos],
        "solo_persona": [i for i in elegidos if i not in aciertos],
        "evaluadores": evaluadores or ["sin completar"],
        "exploratoria": True,
        "candidatos": len(filas),
        "metodo": f"Una persona eligió a ciegas 5 de los {len(filas)} temas mejor puntuados, "
        "presentados en orden aleatorio y sin puntaje; P@5 = coincidencias con el top 5 del "
        "sistema / 5. Exploratoria: la eligió un integrante del equipo, no un editor de TVN.",
    }


# --- Clasificación y eficiencia -------------------------------------------------------


def metrica_clasificacion(ruta_evaluacion: Path) -> dict[str, Any]:
    if not Path(ruta_evaluacion).exists():
        return _pendiente(
            "No hay evaluación de la clasificación contra etiquetas humanas.",
            "uv run python -m faro_editorial.clasificacion evaluar",
        )
    datos = json.loads(Path(ruta_evaluacion).read_text(encoding="utf-8"))
    return {
        "estado": "medida",
        "n_etiquetas": datos["n_etiquetas"],
        "metodo": datos["metodo"],
        "metodo_etiquetado": "docs/etiquetado.md",
        "variantes": {
            nombre: {
                "macro_f1": v["macro_f1"],
                "aciertos": v["aciertos"],
                "n": v["n"],
                "abstenciones": v.get("abstenciones", 0),
            }
            for nombre, v in datos["variantes"].items()
        },
    }


def metrica_agrupacion(ruta_evaluacion: Path) -> dict[str, Any]:
    if not Path(ruta_evaluacion).exists():
        return _pendiente(
            "No hay pares de titulares etiquetados para la agrupación.",
            "uv run python -m faro_editorial.evaluacion_agrupacion pares (y luego evaluar)",
        )
    datos = json.loads(Path(ruta_evaluacion).read_text(encoding="utf-8"))
    return {
        "estado": "medida",
        "metodo": datos["metodo"],
        "pares": datos["etiquetados"],
        "mismo_evento": datos["mismo_evento"],
        "etiquetadores": datos["etiquetadores"],
        "estratos": datos["estratos"],
        "metodos": datos["metodos"],
    }


def metrica_ahorro_tiempo(ruta: Path) -> dict[str, Any]:
    """Tarea equivalente hecha a mano y con Faro (sección 9.1): mediana de minutos por modo.
    Solo cuenta las tareas completas; con pocas pruebas por modo es un dato orientativo."""
    if not Path(ruta).exists():
        return _pendiente(
            "No hay tareas cronometradas a mano y con Faro.",
            f"completar data/evaluacion/{NOMBRE_AHORRO} (protocolo en docs/ahorro_tiempo.md)",
        )
    with Path(ruta).open(encoding="utf-8", newline="") as f:
        filas = [x for x in csv.DictReader(f) if (x.get("minutos") or "").strip()]
    por_modo: dict[str, list[float]] = {"manual": [], "asistido": []}
    incompletas = 0
    for x in filas:
        modo = (x.get("modo") or "").strip().lower()
        if modo not in por_modo:
            continue
        if (x.get("completa") or "").strip().lower() not in SI:
            incompletas += 1
            continue
        por_modo[modo].append(float(x["minutos"].replace(",", ".")))
    if not por_modo["manual"] or not por_modo["asistido"]:
        return _pendiente(
            "Falta al menos una tarea completa a mano y otra con Faro.",
            f"completar data/evaluacion/{NOMBRE_AHORRO}",
        )
    manual = statistics.median(por_modo["manual"])
    asistido = statistics.median(por_modo["asistido"])
    return {
        "estado": "medida",
        "metodo": (
            "Misma tarea (de fuentes sueltas a un tema con evidencia y preguntas pendientes) "
            "hecha a mano y con Faro, cronometrada; mediana de minutos por modo. Solo tareas "
            "completas."
        ),
        "tareas": len({(x.get("tarea") or "").strip() for x in filas}),
        "n_manual": len(por_modo["manual"]),
        "n_asistido": len(por_modo["asistido"]),
        "mediana_manual_min": round(manual, 2),
        "mediana_asistido_min": round(asistido, 2),
        "ahorro": round(1 - asistido / manual, 4) if manual else None,
        "incompletas": incompletas,
        "personas": sorted({(x.get("persona") or "").strip() for x in filas} - {""}),
    }


def metrica_eficiencia(
    ruta_resultados: Path, borradores: dict[str, dict[str, Any]], ruta_registro: Path
) -> dict[str, Any]:
    salida: dict[str, Any] = {"estado": "medida", "meta_mediana_consulta_s": META_MEDIANA_S}
    if Path(ruta_resultados).exists():
        datos = json.loads(Path(ruta_resultados).read_text(encoding="utf-8"))
        tiempos = [r["latencia_s"] for r in datos["resultados"]]
        mediana = round(statistics.median(tiempos), 3) if tiempos else None
        salida["consultas"] = {
            "n": len(tiempos),
            "mediana_s": mediana,
            "p95_s": _percentil(tiempos, 95),
            "cumple_meta": mediana is not None and mediana <= META_MEDIANA_S,
            "con_jev": datos["con_jev"],
            "llamadas_en_vivo": datos["llamadas_en_vivo"],
            "costo_usd_en_vivo": datos["costo_usd_en_vivo"],
            "costo_usd_por_consulta": round(datos["costo_usd_en_vivo"] / len(tiempos), 6)
            if tiempos
            else None,
            "tokens_entrada": datos["tokens_entrada"],
            "tokens_salida": datos["tokens_salida"],
            "preparacion_indice_s": datos.get("preparacion_indice_s"),
        }
    else:
        salida["consultas"] = _pendiente(
            "No se corrió el conjunto de consultas.",
            "uv run python -m faro_editorial.metricas consultas",
        )
    # Borradores: tiempo y costo de la corrida en vivo que los produjo (preparación por lotes,
    # antes de la demo; en la demo salen de la caché).
    vivos = [b for b in borradores.values() if not b.get("abstencion") and b.get("ia")]
    if vivos:
        tiempos = [b["ia"]["latencia_s"] for b in vivos if b["ia"].get("latencia_s")]
        salida["borradores"] = {
            "n": len(vivos),
            "mediana_s": round(statistics.median(tiempos), 3) if tiempos else None,
            "p95_s": _percentil(tiempos, 95),
            "intentos_promedio": round(statistics.mean(b["ia"]["intentos"] for b in vivos), 2),
            "costo_usd_llm": round(sum(b["ia"]["costo_usd_llm"] for b in vivos), 6),
            "costo_usd_jev": round(sum(b["ia"]["costo_usd_jev"] for b in vivos), 6),
            "nota": "Solo el tiempo del LLM (todos los intentos), sin la verificación con Jev. "
            "Es preparación por lotes antes de la demo, no la consulta.",
        }
    tokens = {"entrada": 0, "salida": 0}
    if Path(ruta_registro).exists():
        with Path(ruta_registro).open(encoding="utf-8") as f:
            for linea in f:
                fila = json.loads(linea)
                if fila.get("tarea") == "borrador" and fila.get("ok"):
                    tokens["entrada"] += fila.get("tokens_entrada") or 0
                    tokens["salida"] += fila.get("tokens_salida") or 0
    if "borradores" in salida:
        salida["borradores"]["tokens_registrados"] = tokens
    return salida


# --- Reporte --------------------------------------------------------------------------


def generar_reporte(data_dir: Path, carpeta_eval: Path | None = None) -> dict[str, Any]:
    from faro_editorial.interfaz import cargar_bandeja

    data_dir = Path(data_dir)
    processed = data_dir / "processed"
    carpeta_eval = Path(carpeta_eval or data_dir / "evaluacion")
    borradores = leer_borradores(processed)
    try:
        bandeja = cargar_bandeja(processed)[0]
    except Exception:  # sin base cargada: Precision@5 queda pendiente
        bandeja = None
    resultados = carpeta_eval / NOMBRE_RESULTADOS_CONSULTAS
    reporte = {
        "generado_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "referencia_snapshot": bandeja.get("referencia_panama") if bandeja else None,
        "cobertura_citas": metrica_cobertura(borradores),
        "validez_sustento": metrica_sustento(carpeta_eval / NOMBRE_PARES, borradores),
        "abstencion": metrica_abstencion(resultados),
        "clasificacion": metrica_clasificacion(carpeta_eval / NOMBRE_EVALUACION_TEMA),
        "agrupacion": metrica_agrupacion(carpeta_eval / NOMBRE_EVALUACION_AGRUPACION),
        "precision_5": metrica_precision5(bandeja, carpeta_eval / NOMBRE_SELECCION),
        "eficiencia": metrica_eficiencia(
            resultados, borradores, data_dir / "cache" / NOMBRE_REGISTRO
        ),
        "ahorro_tiempo": metrica_ahorro_tiempo(carpeta_eval / NOMBRE_AHORRO),
    }
    return _con_intervalos(reporte)


def _fraccion(m: dict[str, Any]) -> str:
    valor = f"{m['valor'] * 100:.1f} %" if m.get("valor") is not None else "—"
    return f"{m['numerador']}/{m['denominador']} ({valor})"


def _ic(m: dict[str, Any]) -> str:
    ic = m.get("ic95") or intervalo_wilson(m["numerador"], m["denominador"])
    return f"{ic[0] * 100:.1f}–{ic[1] * 100:.1f} %" if ic else "—"


def _cumple(m: dict[str, Any]) -> str:
    return "sí" if m.get("cumple") else "no"


def reporte_markdown(r: dict[str, Any]) -> str:
    lineas = [
        "# Métricas de la ejecución final",
        "",
        f"Generado el {r['generado_utc']} con `uv run python -m faro_editorial.metricas "
        f"reporte` · snapshot con corte {r['referencia_snapshot']} (hora de Panamá).",
        "",
        "Metas orientativas de la sección 9.1 del reto, no resultados. Cada métrica muestra "
        "numerador, denominador y fallos. Una métrica pendiente no tiene número: dice qué falta.",
        "",
        "| Métrica | Resultado | IC 95 % (Wilson) | Meta | Cumple |",
        "|---|---|---|---|---|",
    ]
    filas = [
        ("Cobertura de citas", "cobertura_citas", "100 %"),
        ("Validez de sustento (revisión humana)", "validez_sustento", "≥ 90 % con ≥ 30 pares"),
        ("Abstención en consultas sin respuesta", "abstencion", "≥ 80 %"),
    ]
    for nombre, clave, meta in filas:
        m = r[clave]
        if m["estado"] == "pendiente":
            lineas.append(f"| {nombre} | pendiente | — | {meta} | — |")
        else:
            lineas.append(f"| {nombre} | {_fraccion(m)} | {_ic(m)} | {meta} | {_cumple(m)} |")
    p5 = r["precision_5"]
    if p5["estado"] == "medida":
        lineas.append(f"| Precision@5 (exploratoria) | {_fraccion(p5)} | {_ic(p5)} | — | — |")
    else:
        lineas.append("| Precision@5 (exploratoria) | pendiente | — | — | — |")
    c = r["clasificacion"]
    if c["estado"] == "medida":
        for nombre, v in c["variantes"].items():
            lineas.append(
                f"| Macro-F1 clasificación · {nombre} | {v['macro_f1']:.3f} "
                f"({v['aciertos']}/{v['n']} aciertos) | "
                f"{_ic({'numerador': v['aciertos'], 'denominador': v['n']})} (aciertos) | — | — |"
            )
    else:
        lineas.append("| Macro-F1 clasificación | pendiente | — | — | — |")
    ag = r["agrupacion"]
    if ag["estado"] == "medida":
        for nombre, v in ag["metodos"].items():
            lineas.append(
                f"| Agrupación · {nombre} | F1 {v['f1']:.3f} · precisión "
                f"{_fraccion(v['precision'])} · recall {_fraccion(v['recall'])} | "
                f"P {_ic(v['precision'])} · R {_ic(v['recall'])} | — | — |"
            )
    else:
        lineas.append("| Agrupación (precisión y recall) | pendiente | — | — | — |")
    e = r["eficiencia"].get("consultas", {})
    if e.get("estado") == "pendiente":
        lineas.append("| Mediana por consulta | pendiente | — | ≤ 15 s | — |")
    else:
        lineas.append(
            f"| Mediana por consulta (p95) | {e['mediana_s']} s ({e['p95_s']} s) | — | ≤ 15 s | "
            f"{'sí' if e['cumple_meta'] else 'no'} |"
        )
    lineas.append("")

    secciones = [
        ("Cobertura de citas", "cobertura_citas", "fallos"),
        ("Validez de sustento", "validez_sustento", "no_respaldadas"),
        ("Abstención", "abstencion", None),
        ("Precision@5", "precision_5", None),
        ("Clasificación", "clasificacion", None),
        ("Agrupación", "agrupacion", None),
        ("Ahorro de tiempo", "ahorro_tiempo", None),
    ]
    for titulo, clave, lista in secciones:
        m = r[clave]
        lineas += [f"## {titulo}", ""]
        if m["estado"] == "pendiente":
            lineas += [f"Pendiente: {m['motivo']} Comando: `{m['comando']}`", ""]
            continue
        lineas += [f"Método: {m['metodo']}", ""]
        if clave == "cobertura_citas":
            lineas.append(
                f"- {m['borradores']} borradores ({m['abstenciones']} abstenciones), "
                f"{m['rechazadas']} afirmaciones rechazadas."
            )
        if clave == "validez_sustento":
            a = m["acuerdo_jev"]
            lineas += [
                f"- Etiquetado por: {', '.join(m['etiquetadores']) or 'sin completar'} · "
                f"filas sin etiquetar: {m['sin_etiquetar']}.",
                f"- Pares suficientes (≥ {m['minimo_pares']}): "
                f"{'sí' if m['suficientes_pares'] else 'no'}.",
                f"- Acuerdo de Jev con la persona: {_fraccion(a)}. {a['metodo']}",
            ]
            lineas += [f"  - Desacuerdo: {d}" for d in a["desacuerdos"]]
        if clave == "abstencion":
            ai = m["abstenciones_incorrectas"]
            rc = m["respondidas_con_evidencia_esperada"]
            df = m["dificiles_rechazadas"]
            lineas += [
                f"- Conjunto {m['conjunto']}, revisado por: {m['revisado_por']} · con Jev: "
                f"{'sí' if m['con_jev'] else 'no (solo umbral)'}.",
                f"- No respondibles difíciles rechazadas: {df['numerador']}/{df['denominador']}.",
                f"- No respondibles que se respondieron: "
                f"{', '.join(m['no_respondibles_respondidas']) or 'ninguna'}.",
                f"- Abstenciones incorrectas en respondibles: {_fraccion(ai)}: "
                f"{', '.join(ai['ids']) or 'ninguna'}.",
                f"- Respondibles con la evidencia esperada citada: {_fraccion(rc)}; fallos: "
                f"{', '.join(rc['fallos']) or 'ninguno'}.",
            ]
        if clave == "precision_5":
            lineas += [
                f"- Evaluador: {', '.join(m['evaluadores'])} · candidatos: {m['candidatos']}.",
                f"- Coinciden: {', '.join(m['coinciden']) or 'ninguno'} · solo el sistema: "
                f"{', '.join(m['solo_sistema']) or 'ninguno'} · solo la persona: "
                f"{', '.join(m['solo_persona']) or 'ninguno'}.",
            ]
        if clave == "clasificacion":
            lineas.append(
                f"- {m['n_etiquetas']} titulares etiquetados; método en {m['metodo_etiquetado']}."
            )
        if clave == "agrupacion":
            lineas.append(
                f"- {m['pares']} pares etiquetados por {', '.join(m['etiquetadores']) or '—'}, "
                f"{m['mismo_evento']} del mismo evento · estratos: "
                + ", ".join(f"{k} {v}" for k, v in m["estratos"].items())
                + "."
            )
            for nombre, v in m["metodos"].items():
                lineas.append(
                    f"- {nombre}: TP {v['tp']} · FP {v['fp']} · FN {v['fn']} · TN {v['tn']}; "
                    f"errores: {', '.join(v['errores']) or 'ninguno'}."
                )
        if clave == "ahorro_tiempo":
            lineas.append(
                f"- A mano: mediana {m['mediana_manual_min']} min (n = {m['n_manual']}) · con "
                f"Faro: {m['mediana_asistido_min']} min (n = {m['n_asistido']}) · ahorro "
                f"{m['ahorro'] * 100:.0f} % · personas: {', '.join(m['personas']) or '—'} · "
                f"tareas incompletas: {m['incompletas']}. Con tan pocas pruebas es un dato "
                "orientativo."
            )
        if lista and m.get(lista):
            lineas += [f"- Fallo: {x}" for x in m[lista]]
        lineas.append("")

    ef = r["eficiencia"]
    lineas += ["## Eficiencia", ""]
    co = ef["consultas"]
    if co.get("estado") == "pendiente":
        lineas.append(f"- Consultas: pendiente. Comando: `{co['comando']}`")
    else:
        lineas.append(
            f"- Consultas: {co['n']}, mediana {co['mediana_s']} s, p95 {co['p95_s']} s · "
            f"llamadas a Jev en vivo: {co['llamadas_en_vivo']} · costo USD "
            f"{co['costo_usd_en_vivo']} ({co['costo_usd_por_consulta']} por consulta) · tokens "
            f"{co['tokens_entrada']} de entrada y {co['tokens_salida']} de salida."
            + (
                f" El índice semántico se prepara una vez al iniciar "
                f"({co['preparacion_indice_s']} s), fuera de la medición por consulta."
                if co.get("preparacion_indice_s")
                else ""
            )
        )
    if "borradores" in ef:
        b = ef["borradores"]
        lineas.append(
            f"- Borradores: {b['n']}, mediana {b['mediana_s']} s, p95 {b['p95_s']} s, "
            f"{b['intentos_promedio']} intentos en promedio · costo USD {b['costo_usd_llm']} "
            f"(LLM) + {b['costo_usd_jev']} (Jev) · tokens registrados "
            f"{b['tokens_registrados']['entrada']} / {b['tokens_registrados']['salida']}. "
            f"{b['nota']}"
        )
    lineas.append("")
    return "\n".join(lineas)


def escribir_reporte(r: dict[str, Any], carpeta: Path = CARPETA_SALIDA) -> dict[str, Path]:
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas = {"json": carpeta / f"{NOMBRE_METRICAS}.json", "md": carpeta / f"{NOMBRE_METRICAS}.md"}
    rutas["json"].write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rutas["md"].write_text(reporte_markdown(r), encoding="utf-8")
    return rutas


# --- Línea de comandos ----------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    from faro_editorial.settings import get_settings

    parser = argparse.ArgumentParser(description="Métricas de la sección 9.1 (#19).")
    parser.add_argument("accion", choices=["pares", "seleccion", "consultas", "reporte"])
    parser.add_argument("--forzar", action="store_true", help="regenera un CSV existente")
    parser.add_argument("--n", type=int, default=15, help="candidatos para la selección")
    args = parser.parse_args(argv)

    s = get_settings()
    carpeta_eval = s.data_dir / "evaluacion"

    if args.accion == "pares":
        try:
            ruta, n = pares_para_etiquetar(
                s.processed_dir, carpeta_eval / NOMBRE_PARES, forzar=args.forzar
            )
        except FileExistsError as e:
            raise SystemExit(str(e)) from None
        print(f"{n} pares afirmación-evidencia para etiquetar: {ruta}")
        print(
            "Completen sustento_humano con 'respaldada' o 'no_respaldada' según si la "
            "evidencia citada dice lo mismo que la afirmación, y su nombre en 'etiquetador'. "
            "Sin consultar a la IA."
        )
        if n < MIN_PARES_SUSTENTO:
            print(
                f"Aviso: el reto pide al menos {MIN_PARES_SUSTENTO} pares si se producen tantos. "
                "Generen más borradores (borradores --top 9) y regeneren con --forzar."
            )
        return

    if args.accion == "seleccion":
        from faro_editorial.interfaz import cargar_bandeja

        try:
            ruta = seleccion_para_editor(
                cargar_bandeja(s.processed_dir)[0],
                carpeta_eval / NOMBRE_SELECCION,
                n=args.n,
                forzar=args.forzar,
            )
        except FileExistsError as e:
            raise SystemExit(str(e)) from None
        print(f"{args.n} temas en orden aleatorio, sin puntaje: {ruta}")
        print(
            "Una persona que no haya visto la bandeja marca con x los 5 que revisaría primero "
            "y escribe su nombre en 'evaluador'."
        )
        return

    if args.accion == "consultas":
        from faro_editorial.busqueda import Buscador, leer_corpus
        from faro_editorial.busqueda import load_config as load_config_busqueda
        from faro_editorial.proveedores import crear_cliente

        conjunto = load_consultas()
        buscador = Buscador(leer_corpus(s.processed_dir), load_config_busqueda())
        # El índice se prepara una vez, fuera de la medición por consulta (igual que en la
        # interfaz, que mantiene el buscador en memoria); su tiempo se reporta aparte.
        preparacion_s = buscador.preparar()
        # Igual que la interfaz: con OFFLINE=1 Jev responde desde la caché y, si un candidato
        # no tiene respuesta guardada, decide la compuerta de palabras clave.
        cliente = crear_cliente("jev", s)
        datos = ejecutar_consultas(buscador, cliente, conjunto, s.cache_dir / NOMBRE_REGISTRO)
        datos["preparacion_indice_s"] = round(preparacion_s, 3)
        if buscador.motivo_respaldo:
            datos["nota_representador"] = buscador.motivo_respaldo
        carpeta_eval.mkdir(parents=True, exist_ok=True)
        ruta = carpeta_eval / NOMBRE_RESULTADOS_CONSULTAS
        ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for r in datos["resultados"]:
            marca = "ABSTENCIÓN" if r["abstencion"] else f"{len(r['ids_citados'])} cita(s)"
            esperado = "debía abstenerse" if r["etiqueta"] == "no_respondible" else "respondible"
            print(f"{r['id']}  {marca:<12} ({esperado}) · {r['latencia_s']:.2f} s")
        if buscador.motivo_respaldo:
            print(f"Aviso: {buscador.motivo_respaldo}")
        if s.offline:
            print(
                "Aviso: OFFLINE=1, Jev respondió solo desde la caché. Para medir la compuerta "
                'completa en vivo (umbral + Noul), corre con $env:OFFLINE="0".'
            )
        print(f"Resultados: {ruta} · costo en vivo USD {datos['costo_usd_en_vivo']}")
        return

    reporte = generar_reporte(s.data_dir, carpeta_eval)
    rutas = escribir_reporte(reporte)
    print(rutas["md"].read_text(encoding="utf-8"))
    print(f"Reporte: {rutas['md']} y {rutas['json']}")


if __name__ == "__main__":
    main()
