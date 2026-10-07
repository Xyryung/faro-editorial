"""Etapa 4 · Priorizar: puntaje de atención explicable (issue #11, T08, CU-01).

P = 30R + 25I + 20U + 15N + 10E, con pesos y rangos de config/rules_v1.yaml y criterios de
normalización de config/criterios_v1.yaml. Es una herramienta de ordenamiento, no una
probabilidad de verdad: cada componente muestra su valor, su aporte y el criterio usado, y el
estado de evidencia se calcula aparte. Una prioridad alta nunca habilita publicación.

Los componentes R, I y U pueden venir de otro evaluador (p. ej. Jev Score, #8) que cumpla
EvaluadorComponentes; los que no entregue se calculan con las reglas base.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field

from faro_editorial.contexto import Contexto, ContextoOficial, Vinculo, buscar_palabra
from faro_editorial.contrato import Noticia
from faro_editorial.rules import COMPONENTES, Reglas, load_rules
from faro_editorial.settings import ROOT_DIR, get_settings

RUTA_CRITERIOS = ROOT_DIR / "config" / "criterios_v1.yaml"
AVISO_PUBLICACION = (
    "La prioridad ordena qué revisar; no habilita publicación. La decisión editorial es de "
    "la persona revisora."
)

EstadoEvidencia = Literal["insuficiente", "parcial", "suficiente_para_borrador"]


# --- Criterios ------------------------------------------------------------------------


class _Tramo(BaseModel):
    horas_maxima: float
    horas_cero: float


class _Relevancia(BaseModel):
    palabras_panama: list[str]
    dominios_panama: list[str]


class _Evidencia(BaseModel):
    procedencias_para_maximo: int
    peso_procedencias: float
    peso_respaldo_oficial: float


class _EstadoEvidencia(BaseModel):
    procedencias_suficientes: int


class Criterios(BaseModel):
    version: str
    relevancia: _Relevancia
    impacto_por_tema: dict[str, float]
    impacto_sin_tema: float
    urgencia: _Tramo
    novedad: _Tramo
    evidencia: _Evidencia
    estado_evidencia: _EstadoEvidencia


def load_criterios(path: Path = RUTA_CRITERIOS) -> Criterios:
    with Path(path).open(encoding="utf-8") as f:
        return Criterios.model_validate(yaml.safe_load(f))


# --- Entrada y salida -----------------------------------------------------------------


class GrupoNoticias(BaseModel):
    """Noticias sobre un mismo evento (lo produce la agrupación, #9). Sin agrupación, cada
    noticia es su propio grupo."""

    id_grupo: str
    noticias: list[Noticia] = Field(min_length=1)
    tema: str | None = None  # si falta, el tema más frecuente entre sus noticias

    @classmethod
    def de_noticia(cls, noticia: Noticia) -> "GrupoNoticias":
        return cls(id_grupo=noticia.id_noticia, noticias=[noticia])

    @property
    def tema_efectivo(self) -> str | None:
        if self.tema:
            return self.tema
        temas = Counter(n.tema for n in self.noticias if n.tema)
        return temas.most_common(1)[0][0] if temas else None

    @property
    def fechas(self) -> list[datetime]:
        """Una fecha por noticia: la de publicación o, si falta, la de detección."""
        return [f for n in self.noticias if (f := n.fecha_publicacion or n.fecha_deteccion)]

    @property
    def todas_las_fechas(self) -> list[datetime]:
        """Publicación y detección de cada noticia: un artículo viejo detectado hoy (GDELT)
        muestra que el evento lleva tiempo circulando aunque el grupo tenga una sola nota."""
        return [f for n in self.noticias for f in (n.fecha_publicacion, n.fecha_deteccion) if f]

    @property
    def procedencias(self) -> set[str]:
        # Medios distintos, no noticias: tres notas del mismo medio son una procedencia (T02).
        return {n.medio.strip().lower() for n in self.noticias}

    @property
    def texto(self) -> str:
        # Titular y, si existe, la descripción del RSS (solo análisis interno, decisión #35).
        partes = []
        for n in self.noticias:
            partes.append(n.titulo)
            if n.descripcion:
                partes.append(n.descripcion)
        return "\n".join(partes)


class Componente(BaseModel):
    valor: float = Field(ge=0, le=1)
    criterio: str
    fuente: str = "reglas"  # "reglas" o el evaluador que lo calculó (p. ej. "jev")


class ComponentePuntuado(Componente):
    peso: int
    aporte: float


class Puntuacion(BaseModel):
    id_grupo: str
    puntaje: float
    banda: str
    componentes: dict[str, ComponentePuntuado]
    estado_evidencia: EstadoEvidencia
    motivo_estado: str
    procedencias: list[str]
    ids_noticias: list[str]
    vinculos: list[Vinculo] = []  # evidencia oficial con su cita (etapa 3)
    pendientes: list[str]
    version_reglas: str
    version_criterios: str
    habilita_publicacion: Literal[False] = False
    aviso: str = AVISO_PUBLICACION


class EvaluadorComponentes(Protocol):
    """Evaluador alternativo (p. ej. Jev Score). Devuelve solo los componentes que calcula."""

    def evaluar(self, grupo: GrupoNoticias, referencia: datetime) -> dict[str, Componente]: ...


# --- Reglas base por componente -------------------------------------------------------


def _tramo(horas: float, tramo: _Tramo) -> float:
    if horas <= tramo.horas_maxima:
        return 1.0
    if horas >= tramo.horas_cero:
        return 0.0
    return round(1 - (horas - tramo.horas_maxima) / (tramo.horas_cero - tramo.horas_maxima), 4)


def _dominio_coincide(dominio: str, patron: str) -> bool:
    """'.pa' es un sufijo de país; 'tvn-2.com' es el dominio o un subdominio suyo, nunca
    otro dominio que solo termine igual ('faketvn-2.com')."""
    if patron.startswith("."):
        return dominio.endswith(patron)
    return dominio == patron or dominio.endswith(f".{patron}")


def _relacion_panama(grupo: GrupoNoticias, c: _Relevancia) -> str | None:
    if palabra := buscar_palabra(grupo.texto, c.palabras_panama):
        return f"el texto menciona '{palabra}'"
    for n in grupo.noticias:
        dominio = (urlparse(n.url).hostname or "").lower()
        if any(_dominio_coincide(dominio, d) for d in c.dominios_panama):
            return f"medio panameño ({dominio})"
    return None


def relevancia(grupo: GrupoNoticias, reglas: Reglas, c: Criterios) -> Componente:
    panama = _relacion_panama(grupo, c.relevancia)
    tema = grupo.tema_efectivo
    editorial = tema in reglas.temas and tema != "otro"
    valor = 0.5 * bool(panama) + 0.5 * editorial
    partes = [
        f"Panamá: {panama}" if panama else "sin relación explícita con Panamá",
        f"tema editorial '{tema}'" if editorial else f"tema '{tema or 'sin clasificar'}'",
    ]
    return Componente(valor=valor, criterio="; ".join(partes) + ".")


def impacto(grupo: GrupoNoticias, c: Criterios) -> Componente:
    tema = grupo.tema_efectivo
    if tema is None:
        return Componente(
            valor=c.impacto_sin_tema,
            criterio="Tema sin clasificar: impacto base provisional.",
        )
    valor = c.impacto_por_tema.get(tema, c.impacto_sin_tema)
    return Componente(valor=valor, criterio=f"Alcance típico del tema '{tema}' ({c.version}).")


def urgencia(grupo: GrupoNoticias, referencia: datetime, c: Criterios) -> Componente:
    if not grupo.fechas:
        return Componente(valor=0.0, criterio="Sin fecha: no se puede medir la urgencia.")
    horas = max((referencia - max(grupo.fechas)).total_seconds() / 3600, 0)
    return Componente(
        valor=_tramo(horas, c.urgencia),
        criterio=(
            f"Última noticia hace {horas:.0f} h (1 hasta {c.urgencia.horas_maxima:.0f} h, "
            f"0 desde {c.urgencia.horas_cero:.0f} h)."
        ),
    )


def novedad(grupo: GrupoNoticias, c: Criterios) -> Componente:
    fechas = grupo.todas_las_fechas
    if not fechas:
        return Componente(valor=0.0, criterio="Sin fecha: no se puede medir la novedad.")
    horas = (max(fechas) - min(fechas)).total_seconds() / 3600
    criterio = (
        f"El evento lleva {horas:.0f} h circulando, de la primera a la última fecha "
        f"(1 hasta {c.novedad.horas_maxima:.0f} h, 0 desde {c.novedad.horas_cero:.0f} h). "
        "El número de noticias no suma."
    )
    if horas > c.novedad.horas_maxima:
        criterio += " Posible noticia antigua recirculada: verificar la fecha original."
    return Componente(valor=_tramo(horas, c.novedad), criterio=criterio)


def evidencia(grupo: GrupoNoticias, contexto: Contexto | None, c: Criterios) -> Componente:
    e = c.evidencia
    n = len(grupo.procedencias)
    oficiales = [v.id_evidencia for v in contexto.vinculos] if contexto else []
    valor = e.peso_procedencias * min(n, e.procedencias_para_maximo) / (
        e.procedencias_para_maximo
    ) + e.peso_respaldo_oficial * bool(oficiales)
    respaldo = f"respaldo oficial: {', '.join(oficiales)}" if oficiales else "sin respaldo oficial"
    return Componente(
        valor=round(min(valor, 1.0), 4),
        criterio=f"{n} procedencia(s) independiente(s); {respaldo}.",
    )


def estado_evidencia(
    grupo: GrupoNoticias, contexto: Contexto | None, c: Criterios
) -> tuple[EstadoEvidencia, str]:
    n = len(grupo.procedencias)
    oficial = bool(contexto and contexto.vinculos)
    pendientes = contexto.pendientes if contexto else []
    if n == 1 and not oficial:
        return "insuficiente", "Una sola procedencia y sin respaldo oficial: requiere investigar."
    if n >= c.estado_evidencia.procedencias_suficientes and not pendientes:
        return "suficiente_para_borrador", f"{n} procedencias independientes y sin pendientes."
    motivo = f"{n} procedencia(s)" + (" con respaldo oficial" if oficial else "")
    if pendientes:
        motivo += f"; {len(pendientes)} dato(s) pendiente(s) de verificar"
    return "parcial", motivo + "."


# --- Motor ----------------------------------------------------------------------------


class MotorPuntaje:
    def __init__(
        self,
        reglas: Reglas | None = None,
        criterios: Criterios | None = None,
        contexto_oficial: ContextoOficial | None = None,
        evaluador: EvaluadorComponentes | None = None,
    ) -> None:
        self.reglas = reglas or load_rules(get_settings().rules_path)
        self.criterios = criterios or load_criterios()
        self.contexto_oficial = contexto_oficial
        self.evaluador = evaluador

    def puntuar(self, grupo: GrupoNoticias, referencia: datetime) -> Puntuacion:
        """referencia: fecha de corte del snapshot (la demo es reproducible, no usa 'ahora')."""
        c = self.criterios
        contexto = None
        if self.contexto_oficial is not None:
            # Desde la primera noticia: el sismo ocurrió antes de que empezaran a reportarlo.
            fecha = min(grupo.fechas) if grupo.fechas else None
            contexto = self.contexto_oficial.vincular(grupo.texto, fecha, grupo.id_grupo)

        componentes = {
            "R": relevancia(grupo, self.reglas, c),
            "I": impacto(grupo, c),
            "U": urgencia(grupo, referencia, c),
            "N": novedad(grupo, c),
            "E": evidencia(grupo, contexto, c),
        }
        if self.evaluador is not None:
            externos = self.evaluador.evaluar(grupo, referencia)
            componentes.update({k: v for k, v in externos.items() if k in ("R", "I", "U")})

        puntuados = {
            k: ComponentePuntuado(
                **componentes[k].model_dump(),
                peso=self.reglas.pesos[k],
                aporte=round(self.reglas.pesos[k] * componentes[k].valor, 2),
            )
            for k in COMPONENTES
        }
        puntaje = round(sum(p.aporte for p in puntuados.values()), 1)
        estado, motivo = estado_evidencia(grupo, contexto, c)
        return Puntuacion(
            id_grupo=grupo.id_grupo,
            puntaje=puntaje,
            banda=self.reglas.banda(puntaje),
            componentes=puntuados,
            estado_evidencia=estado,
            motivo_estado=motivo,
            procedencias=sorted(grupo.procedencias),
            ids_noticias=[n.id_noticia for n in grupo.noticias],
            vinculos=contexto.vinculos if contexto else [],
            pendientes=contexto.pendientes if contexto else [],
            version_reglas=self.reglas.version,
            version_criterios=c.version,
        )

    def ranking(self, grupos: Sequence[GrupoNoticias], referencia: datetime) -> list[Puntuacion]:
        """Ordena de mayor a menor puntaje. Empates según `desempate` de las reglas
        (v1: mayor urgencia y luego ID)."""
        puntuaciones = [self.puntuar(g, referencia) for g in grupos]

        def clave(p: Puntuacion) -> tuple:
            desempate = [
                p.id_grupo if criterio == "id" else -p.componentes[criterio].valor
                for criterio in self.reglas.desempate
            ]
            return (-p.puntaje, *desempate)

        return sorted(puntuaciones, key=clave)
