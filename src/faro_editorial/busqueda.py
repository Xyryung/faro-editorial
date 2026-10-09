"""Etapa 5 · Explicar: búsqueda híbrida y compuerta de abstención (issue #13, T06, CU-04).

Responde consultas en español recuperando evidencia del snapshot. Si no hay evidencia
suficiente, se abstiene de forma explícita y explica qué información haría falta: nunca
inventa cifras ni citas.

- Línea base: BM25 por palabras clave (sin IA).
- Variante híbrida: BM25 + similitud semántica (embeddings, con caída a TF-IDF registrada).
- Compuerta de abstención: umbral de recuperación + pregunta Noul a Jev ("la evidencia
  responde la pregunta"). Sin cliente (offline sin caché), decide solo el umbral y queda
  registrado el método.

Comandos (desde la raíz del repo, después de la carga):
    uv run python -m faro_editorial.busqueda "¿qué lluvias hubo en Chiriquí?"
    uv run python -m faro_editorial.busqueda "pregunta" --metodo bm25
    uv run python -m faro_editorial.busqueda comparar "pregunta"
"""

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

from faro_editorial.agrupacion import Representador, crear_representador
from faro_editorial.contexto import normalizar
from faro_editorial.decisiones import ClienteDecisiones, PreguntaNoul
from faro_editorial.settings import ROOT_DIR

RUTA_CONFIG = ROOT_DIR / "config" / "busqueda_v1.yaml"


class PreguntaNoulConfig(BaseModel):
    instrucciones: str
    criterio_si: str | None = None
    criterio_no: str | None = None


class ConfigBusqueda(BaseModel):
    version: str
    k_recuperados: int = Field(ge=1, le=20)
    umbral: float = Field(ge=0, le=1)
    margen: float = Field(ge=0, le=1)
    alfa: float = Field(ge=0, le=1)
    umbral_noul: float = Field(ge=0, le=1)
    metodo_semantico: str = "auto"
    pregunta_noul: PreguntaNoulConfig


def load_config(path: Path = RUTA_CONFIG) -> ConfigBusqueda:
    with Path(path).open(encoding="utf-8") as f:
        return ConfigBusqueda.model_validate(yaml.safe_load(f))


def _tokens(texto: str) -> list[str]:
    return normalizar(texto).split()


@dataclass
class Cita:
    id_noticia: str
    titulo: str
    medio: str
    puntaje: float
    metodo: str
    prob_noul: float | None = None


@dataclass
class RespuestaConsulta:
    pregunta: str
    abstencion: bool
    citas: list[Cita] = field(default_factory=list)
    motivo: str | None = None
    # Qué información haría falta para responder (solo en abstenciones).
    falta: str | None = None
    metodo: str = "hibrida"
    motivo_respaldo: str | None = None


class Buscador:
    """Corpus en memoria: BM25 siempre, semántica según config (E5 o TF-IDF)."""

    def __init__(
        self,
        documentos: list[dict],
        config: ConfigBusqueda | None = None,
        representador: Representador | None = None,
    ) -> None:
        self.config = config or load_config()
        self.docs = documentos
        self._bm25 = BM25Okapi([_tokens(d["titulo"]) for d in documentos])
        if representador is None:
            representador, motivo = crear_representador(
                self.config.metodo_semantico,  # type: ignore[arg-type]
                self._modelo_e5(),
            )
            self.motivo_respaldo = motivo
        else:
            self.motivo_respaldo = None
        self._representador = representador
        self._titulos = [d["titulo"] for d in documentos]

    @staticmethod
    def _modelo_e5() -> str:
        from faro_editorial.settings import get_settings

        return get_settings().embedding_model

    def _puntajes(self, consulta: str, metodo: str) -> tuple[list[float], list[float], list[bool]]:
        """Devuelve (fusión para ordenar, coseno crudo para la compuerta, ancla BM25)."""
        toks = _tokens(consulta)
        bm25 = list(self._bm25.get_scores(toks))
        hits = [s > 0 for s in bm25]
        max_bm25 = max(bm25) if bm25 else 0.0
        bm25_n = [s / max_bm25 if max_bm25 > 0 else 0.0 for s in bm25]
        if metodo == "bm25":
            return bm25_n, bm25_n, hits
        # Consulta y corpus se vectorizan juntos: comparten el mismo espacio
        # (imprescindible con TF-IDF, que ajusta su vocabulario al invocarlo).
        matriz = self._representador.vectores([*self._titulos, consulta])
        sims = matriz[:-1] @ matriz[-1].T
        if hasattr(sims, "todense"):  # dispersa (TF-IDF)
            sims = sims.todense()
        import numpy as np

        coseno = [float(x) for x in np.asarray(sims).ravel()]
        max_sem = max(coseno) if coseno else 0.0
        sem_n = [s / max_sem if max_sem > 0 else 0.0 for s in coseno]
        alfa = self.config.alfa
        fusion = [alfa * s + (1 - alfa) * b for s, b in zip(sem_n, bm25_n, strict=True)]
        return fusion, coseno, hits

    def recuperar(self, consulta: str, metodo: str = "hibrida") -> tuple[list[Cita], str | None]:
        """Top-k por encima del umbral, ordenados por fusión.

        Sin ninguna coincidencia léxica se exige además un ganador semántico claro
        (margen sobre el segundo): los embeddings dan cosenos altos hasta a lo ajeno.
        Devuelve (citas, motivo de rechazo o None si hay candidatos).
        """
        fusion, coseno, hits = self._puntajes(consulta, metodo)
        orden = sorted(range(len(self.docs)), key=lambda i: -fusion[i])
        if not orden or coseno[orden[0]] < self.config.umbral:
            return [], (
                f"ningún registro supera el umbral {self.config.umbral} ({self.config.version})"
            )
        if (
            len(orden) > 1
            and not any(hits)
            and coseno[orden[0]] - coseno[orden[1]] < self.config.margen
        ):
            return [], (
                "la consulta no tiene coincidencias léxicas y ningún candidato destaca "
                f"en lo semántico (margen < {self.config.margen})"
            )
        citas = []
        for i in orden[: self.config.k_recuperados]:
            if coseno[i] < self.config.umbral:
                continue
            d = self.docs[i]
            citas.append(
                Cita(
                    id_noticia=d["id_noticia"],
                    titulo=d["titulo"],
                    medio=d.get("medio") or "?",
                    puntaje=round(fusion[i], 4),
                    metodo=metodo,
                )
            )
        return citas, None

    def responder(
        self,
        consulta: str,
        cliente: ClienteDecisiones | None,
        metodo: str = "hibrida",
    ) -> RespuestaConsulta:
        """Recupera candidatos y aplica la compuerta: umbral + Noul a Jev."""
        candidatos, rechazo = self.recuperar(consulta, metodo)
        if not candidatos:
            return self._abstencion(
                consulta,
                metodo,
                f"{rechazo or 'sin candidatos'}; {self.config.version}.",
            )
        if cliente is None:
            for c in candidatos:
                c.metodo = f"{metodo}+umbral"
            notas = [
                m
                for m in (
                    self.motivo_respaldo,
                    "Sin Jev (offline sin caché): decide solo el umbral.",
                )
                if m
            ]
            return RespuestaConsulta(
                pregunta=consulta,
                abstencion=False,
                citas=candidatos,
                metodo=metodo,
                motivo_respaldo=" ".join(notas),
            )
        pregunta = PreguntaNoul(
            instrucciones=self.config.pregunta_noul.instrucciones,
            criterio_si=self.config.pregunta_noul.criterio_si,
            criterio_no=self.config.pregunta_noul.criterio_no,
        )
        aceptadas = []
        for c in candidatos:
            estado = f"Pregunta: {consulta}\nEvidencia: {c.titulo} ({c.medio})"
            decision = cliente.decidir(estado, {"responde": pregunta}, self.config.version)
            respuesta = decision.respuesta("responde")
            if decision.abstencion or respuesta is None:
                continue
            c.prob_noul = round(respuesta.probabilidad, 4)
            if respuesta.probabilidad >= self.config.umbral_noul:
                aceptadas.append(c)
        if not aceptadas:
            return self._abstencion(
                consulta,
                metodo,
                f"{len(candidatos)} candidato(s) superaron el umbral, pero ninguno pasó "
                f"la pregunta Noul (umbral {self.config.umbral_noul}).",
            )
        return RespuestaConsulta(
            pregunta=consulta, abstencion=False, citas=aceptadas, metodo=metodo
        )

    def _abstencion(self, consulta: str, metodo: str, motivo: str) -> RespuestaConsulta:
        medios = sorted({d.get("medio") or "?" for d in self.docs})
        return RespuestaConsulta(
            pregunta=consulta,
            abstencion=True,
            motivo=motivo,
            falta=(
                "Para responder haría falta evidencia que hoy no está en el corpus: "
                f"el snapshot cubre {len(self.docs)} noticia(s) de {len(medios)} medio(s). "
                "Reformula la pregunta, amplía el snapshot o verifica por otra vía."
            ),
            metodo=metodo,
            motivo_respaldo=self.motivo_respaldo,
        )


def leer_corpus(processed_dir: Path) -> list[dict]:
    """Noticias de la base cargada como corpus de búsqueda."""
    import duckdb

    from faro_editorial.carga import NOMBRE_DB

    with duckdb.connect(str(Path(processed_dir) / NOMBRE_DB), read_only=True) as con:
        filas = con.execute("SELECT id_noticia, titulo, medio FROM noticias").fetchall()
    return [{"id_noticia": r[0], "titulo": r[1], "medio": r[2]} for r in filas]


def main(argv: list[str] | None = None) -> None:
    from faro_editorial.settings import get_settings

    parser = argparse.ArgumentParser(description="Consulta con abstención (#13).")
    parser.add_argument("consulta", nargs="?", help="pregunta en español (o 'comparar')")
    parser.add_argument("--texto", help="pregunta (alternativa a posicional, para comparar)")
    parser.add_argument("--metodo", choices=["hibrida", "bm25"], default="hibrida")
    parser.add_argument("--k", type=int, default=None, help="candidatos (anula la config)")
    args = parser.parse_args(argv)

    s = get_settings()
    if not (s.processed_dir / "faro.duckdb").exists():
        raise SystemExit(
            "No hay base cargada: ejecuta antes  uv run python -m faro_editorial.carga"
        )
    config = load_config()
    if args.k is not None:
        config = config.model_copy(update={"k_recuperados": args.k})
    buscador = Buscador(leer_corpus(s.processed_dir), config)

    def cliente() -> ClienteDecisiones | None:
        if s.offline:
            print("Aviso: OFFLINE=1, sin Jev: decide solo el umbral.")
            return None
        from faro_editorial.proveedores import crear_cliente

        return crear_cliente("jev")

    consultas = [args.texto or args.consulta] if args.consulta != "comparar" else [args.texto]
    if args.consulta == "comparar":
        if not args.texto:
            raise SystemExit("comparar necesita --texto 'pregunta'")
        for metodo in ("bm25", "hibrida"):
            r = buscador.responder(args.texto, cliente(), metodo)
            _imprimir(r, detalle=False)
        return
    if not consultas[0]:
        raise SystemExit("Indica una pregunta entre comillas.")
    _imprimir(buscador.responder(consultas[0], cliente(), args.metodo))


def _imprimir(r: RespuestaConsulta, detalle: bool = True) -> None:
    print(f"[{r.metodo}] {r.pregunta}")
    if r.abstencion:
        print(f"  Abstención: {r.motivo}")
        print(f"  Falta: {r.falta}")
        return
    for c in r.citas:
        extra = f" · Noul {c.prob_noul}" if c.prob_noul is not None else ""
        print(f"  {c.puntaje:.2f} [{c.id_noticia}] {c.titulo} ({c.medio}){extra}")
    if r.motivo_respaldo and detalle:
        print(f"  Nota: {r.motivo_respaldo}")


if __name__ == "__main__":
    main()
