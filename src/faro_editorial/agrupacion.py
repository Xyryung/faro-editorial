"""Etapa 2 · Organizar: agrupa las noticias del mismo evento (issue #9, T02, CU-03).

1. Cada titular se convierte en un vector: embeddings locales multilingual-e5-small (CPU, sin
   internet una vez descargado el modelo) o, si no están disponibles, TF-IDF de caracteres.
2. Se comparan solo titulares cercanos en el tiempo (ventana_horas) y se unen los pares con
   similitud coseno alta, de mayor a menor similitud.
3. Para unir dos grupos ya formados, la similitud promedio entre todos sus titulares también
   debe ser alta: así no se encadenan temas distintos. Un grupo no crece más allá de
   max_tamano ni abarca más de max_horas_grupo.

Cada grupo conserva los IDs de todas sus noticias: no se pierde ninguna fuente. La agrupación
no decide importancia ni corroboración; eso lo hace el puntaje, que cuenta procedencias
independientes (una agencia replicada en varios medios es una sola procedencia, #47).

Salidas en data/processed/:
- grupos.jsonl     una línea por grupo de 2 o más notas; la lee la bandeja
- agrupacion.json  método, umbrales, conteos y los grupos más grandes, para revisar

Uso:  uv run python -m faro_editorial.agrupacion [--metodo auto|e5|tfidf] [--umbral 0.9]
"""

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
import yaml
from pydantic import BaseModel

from faro_editorial.contexto import normalizar
from faro_editorial.contrato import Noticia
from faro_editorial.settings import ROOT_DIR

RUTA_CONFIG = ROOT_DIR / "config" / "agrupacion_v1.yaml"
NOMBRE_GRUPOS = "grupos.jsonl"
NOMBRE_RESUMEN = "agrupacion.json"
LOTE = 512

Metodo = Literal["auto", "e5", "tfidf"]


class ConfigAgrupacion(BaseModel):
    version: str
    metodo: Metodo = "auto"
    umbral: dict[Literal["e5", "tfidf"], float]
    margen_promedio: float = 0.05
    ventana_horas: float = 72
    max_horas_grupo: float = 168
    max_tamano: int = 40


def load_config(path: Path = RUTA_CONFIG) -> ConfigAgrupacion:
    with Path(path).open(encoding="utf-8") as f:
        return ConfigAgrupacion.model_validate(yaml.safe_load(f))


# --- Representación de los titulares --------------------------------------------------


class Representador(Protocol):
    metodo: Literal["e5", "tfidf"]
    nombre: str

    def vectores(self, textos: list[str]) -> Any:
        """Una fila por texto, con norma 1: el producto punto es la similitud coseno."""
        ...


class RepresentadorTfidf:
    """N-gramas de 3 a 5 caracteres, sin tildes ni mayúsculas. Sin modelo ni internet;
    tolera variaciones de redacción ("Mulino veta" / "Mulino vetó")."""

    metodo: Literal["tfidf"] = "tfidf"
    nombre = "tfidf-char_wb-3-5"

    def vectores(self, textos: list[str]) -> Any:
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectorizador = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, preprocessor=normalizar
        )
        return vectorizador.fit_transform(textos).tocsr()  # filas con norma L2 = 1


class RepresentadorE5:
    """multilingual-e5-small en CPU. Para tareas simétricas (comparar titulares entre sí) el
    modelo pide el prefijo "query: " en todos los textos."""

    metodo: Literal["e5"] = "e5"

    def __init__(self, modelo: str) -> None:
        self.nombre = modelo
        from sentence_transformers import SentenceTransformer  # importación diferida

        self._modelo = SentenceTransformer(modelo, device="cpu")

    def vectores(self, textos: list[str]) -> np.ndarray:
        vectores = self._modelo.encode(
            [f"query: {t}" for t in textos],
            batch_size=64,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=len(textos) > 500,
        )
        return np.asarray(vectores, dtype=np.float32)


def crear_representador(metodo: Metodo, modelo_e5: str) -> tuple[Representador, str | None]:
    """Devuelve el representador y, si hubo que caer a TF-IDF, el motivo."""
    if metodo == "tfidf":
        return RepresentadorTfidf(), None
    try:
        return RepresentadorE5(modelo_e5), None
    except Exception as e:  # sin torch, sin modelo descargado y sin internet, etc.
        if metodo == "e5":
            raise
        motivo = (
            f"No se pudo cargar {modelo_e5} ({type(e).__name__}: {str(e)[:160]}); se usa TF-IDF."
        )
        return RepresentadorTfidf(), motivo


# --- Agrupación -----------------------------------------------------------------------


def fecha_de(noticia: Noticia) -> datetime | None:
    # Misma regla que la carga: manda la publicación; si falta (GDELT), la detección.
    return noticia.fecha_publicacion or noticia.fecha_deteccion


def _disperso(matriz: Any) -> bool:
    """TF-IDF devuelve una matriz dispersa de scipy (dependencia de scikit-learn)."""
    return hasattr(matriz, "toarray")


def _fila(matriz: Any, i: int) -> Any:
    return matriz[i] if _disperso(matriz) else matriz[i : i + 1]


def _punto(a: Any, b: Any) -> float:
    if _disperso(a):
        return float(a.multiply(b).sum())
    return float((a @ b.T).item())


@dataclass
class Grupo:
    miembros: list[int]
    suma: Any  # suma de los vectores (fila densa o dispersa)
    primera: datetime | None
    ultima: datetime | None


def _horas(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 3600


def agrupar(
    noticias: list[Noticia],
    vectores: Any,
    umbral: float,
    config: ConfigAgrupacion,
) -> tuple[list[list[int]], dict[str, int]]:
    """Devuelve los grupos (índices de `noticias`) y estadísticas de la ejecución."""
    n = len(noticias)
    fechas = [fecha_de(x) for x in noticias]
    segundos = np.array([f.timestamp() if f else np.nan for f in fechas], dtype=np.float64)
    ventana_s = config.ventana_horas * 3600

    # 1. Pares candidatos: similitud alta y fechas cercanas (o alguna sin fecha).
    pares: list[tuple[float, int, int]] = []
    for inicio in range(0, n, LOTE):
        fin = min(inicio + LOTE, n)
        bloque = vectores[inicio:fin] @ vectores.T
        bloque = bloque.toarray() if _disperso(bloque) else np.asarray(bloque)
        filas, columnas = np.nonzero(bloque >= umbral)
        for f, j in zip(filas.tolist(), columnas.tolist(), strict=True):
            i = inicio + f
            if j <= i:
                continue
            ti, tj = segundos[i], segundos[j]
            if not (np.isnan(ti) or np.isnan(tj)) and abs(ti - tj) > ventana_s:
                continue
            pares.append((float(bloque[f, j]), i, j))
    pares.sort(key=lambda p: (-p[0], p[1], p[2]))  # determinista

    # 2. Unión de mayor a menor similitud, con chequeos contra el encadenamiento.
    grupo_de = list(range(n))
    grupos: dict[int, Grupo] = {
        i: Grupo([i], _fila(vectores, i), fechas[i], fechas[i]) for i in range(n)
    }
    stats = Counter(pares_candidatos=len(pares))
    umbral_promedio = umbral - config.margen_promedio
    for _, i, j in pares:
        a, b = grupo_de[i], grupo_de[j]
        if a == b:
            continue
        ga, gb = grupos[a], grupos[b]
        if len(ga.miembros) + len(gb.miembros) > config.max_tamano:
            stats["rechazados_por_tamano"] += 1
            continue
        primeras = [f for f in (ga.primera, gb.primera) if f]
        ultimas = [f for f in (ga.ultima, gb.ultima) if f]
        if primeras and ultimas and _horas(min(primeras), max(ultimas)) > config.max_horas_grupo:
            stats["rechazados_por_duracion"] += 1
            continue
        promedio = _punto(ga.suma, gb.suma) / (len(ga.miembros) * len(gb.miembros))
        if promedio < umbral_promedio:
            stats["rechazados_por_promedio"] += 1
            continue
        # Unir el más chico dentro del más grande.
        if len(ga.miembros) < len(gb.miembros):
            a, b, ga, gb = b, a, gb, ga
        ga.miembros.extend(gb.miembros)
        ga.suma = ga.suma + gb.suma
        ga.primera = min(primeras) if primeras else None
        ga.ultima = max(ultimas) if ultimas else None
        for m in gb.miembros:
            grupo_de[m] = a
        del grupos[b]
        stats["uniones"] += 1

    resultado = [
        sorted(g.miembros, key=lambda m: (segundos[m] if not np.isnan(segundos[m]) else 0, m))
        for g in grupos.values()
    ]
    resultado.sort(key=lambda g: (-len(g), noticias[g[0]].id_noticia))
    return resultado, dict(stats)


def id_grupo(ids_noticias: Sequence[str]) -> str:
    """Estable: el mismo conjunto de noticias da el mismo ID en cada ejecución."""
    clave = "|".join(sorted(ids_noticias))
    return "g-" + hashlib.sha256(clave.encode("utf-8")).hexdigest()[:12]


# --- Ejecución completa ---------------------------------------------------------------


def ejecutar_agrupacion(
    processed_dir: Path,
    config: ConfigAgrupacion | None = None,
    representador: Representador | None = None,
    umbral: float | None = None,
    metodo: Metodo | None = None,
) -> dict[str, Any]:
    """Lee la base de la carga, agrupa y escribe grupos.jsonl y agrupacion.json."""
    from faro_editorial.bandeja import leer_noticias
    from faro_editorial.carga import NOMBRE_DB
    from faro_editorial.settings import get_settings

    processed_dir = Path(processed_dir)
    config = config or load_config()
    aviso = None
    if representador is None:
        modelo = get_settings().embedding_model
        representador, aviso = crear_representador(metodo or config.metodo, modelo)
    umbral = umbral if umbral is not None else config.umbral[representador.metodo]

    noticias = leer_noticias(processed_dir / NOMBRE_DB)
    grupos: list[list[int]] = []
    stats: dict[str, int] = {}
    if noticias:
        vectores = representador.vectores([x.titulo for x in noticias])
        grupos, stats = agrupar(noticias, vectores, umbral, config)

    multiples = [g for g in grupos if len(g) > 1]
    lineas = []
    for g in multiples:
        ids = [noticias[i].id_noticia for i in g]
        lineas.append(json.dumps({"id_grupo": id_grupo(ids), "ids_noticias": ids}))
    (processed_dir / NOMBRE_GRUPOS).write_text(
        "\n".join(lineas) + ("\n" if lineas else ""), encoding="utf-8"
    )

    def detalle(g: list[int]) -> dict[str, Any]:
        primera = fecha_de(noticias[g[0]])
        return {
            "id_grupo": id_grupo([noticias[i].id_noticia for i in g]),
            "noticias": len(g),
            "medios": sorted({noticias[i].medio for i in g}),
            "desde_utc": primera.astimezone(UTC).isoformat() if primera else None,
            "titulares": [noticias[i].titulo for i in g[:5]],
        }

    tamanos = Counter(len(g) for g in multiples)
    resumen = {
        "version": config.version,
        "generado_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "metodo": representador.metodo,
        "modelo": representador.nombre,
        "aviso": aviso,
        "umbral": umbral,
        "margen_promedio": config.margen_promedio,
        "ventana_horas": config.ventana_horas,
        "max_horas_grupo": config.max_horas_grupo,
        "max_tamano": config.max_tamano,
        "noticias": len(noticias),
        "grupos_total": len(grupos),
        "grupos_con_2_o_mas": len(multiples),
        "noticias_agrupadas": sum(len(g) for g in multiples),
        "grupos_con_varios_medios": sum(len({noticias[i].medio for i in g}) > 1 for g in multiples),
        "tamanos": {str(k): v for k, v in sorted(tamanos.items())},
        "estadisticas": stats,
        "mayores": [detalle(g) for g in multiples[:20]],
    }
    (processed_dir / NOMBRE_RESUMEN).write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return resumen


def main(argv: list[str] | None = None) -> None:
    from faro_editorial.carga import NOMBRE_DB
    from faro_editorial.settings import get_settings

    parser = argparse.ArgumentParser(description="Agrupa las noticias del mismo evento.")
    parser.add_argument("--metodo", choices=["auto", "e5", "tfidf"], help="anula la config")
    parser.add_argument("--umbral", type=float, help="similitud mínima (anula la config)")
    parser.add_argument("--muestra", type=int, default=8, help="grupos a mostrar en consola")
    args = parser.parse_args(argv)

    s = get_settings()
    if not (s.processed_dir / NOMBRE_DB).exists():
        raise SystemExit(
            "No hay base cargada: ejecuta antes  uv run python -m faro_editorial.carga"
        )
    r = ejecutar_agrupacion(s.processed_dir, umbral=args.umbral, metodo=args.metodo)
    if r["aviso"]:
        print(f"Aviso: {r['aviso']}")
    print(f"Método: {r['metodo']} ({r['modelo']}) · umbral {r['umbral']} · {r['version']}")
    print(
        f"{r['noticias']} noticias → {r['grupos_total']} grupos; {r['grupos_con_2_o_mas']} "
        f"con 2 o más notas ({r['noticias_agrupadas']} noticias), "
        f"{r['grupos_con_varios_medios']} con más de un medio"
    )
    print(f"Tamaños: {r['tamanos']} · {r['estadisticas']}")
    for g in r["mayores"][: args.muestra]:
        print(f"\n  [{g['noticias']} notas · {', '.join(g['medios'])}] {g['desde_utc'] or ''}")
        for titular in g["titulares"]:
            print(f"     - {titular[:110]}")
    print(f"\nGrupos para la bandeja: {s.processed_dir / NOMBRE_GRUPOS}")
    print(f"Resumen: {s.processed_dir / NOMBRE_RESUMEN}")


if __name__ == "__main__":
    main()
