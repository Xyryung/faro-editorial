"""Etapa 2 · Organizar: tema de cada grupo de noticias (issue #10) y su evaluación (#6, #7).

Temas: los de config/rules_v1.yaml (economia, logistica_canal, turismo, servicios_publicos,
eventos_naturales, regulacion, otro). El tema alimenta dos componentes del puntaje:
relevancia (tema editorial) e impacto (alcance típico del tema).

- Línea base: palabras clave por tema (config/clasificacion_v1.yaml). Sin IA.
- Jev Choice a través de la capa de decisiones (#8): caché, modo offline y abstención. Si Jev
  se abstiene (sin internet y sin caché, error o respuesta inválida), se usa la línea base y
  queda registrado qué método decidió cada tema.

Comandos (desde la raíz del repo, después de la carga y la agrupación):
    uv run python -m faro_editorial.clasificacion                 # clasifica y escribe grupos.jsonl
    uv run python -m faro_editorial.clasificacion muestra         # 60 titulares para etiquetar
    uv run python -m faro_editorial.clasificacion evaluar         # macro-F1 contra las etiquetas
"""

import argparse
import csv
import json
import random
import re
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from faro_editorial.contexto import normalizar
from faro_editorial.contrato import Noticia
from faro_editorial.decisiones import ClienteDecisiones, PreguntaChoice
from faro_editorial.settings import ROOT_DIR

RUTA_CONFIG = ROOT_DIR / "config" / "clasificacion_v1.yaml"
NOMBRE_GRUPOS = "grupos.jsonl"
NOMBRE_RESUMEN = "clasificacion.json"
NOMBRE_ETIQUETAS = "etiquetas_tema.csv"
NOMBRE_EVALUACION = "evaluacion_tema"
OTRO = "otro"

Idioma = Literal["es", "en"]


class Pregunta(BaseModel):
    instrucciones: str
    opciones: dict[str, str]


class ConfigClasificacion(BaseModel):
    version: str
    metodo: Literal["jev", "palabras"] = "jev"
    hilos: int = 8
    titulares_por_grupo: int = 3
    idioma: Idioma = "es"  # idioma de la pregunta para Jev al clasificar los grupos
    pregunta: dict[Idioma, Pregunta]
    palabras_clave: dict[str, list[str]]

    @property
    def temas(self) -> list[str]:
        return list(self.pregunta["es"].opciones)


def load_config(path: Path = RUTA_CONFIG) -> ConfigClasificacion:
    with Path(path).open(encoding="utf-8") as f:
        config = ConfigClasificacion.model_validate(yaml.safe_load(f))
    temas = set(config.temas)
    for idioma, pregunta in config.pregunta.items():
        if set(pregunta.opciones) != temas:
            raise ValueError(f"La pregunta en '{idioma}' no tiene los mismos temas que la de 'es'")
    if desconocidos := set(config.palabras_clave) - temas:
        raise ValueError(f"Palabras clave para temas desconocidos: {sorted(desconocidos)}")
    return config


# --- Línea base -----------------------------------------------------------------------


def _patrones(config: ConfigClasificacion) -> dict[str, list[tuple[str, re.Pattern]]]:
    return {
        tema: [(p, re.compile(rf"\b{re.escape(normalizar(p))}\b")) for p in palabras]
        for tema, palabras in config.palabras_clave.items()
    }


def clasificar_por_palabras(
    textos: list[str], config: ConfigClasificacion, patrones: dict | None = None
) -> tuple[str, dict[str, list[str]]]:
    """Gana el tema con más palabras clave distintas; empate: orden de la config; ninguna:
    otro. Devuelve también las palabras encontradas, para explicar la decisión."""
    patrones = patrones or _patrones(config)
    texto = normalizar(" | ".join(textos))
    encontradas = {
        tema: [p for p, patron in lista if patron.search(texto)] for tema, lista in patrones.items()
    }
    encontradas = {t: ps for t, ps in encontradas.items() if ps}
    if not encontradas:
        return OTRO, {}
    orden = list(config.palabras_clave)
    tema = max(encontradas, key=lambda t: (len(encontradas[t]), -orden.index(t)))
    return tema, encontradas


# --- Jev ------------------------------------------------------------------------------


def estado_jev(textos: list[str]) -> str:
    return "Titulares:\n" + "\n".join(f"- {t}" for t in textos)


@dataclass
class ResultadoTema:
    tema: str
    metodo: Literal["jev", "palabras"]
    confianza: float | None = None
    probabilidades: dict[str, float] | None = None
    palabras: dict[str, list[str]] = field(default_factory=dict)
    motivo_respaldo: str | None = None  # por qué se usó la línea base en lugar de Jev
    costo_usd: float | None = None
    desde_cache: bool = False


class ClasificadorTema:
    def __init__(
        self,
        config: ConfigClasificacion,
        cliente: ClienteDecisiones | None,
        idioma: Idioma = "es",
    ) -> None:
        self.config = config
        self.cliente = cliente
        self.idioma = idioma
        p = config.pregunta[idioma]
        self.preguntas = {
            "tema": PreguntaChoice(instrucciones=p.instrucciones, opciones=p.opciones)
        }
        self.version_prompt = f"{config.version}-{idioma}"
        self._patrones = _patrones(config)

    def clasificar(self, textos: list[str]) -> ResultadoTema:
        tema_base, palabras = clasificar_por_palabras(textos, self.config, self._patrones)
        if self.cliente is None:
            return ResultadoTema(tema_base, "palabras", palabras=palabras)
        decision = self.cliente.decidir(estado_jev(textos), self.preguntas, self.version_prompt)
        respuesta = decision.respuesta("tema")
        if decision.abstencion or respuesta is None:
            return ResultadoTema(
                tema_base,
                "palabras",
                palabras=palabras,
                motivo_respaldo=decision.motivo or "Jev sin respuesta",
            )
        return ResultadoTema(
            respuesta.opcion,
            "jev",
            confianza=respuesta.confianza,
            probabilidades=respuesta.probabilidades,
            palabras=palabras,
            costo_usd=decision.costo_usd,
            desde_cache=decision.desde_cache,
        )

    def clasificar_varios(
        self, lotes: list[list[str]], progreso: bool = False
    ) -> list[ResultadoTema]:
        hechos = 0
        candado = threading.Lock()

        def uno(textos: list[str]) -> ResultadoTema:
            nonlocal hechos
            r = self.clasificar(textos)
            if progreso:
                with candado:
                    hechos += 1
                    if hechos % 50 == 0 or hechos == len(lotes):
                        print(
                            f"\r  … {hechos}/{len(lotes)} grupos",
                            end="",
                            file=sys.stderr,
                            flush=True,
                        )
            return r

        if self.cliente is None or self.config.hilos <= 1:
            resultados = [uno(t) for t in lotes]
        else:
            with ThreadPoolExecutor(max_workers=self.config.hilos) as hilos:
                resultados = list(hilos.map(uno, lotes))
        if progreso:
            print(file=sys.stderr)
        return resultados


# --- Grupos ---------------------------------------------------------------------------


def leer_grupos(processed_dir: Path, noticias: list[Noticia]) -> list[tuple[str, list[Noticia]]]:
    """Grupos de grupos.jsonl (agrupación, #9) más una noticia por grupo para las sueltas.
    Una línea inválida o una noticia repetida se ignoran aquí: la bandeja las advierte."""
    por_id = {n.id_noticia: n for n in noticias}
    ruta = Path(processed_dir) / NOMBRE_GRUPOS
    grupos: list[tuple[str, list[Noticia]]] = []
    asignadas: set[str] = set()
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            try:
                datos = json.loads(linea)
                id_grupo, ids = datos["id_grupo"], datos["ids_noticias"]
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            miembros = [por_id[i] for i in ids if i in por_id and i not in asignadas]
            if miembros:
                asignadas.update(n.id_noticia for n in miembros)
                grupos.append((id_grupo, miembros))
    grupos += [(n.id_noticia, [n]) for n in noticias if n.id_noticia not in asignadas]
    return grupos


def clasificar_grupos(
    processed_dir: Path,
    config: ConfigClasificacion | None = None,
    cliente: ClienteDecisiones | None = None,
    progreso: bool = False,
) -> dict[str, Any]:
    """Clasifica todos los grupos y reescribe grupos.jsonl con el tema de cada uno (las
    noticias sueltas pasan a ser grupos de una nota, para que también tengan tema)."""
    from faro_editorial.bandeja import leer_noticias
    from faro_editorial.carga import NOMBRE_DB

    processed_dir = Path(processed_dir)
    config = config or load_config()
    noticias = leer_noticias(processed_dir / NOMBRE_DB)
    grupos = leer_grupos(processed_dir, noticias)
    clasificador = ClasificadorTema(config, cliente, config.idioma)
    lotes = [[n.titulo for n in miembros[: config.titulares_por_grupo]] for _, miembros in grupos]
    resultados = clasificador.clasificar_varios(lotes, progreso=progreso)

    lineas = []
    for (id_grupo, miembros), r in zip(grupos, resultados, strict=True):
        fila = {
            "id_grupo": id_grupo,
            "ids_noticias": [n.id_noticia for n in miembros],
            "tema": r.tema,
            "tema_metodo": r.metodo,
        }
        if r.confianza is not None:
            fila["tema_confianza"] = round(r.confianza, 4)
        lineas.append(json.dumps(fila, ensure_ascii=False))
    (processed_dir / NOMBRE_GRUPOS).write_text(
        "\n".join(lineas) + ("\n" if lineas else ""), encoding="utf-8"
    )

    respaldos = Counter(r.motivo_respaldo for r in resultados if r.motivo_respaldo)
    costos = [r.costo_usd for r in resultados if r.metodo == "jev" and not r.desde_cache]
    resumen = {
        "version": config.version,
        "generado_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "metodo_configurado": "jev" if cliente else "palabras",
        "modelo": getattr(getattr(cliente, "proveedor", None), "modelo", None),
        "grupos": len(grupos),
        "por_tema": dict(Counter(r.tema for r in resultados).most_common()),
        "por_metodo": dict(Counter(r.metodo for r in resultados).most_common()),
        "jev_desde_cache": sum(r.desde_cache for r in resultados),
        "jev_en_vivo": len(costos),
        "costo_usd_en_vivo": round(sum(c for c in costos if c), 6),
        "motivos_de_respaldo": dict(respaldos.most_common(5)),
        "coincidencia_jev_vs_palabras": _coincidencia(clasificador, lotes, resultados),
    }
    (processed_dir / NOMBRE_RESUMEN).write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return resumen


def _coincidencia(
    clasificador: ClasificadorTema, lotes: list[list[str]], resultados: list[ResultadoTema]
) -> float | None:
    """Proporción de grupos donde Jev y la línea base coinciden (sin etiquetas: solo una
    señal de cuánto cambia la IA; la calidad se mide con `evaluar`)."""
    pares = [
        (r.tema, clasificar_por_palabras(t, clasificador.config, clasificador._patrones)[0])
        for t, r in zip(lotes, resultados, strict=True)
        if r.metodo == "jev"
    ]
    return round(sum(a == b for a, b in pares) / len(pares), 4) if pares else None


# --- Etiquetado humano y evaluación ---------------------------------------------------

COLUMNAS_ETIQUETAS = ["id_noticia", "titulo", "medio", "tema_humano", "etiquetador", "comentario"]


def muestra_para_etiquetar(
    processed_dir: Path,
    destino: Path,
    n: int = 60,
    semilla: int = 7,
    config: ConfigClasificacion | None = None,
) -> Path:
    """Muestra estratificada por el tema de la línea base, para que no salgan 50 de "otro".
    El CSV no incluye el tema propuesto: así las etiquetas humanas no se sesgan."""
    from faro_editorial.bandeja import leer_noticias
    from faro_editorial.carga import NOMBRE_DB

    config = config or load_config()
    destino = Path(destino)
    if destino.exists():
        raise FileExistsError(f"{destino} ya existe: no se sobrescriben etiquetas humanas.")
    noticias = leer_noticias(Path(processed_dir) / NOMBRE_DB)
    patrones = _patrones(config)
    por_tema: dict[str, list[Noticia]] = {}
    vistos: set[str] = set()
    for x in sorted(noticias, key=lambda x: x.id_noticia):
        clave = normalizar(x.titulo)
        if clave in vistos:  # titulares repetidos no aportan
            continue
        vistos.add(clave)
        tema, _ = clasificar_por_palabras([x.titulo], config, patrones)
        por_tema.setdefault(tema, []).append(x)
    azar = random.Random(semilla)
    for lista in por_tema.values():
        azar.shuffle(lista)
    elegidas: list[Noticia] = []
    while len(elegidas) < n and any(por_tema.values()):
        for tema in config.temas:  # reparto en rondas: un titular de cada tema por vez
            if por_tema.get(tema) and len(elegidas) < n:
                elegidas.append(por_tema[tema].pop())
    azar.shuffle(elegidas)
    destino.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: Excel muestra bien las tildes.
    with destino.open("w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS_ETIQUETAS)
        escritor.writeheader()
        for x in elegidas:
            escritor.writerow({"id_noticia": x.id_noticia, "titulo": x.titulo, "medio": x.medio})
    return destino


def leer_etiquetas(
    ruta: Path, config: ConfigClasificacion
) -> tuple[list[dict[str, str]], list[str]]:
    """Filas con tema_humano válido y avisos. Tolera ';' como separador (Excel en español)."""
    texto = Path(ruta).read_text(encoding="utf-8-sig")
    dialecto = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=",;")
    filas, avisos = [], []
    for numero, fila in enumerate(csv.DictReader(texto.splitlines(), dialect=dialecto), 2):
        tema = (fila.get("tema_humano") or "").strip().lower()
        if not tema:
            avisos.append(f"Fila {numero}: sin tema_humano, se omite")
            continue
        if tema not in config.temas:
            avisos.append(f"Fila {numero}: tema '{tema}' desconocido, se omite")
            continue
        filas.append({**fila, "tema_humano": tema})
    return filas, avisos


def _metricas(verdad: list[str], prediccion: list[str], temas: list[str]) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, f1_score, precision_recall_fscore_support

    presentes = [t for t in temas if t in set(verdad)]
    p, r, f, soporte = precision_recall_fscore_support(
        verdad, prediccion, labels=presentes, zero_division=0
    )
    return {
        "n": len(verdad),
        "macro_f1": round(
            float(f1_score(verdad, prediccion, labels=presentes, average="macro", zero_division=0)),
            4,
        ),
        "exactitud": round(float(accuracy_score(verdad, prediccion)), 4),
        "aciertos": sum(a == b for a, b in zip(verdad, prediccion, strict=True)),
        "por_tema": {
            t: {
                "precision": round(float(p[i]), 3),
                "recall": round(float(r[i]), 3),
                "f1": round(float(f[i]), 3),
                "soporte": int(soporte[i]),
            }
            for i, t in enumerate(presentes)
        },
        "temas_evaluados": presentes,
    }


def evaluar(
    ruta_etiquetas: Path,
    clasificadores: dict[str, ClasificadorTema],
    config: ConfigClasificacion | None = None,
) -> dict[str, Any]:
    """Macro-F1 de cada variante sobre las mismas etiquetas humanas (sección 9.1, #7)."""
    config = config or load_config()
    filas, avisos = leer_etiquetas(ruta_etiquetas, config)
    if not filas:
        raise ValueError(f"No hay etiquetas válidas en {ruta_etiquetas}: " + "; ".join(avisos[:3]))
    verdad = [f["tema_humano"] for f in filas]
    variantes = {}
    for nombre, clasificador in clasificadores.items():
        resultados = clasificador.clasificar_varios([[f["titulo"]] for f in filas])
        # Para medir a Jev, una abstención es un error: no se rellena con la línea base.
        if clasificador.cliente is not None:
            prediccion = [r.tema if r.metodo == "jev" else "(sin respuesta)" for r in resultados]
        else:
            prediccion = [r.tema for r in resultados]
        costos = [r.costo_usd or 0 for r in resultados if r.metodo == "jev" and not r.desde_cache]
        variantes[nombre] = {
            **_metricas(verdad, prediccion, config.temas),
            "abstenciones": sum(r.metodo != "jev" for r in resultados)
            if clasificador.cliente is not None
            else 0,
            "costo_usd_en_vivo": round(sum(costos), 6),
            "errores": [
                {"id_noticia": f["id_noticia"], "titulo": f["titulo"], "humano": v, "modelo": p}
                for f, v, p in zip(filas, verdad, prediccion, strict=True)
                if v != p
            ][:15],
        }
    return {
        "version": config.version,
        "generado_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "etiquetas": str(ruta_etiquetas),
        "n_etiquetas": len(filas),
        "distribucion_humana": dict(Counter(verdad).most_common()),
        "avisos": avisos,
        "metodo": "macro-F1 sobre los temas presentes en las etiquetas humanas; una abstención "
        "de la IA cuenta como error.",
        "variantes": variantes,
    }


def escribir_evaluacion(resultado: dict[str, Any], carpeta: Path) -> dict[str, Path]:
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas = {
        "json": carpeta / f"{NOMBRE_EVALUACION}.json",
        "md": carpeta / f"{NOMBRE_EVALUACION}.md",
    }
    rutas["json"].write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lineas = [
        "# Evaluación de la clasificación temática",
        "",
        f"{resultado['n_etiquetas']} titulares etiquetados por el equipo · {resultado['metodo']}",
        "",
        "| Variante | Macro-F1 | Exactitud | Aciertos | Abstenciones | Costo (USD) |",
        "|---|---|---|---|---|---|",
    ]
    for nombre, v in resultado["variantes"].items():
        lineas.append(
            f"| {nombre} | {v['macro_f1']:.3f} | {v['exactitud']:.3f} | "
            f"{v['aciertos']}/{v['n']} | {v['abstenciones']} | {v['costo_usd_en_vivo']:.4f} |"
        )
    rutas["md"].write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return rutas


# --- Línea de comandos ----------------------------------------------------------------


def _cliente(tipo: str) -> ClienteDecisiones:
    from faro_editorial.proveedores import crear_cliente

    return crear_cliente("llm" if tipo == "llm" else "jev")


def main(argv: list[str] | None = None) -> None:
    from faro_editorial.carga import NOMBRE_DB
    from faro_editorial.settings import get_settings

    parser = argparse.ArgumentParser(description="Tema de cada grupo de noticias (#10).")
    parser.add_argument(
        "accion", nargs="?", default="clasificar", choices=["clasificar", "muestra", "evaluar"]
    )
    parser.add_argument("--metodo", choices=["jev", "palabras"], help="anula la config")
    parser.add_argument("--n", type=int, default=60, help="muestra: titulares a etiquetar")
    parser.add_argument("--archivo", type=Path, help="CSV de etiquetas (muestra/evaluar)")
    parser.add_argument(
        "--variantes",
        default="palabras,jev_es,jev_en",
        help="evaluar: palabras, jev_es, jev_en y/o llm, separadas por coma",
    )
    args = parser.parse_args(argv)

    s = get_settings()
    if not (s.processed_dir / NOMBRE_DB).exists():
        raise SystemExit(
            "No hay base cargada: ejecuta antes  uv run python -m faro_editorial.carga"
        )
    config = load_config()
    etiquetas = args.archivo or s.data_dir / "evaluacion" / NOMBRE_ETIQUETAS

    if args.accion == "muestra":
        try:
            ruta = muestra_para_etiquetar(s.processed_dir, etiquetas, args.n, config=config)
        except FileExistsError as e:
            raise SystemExit(str(e)) from None
        print(f"Para etiquetar ({args.n} titulares): {ruta}")
        print(f"Completen tema_humano con uno de: {', '.join(config.temas)}")
        print(
            "Cada integrante etiqueta su parte sin consultar al modelo; anoten su nombre en "
            "'etiquetador'. Las dudas van en 'comentario'."
        )
        return

    if args.accion == "evaluar":
        clasificadores: dict[str, ClasificadorTema] = {}
        for nombre in [v.strip() for v in args.variantes.split(",") if v.strip()]:
            if nombre == "palabras":
                clasificadores[nombre] = ClasificadorTema(config, None)
            elif nombre in ("jev_es", "jev_en"):
                idioma: Idioma = "en" if nombre == "jev_en" else "es"
                clasificadores[nombre] = ClasificadorTema(config, _cliente("jev"), idioma)
            elif nombre == "llm":
                clasificadores[nombre] = ClasificadorTema(config, _cliente("llm"))
            else:
                raise SystemExit(f"Variante desconocida: {nombre}")
        try:
            resultado = evaluar(etiquetas, clasificadores, config)
        except (FileNotFoundError, ValueError) as e:
            raise SystemExit(str(e)) from None
        rutas = escribir_evaluacion(resultado, etiquetas.parent)
        for aviso in resultado["avisos"][:10]:
            print(f"  Aviso: {aviso}")
        print(rutas["md"].read_text(encoding="utf-8"))
        print(f"Detalle (errores por variante): {rutas['json']}")
        return

    metodo = args.metodo or config.metodo
    cliente = _cliente("jev") if metodo == "jev" else None
    if cliente is not None and cliente.offline:
        print(
            "Aviso: OFFLINE=1, Jev solo responde desde la caché; lo que falte usa la línea base. "
            'Para la primera corrida:  $env:OFFLINE="0"'
        )
    r = clasificar_grupos(s.processed_dir, config, cliente, progreso=True)
    print(f"{r['grupos']} grupos clasificados · {r['version']} · modelo {r['modelo'] or '-'}")
    print(f"Por tema: {r['por_tema']}")
    print(
        f"Por método: {r['por_metodo']} · Jev en vivo: {r['jev_en_vivo']} "
        f"(USD {r['costo_usd_en_vivo']}), desde caché: {r['jev_desde_cache']}"
    )
    if r["motivos_de_respaldo"]:
        print(f"Línea base por: {r['motivos_de_respaldo']}")
    if r["coincidencia_jev_vs_palabras"] is not None:
        print(f"Coincidencia Jev / palabras clave: {r['coincidencia_jev_vs_palabras']:.0%}")
    print(f"Grupos con tema para la bandeja: {s.processed_dir / NOMBRE_GRUPOS}")


if __name__ == "__main__":
    main()
