"""Evaluación de la agrupación contra etiquetas humanas (sección 9.1 del reto).

Compara la agrupación del snapshot (grupos.jsonl, hecha con el método de agrupacion.json,
normalmente embeddings e5) con la línea base TF-IDF de caracteres, sobre pares de titulares
que una persona etiqueta a ciegas como "mismo evento" o no.

1. `pares` arma una muestra estratificada de pares, porque al azar casi todos serían de eventos
   distintos y no medirían nada:
   - pares que el método del snapshot agrupó,
   - pares que solo TF-IDF agrupó,
   - pares parecidos (cerca del umbral de TF-IDF, dentro de la ventana) que ninguno agrupó,
     para encontrar lo que ambos métodos se pierden.
   El CSV va en orden aleatorio y sin la predicción de ningún método; las predicciones quedan
   aparte, en un JSON que la persona no necesita abrir.
2. `evaluar` cruza las etiquetas con las predicciones y reporta, para cada método, verdaderos y
   falsos positivos, falsos negativos, precisión, recall y F1, con intervalo de Wilson al 95 %.

La muestra es diagnóstica, no poblacional: el recall se mide sobre los pares "mismo evento" de
la muestra, no sobre todos los del snapshot.

Uso (con DATA_DIR apuntando al snapshot):
    uv run python -m faro_editorial.evaluacion_agrupacion pares     # CSV para etiquetar
    uv run python -m faro_editorial.evaluacion_agrupacion evaluar   # tras completar el CSV
"""

import argparse
import csv
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from faro_editorial.agrupacion import (
    NOMBRE_GRUPOS,
    RepresentadorTfidf,
    agrupar,
    fecha_de,
    load_config,
)
from faro_editorial.metricas import SI, intervalo_wilson

NOMBRE_CSV = "pares_agrupacion.csv"
NOMBRE_PREDICCIONES = "pares_agrupacion.predicciones.json"
NOMBRE_RESULTADO = "evaluacion_agrupacion.json"
COLUMNAS = [
    "id_par",
    "titulo_a",
    "medio_a",
    "fecha_a",
    "titulo_b",
    "medio_b",
    "fecha_b",
    "mismo_evento",
    "etiquetador",
    "comentario",
]
NO = {"no", "n", "0", "false"}
# Similitud TF-IDF mínima para que un par no agrupado cuente como "parecido".
PISO_PARECIDO = 0.35


def _grupo_por_noticia(grupos: list[list[str]]) -> dict[str, int]:
    return {id_: k for k, ids in enumerate(grupos) for id_ in ids}


def _pares_de_grupos(grupos: list[list[str]], azar: random.Random) -> list[tuple[str, str]]:
    """Un par al azar de cada grupo de 2 o más noticias."""
    return [tuple(sorted(azar.sample(ids, 2))) for ids in grupos if len(ids) >= 2]


def pares_para_etiquetar(
    processed_dir: Path,
    carpeta_eval: Path,
    n: int = 40,
    semilla: int = 7,
    forzar: bool = False,
) -> tuple[Path, int]:
    from faro_editorial.bandeja import leer_noticias
    from faro_editorial.carga import NOMBRE_DB

    processed_dir, carpeta_eval = Path(processed_dir), Path(carpeta_eval)
    ruta_csv = carpeta_eval / NOMBRE_CSV
    if ruta_csv.exists() and not forzar:
        raise FileExistsError(f"{ruta_csv} ya existe: usa --forzar para regenerarlo.")

    noticias = leer_noticias(processed_dir / NOMBRE_DB)
    por_id = {x.id_noticia: x for x in noticias}
    indice = {x.id_noticia: i for i, x in enumerate(noticias)}
    config = load_config()
    info = json.loads((processed_dir / "agrupacion.json").read_text(encoding="utf-8"))
    metodo_snapshot = info.get("metodo", "snapshot")

    with (processed_dir / NOMBRE_GRUPOS).open(encoding="utf-8") as f:
        grupos_snapshot = [json.loads(linea)["ids_noticias"] for linea in f if linea.strip()]
    vectores = RepresentadorTfidf().vectores([x.titulo for x in noticias])
    indices_tfidf, _ = agrupar(noticias, vectores, config.umbral["tfidf"], config)
    grupos_tfidf = [[noticias[i].id_noticia for i in g] for g in indices_tfidf if len(g) > 1]
    de_snapshot = _grupo_por_noticia(grupos_snapshot)
    de_tfidf = _grupo_por_noticia(grupos_tfidf)

    def mismo(grupo_de: dict[str, int], a: str, b: str) -> bool:
        return a in grupo_de and grupo_de.get(a) == grupo_de.get(b)

    azar = random.Random(semilla)
    cuota = n // 3
    estratos: dict[str, list[tuple[str, str]]] = {
        "agrupado_snapshot": _pares_de_grupos(grupos_snapshot, azar),
        "solo_tfidf": [
            p for p in _pares_de_grupos(grupos_tfidf, azar) if not mismo(de_snapshot, *p)
        ],
        "parecido_no_agrupado": [],
    }
    # Pares parecidos que ninguno agrupó: vecino TF-IDF más cercano dentro de la ventana.
    ventana_s = config.ventana_horas * 3600
    orden = list(range(len(noticias)))
    azar.shuffle(orden)
    for i in orden:
        if len(estratos["parecido_no_agrupado"]) >= n:
            break
        sims = np.asarray((vectores[i] @ vectores.T).toarray()).ravel()
        sims[i] = 0
        fi = fecha_de(noticias[i])
        for j in np.argsort(-sims)[:5].tolist():
            if not PISO_PARECIDO <= sims[j] < config.umbral["tfidf"]:
                continue
            fj = fecha_de(noticias[j])
            if fi and fj and abs((fi - fj).total_seconds()) > ventana_s:
                continue
            a, b = sorted((noticias[i].id_noticia, noticias[j].id_noticia))
            if not mismo(de_snapshot, a, b) and not mismo(de_tfidf, a, b):
                estratos["parecido_no_agrupado"].append((a, b))
                break

    elegidos: dict[tuple[str, str], str] = {}
    for nombre in ("agrupado_snapshot", "solo_tfidf", "parecido_no_agrupado"):
        candidatos = [p for p in estratos[nombre] if p not in elegidos]
        for p in azar.sample(candidatos, min(cuota, len(candidatos))):
            elegidos[p] = nombre
    # Si un estrato quedó corto, se completa con los demás.
    sobrantes = [(p, e) for e, lista in estratos.items() for p in lista if p not in elegidos]
    azar.shuffle(sobrantes)
    for p, e in sobrantes[: max(0, n - len(elegidos))]:
        elegidos[p] = e

    pares = list(elegidos.items())
    azar.shuffle(pares)  # orden aleatorio: el estrato no se adivina por la posición
    carpeta_eval.mkdir(parents=True, exist_ok=True)
    predicciones: dict[str, Any] = {
        "generado_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "metodo_snapshot": metodo_snapshot,
        "umbral_tfidf": config.umbral["tfidf"],
        "semilla": semilla,
        "pares": {},
    }
    with ruta_csv.open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS)
        escritor.writeheader()
        for k, ((a, b), estrato) in enumerate(pares, start=1):
            id_par = f"P{k:02d}"
            na, nb = por_id[a], por_id[b]
            escritor.writerow(
                {
                    "id_par": id_par,
                    "titulo_a": na.titulo,
                    "medio_a": na.medio,
                    "fecha_a": str(fecha_de(na) or "")[:16],
                    "titulo_b": nb.titulo,
                    "medio_b": nb.medio,
                    "fecha_b": str(fecha_de(nb) or "")[:16],
                }
            )
            sim = float((vectores[indice[a]] @ vectores[indice[b]].T).toarray()[0, 0])
            predicciones["pares"][id_par] = {
                "ids": [a, b],
                "estrato": estrato,
                "snapshot": mismo(de_snapshot, a, b),
                "tfidf": mismo(de_tfidf, a, b),
                "similitud_tfidf": round(sim, 3),
            }
    (carpeta_eval / NOMBRE_PREDICCIONES).write_text(
        json.dumps(predicciones, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return ruta_csv, len(pares)


def _puntajes(tp: int, fp: int, fn: int, tn: int) -> dict[str, Any]:
    def fraccion(num: int, den: int) -> dict[str, Any]:
        return {
            "numerador": num,
            "denominador": den,
            "valor": round(num / den, 4) if den else None,
            "ic95": intervalo_wilson(num, den),
        }

    precision, recall = fraccion(tp, tp + fp), fraccion(tp, tp + fn)
    p, r = precision["valor"], recall["valor"]
    f1 = round(2 * p * r / (p + r), 4) if p and r else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def evaluar(carpeta_eval: Path) -> dict[str, Any]:
    """Cruza las etiquetas del CSV con las predicciones guardadas al armar la muestra."""
    carpeta_eval = Path(carpeta_eval)
    predicciones = json.loads((carpeta_eval / NOMBRE_PREDICCIONES).read_text(encoding="utf-8"))
    with (carpeta_eval / NOMBRE_CSV).open(encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))

    etiquetas: dict[str, bool] = {}
    sin_etiquetar, etiquetadores = [], set()
    for fila in filas:
        valor = (fila.get("mismo_evento") or "").strip().lower()
        if valor in SI:
            etiquetas[fila["id_par"]] = True
        elif valor in NO:
            etiquetas[fila["id_par"]] = False
        else:
            sin_etiquetar.append(fila["id_par"])
            continue
        if (fila.get("etiquetador") or "").strip():
            etiquetadores.add(fila["etiquetador"].strip())

    nombre_snapshot = predicciones["metodo_snapshot"]
    if nombre_snapshot == "tfidf":  # el snapshot ya se agrupó con la línea base
        nombre_snapshot = "snapshot-tfidf"
    metodos = {nombre_snapshot: "snapshot", "tfidf": "tfidf"}
    resultado: dict[str, Any] = {
        "generado_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "metodo": (
            "Pares de titulares etiquetados a ciegas como 'mismo evento' o no; precisión = "
            "pares agrupados que son el mismo evento / pares agrupados; recall = pares del "
            "mismo evento que el método agrupó / pares del mismo evento de la muestra. "
            "Muestra estratificada, no poblacional."
        ),
        "pares": len(filas),
        "etiquetados": len(etiquetas),
        "mismo_evento": sum(etiquetas.values()),
        "sin_etiquetar": sin_etiquetar,
        "etiquetadores": sorted(etiquetadores),
        "estratos": {},
        "metodos": {},
    }
    for p in predicciones["pares"].values():
        resultado["estratos"][p["estrato"]] = resultado["estratos"].get(p["estrato"], 0) + 1
    for nombre, clave in metodos.items():
        tp = fp = fn = tn = 0
        errores = []
        for id_par, real in etiquetas.items():
            predicho = predicciones["pares"][id_par][clave]
            tp += predicho and real
            fp += predicho and not real
            fn += real and not predicho
            tn += not real and not predicho
            if predicho != real:
                errores.append(id_par)
        resultado["metodos"][nombre] = _puntajes(tp, fp, fn, tn) | {"errores": errores}
    (carpeta_eval / NOMBRE_RESULTADO).write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return resultado


def main(argv: list[str] | None = None) -> None:
    from faro_editorial.settings import get_settings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("accion", choices=["pares", "evaluar"])
    parser.add_argument("--n", type=int, default=40, help="pares de la muestra")
    parser.add_argument("--forzar", action="store_true", help="regenera un CSV existente")
    args = parser.parse_args(argv)
    s = get_settings()
    carpeta_eval = s.data_dir / "evaluacion"

    if args.accion == "pares":
        try:
            ruta, n = pares_para_etiquetar(
                s.processed_dir, carpeta_eval, args.n, forzar=args.forzar
            )
        except FileExistsError as e:
            raise SystemExit(str(e)) from None
        print(f"{n} pares para etiquetar: {ruta}")
        print(
            "En 'mismo_evento' escriban 'si' si los dos titulares cuentan el mismo hecho "
            "(aunque con otras palabras) y 'no' si son hechos distintos, aunque sean del mismo "
            "tema. Su nombre en 'etiquetador'. Sin consultar a la IA."
        )
        return

    r = evaluar(carpeta_eval)
    print(
        f"{r['etiquetados']} de {r['pares']} pares etiquetados; {r['mismo_evento']} del mismo "
        "evento."
    )
    for metodo, m in r["metodos"].items():
        p, rc = m["precision"], m["recall"]
        print(
            f"{metodo:<6} precisión {p['numerador']}/{p['denominador']} · recall "
            f"{rc['numerador']}/{rc['denominador']} · F1 {m['f1']:.3f} "
            f"(TP {m['tp']} · FP {m['fp']} · FN {m['fn']} · TN {m['tn']})"
        )
    if r["sin_etiquetar"]:
        print(f"Aviso: {len(r['sin_etiquetar'])} pares sin etiquetar no cuentan.")


if __name__ == "__main__":
    main()
