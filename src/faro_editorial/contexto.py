"""Etapa 3 · Contextualizar: relaciona noticias con indicadores del Banco Mundial y sismos
de USGS sin forzar relaciones (issue #12, T04, CU-02).

- Indicadores: solo si el titular menciona lo que el indicador mide (config/contexto_v1.yaml).
  La cita sale de una plantilla con país, año, unidad e ID, y siempre aclara que es un dato
  anual histórico, nunca una medición de hoy.
- USGS: solo para noticias sobre sismos y solo si hay un evento registrado cerca de la fecha
  de la noticia. Nunca respalda inundaciones, daños ni pérdidas.
- Cada vínculo guarda la regla que lo justificó; cuando no hay vínculo sustentado, se anota
  el motivo como información pendiente de verificar.
"""

import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import duckdb
import yaml
from pydantic import BaseModel

from faro_editorial.contrato import Evento, Indicador, Noticia
from faro_editorial.settings import ROOT_DIR

RUTA_REGLAS = ROOT_DIR / "config" / "contexto_v1.yaml"
ZONA_PANAMA = ZoneInfo("America/Panama")

PAISES = {
    "PAN": "Panamá",
    "CRI": "Costa Rica",
    "COL": "Colombia",
    "DOM": "República Dominicana",
    "MEX": "México",
    "GTM": "Guatemala",
}


# --- Reglas ---------------------------------------------------------------------------


class ReglaIndicador(BaseModel):
    nombre: str
    palabras_clave: list[str]


class ReglasUSGS(BaseModel):
    palabras_clave: list[str]
    horas_antes: int
    horas_despues: int
    limitacion: str


class ReglasContexto(BaseModel):
    version: str
    pais: str
    indicadores: dict[str, ReglaIndicador]
    anios_serie: int
    usgs: ReglasUSGS


def load_reglas_contexto(path: Path = RUTA_REGLAS) -> ReglasContexto:
    with Path(path).open(encoding="utf-8") as f:
        return ReglasContexto.model_validate(yaml.safe_load(f))


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar palabras clave."""
    sin_tildes = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sin_tildes if not unicodedata.combining(c)).lower()


def buscar_palabra(texto: str, palabras: list[str]) -> str | None:
    """Devuelve la primera palabra clave que aparece como palabra completa en el texto."""
    normalizado = normalizar(texto)
    for palabra in palabras:
        if re.search(rf"\b{re.escape(normalizar(palabra))}\b", normalizado):
            return palabra
    return None


# --- Resultado ------------------------------------------------------------------------


class Vinculo(BaseModel):
    tipo: Literal["indicador", "evento_sismico"]
    id_evidencia: str  # BM:PAN:<indicador>:<año> o USGS:<id del evento>
    campo: str  # campo de la evidencia que respalda la cita
    cita: str
    regla: str  # por qué se vinculó
    limitaciones: list[str]
    serie: list[tuple[int, float | None]] | None = None


class Contexto(BaseModel):
    id_noticia: str | None
    version_reglas: str
    vinculos: list[Vinculo]
    pendientes: list[str]  # relaciones mencionadas sin evidencia oficial que las respalde


# --- Plantillas -----------------------------------------------------------------------


def formatear_valor(valor: float) -> str:
    if abs(valor) >= 1000:
        return f"{valor:,.0f}"
    return f"{round(valor, 2):g}"


def citar_indicador(indicador: Indicador, nombre: str) -> str:
    """Cita por plantilla. Nunca usa 'hoy' ni 'actual': el dato es anual e histórico."""
    pais = PAISES.get(indicador.pais_iso3, indicador.pais_iso3)
    unidad = indicador.unidad or "unidad no indicada"
    if indicador.valor is None:
        cifra = "sin dato publicado"
    else:
        cifra = f"{formatear_valor(indicador.valor)} ({unidad})"
    return (
        f"{nombre}, {pais}, {indicador.anio}: {cifra}. "
        f"Dato anual de {indicador.anio}, no una medición actual. "
        f"Fuente: Banco Mundial, indicador {indicador.indicador_id}, "
        f"extraído el {indicador.fecha_extraccion:%Y-%m-%d}."
    )


def _hora(fecha: datetime) -> str:
    local = fecha.astimezone(ZONA_PANAMA)
    return f"{fecha:%Y-%m-%d %H:%M} UTC ({local:%Y-%m-%d %H:%M} hora de Panamá)"


def citar_evento(evento: Evento) -> str:
    lugar = evento.place or "ubicación no indicada"
    profundidad = f"{formatear_valor(evento.depth)} km" if evento.depth is not None else "sin dato"
    return (
        f"Sismo de magnitud {evento.magnitude} registrado por USGS el {_hora(evento.time)}: "
        f"{lugar}; profundidad {profundidad}. Evento {evento.id}."
    )


# --- Vinculación ----------------------------------------------------------------------


class ContextoOficial:
    def __init__(
        self,
        indicadores: list[Indicador],
        eventos: list[Evento],
        reglas: ReglasContexto | None = None,
    ) -> None:
        self.reglas = reglas or load_reglas_contexto()
        self._indicadores: dict[tuple[str, str], dict[int, Indicador]] = {}
        for i in indicadores:
            self._indicadores.setdefault((i.pais_iso3, i.indicador_id), {})[i.anio] = i
        self._eventos = sorted(eventos, key=lambda e: e.time)

    @classmethod
    def desde_duckdb(cls, ruta: Path, reglas: ReglasContexto | None = None) -> "ContextoOficial":
        with duckdb.connect(str(ruta), read_only=True) as con:

            def filas(tabla: str) -> list[dict]:
                cursor = con.execute(f"SELECT * FROM {tabla}")
                columnas = [c[0] for c in cursor.description]
                return [dict(zip(columnas, fila, strict=True)) for fila in cursor.fetchall()]

            indicadores = [Indicador.model_validate(f) for f in filas("indicadores")]
            eventos = [Evento.model_validate(f) for f in filas("eventos")]
        return cls(indicadores, eventos, reglas)

    def serie(self, indicador_id: str, pais: str | None = None) -> dict[int, Indicador]:
        return self._indicadores.get((pais or self.reglas.pais, indicador_id), {})

    def _vinculo_indicador(
        self, indicador_id: str, regla: ReglaIndicador, palabra: str
    ) -> Vinculo | str:
        pais = self.reglas.pais
        por_anio = self.serie(indicador_id, pais)
        con_valor = sorted(a for a, i in por_anio.items() if i.valor is not None)
        if not con_valor:
            return (
                f"El titular menciona '{palabra}', pero el Banco Mundial no tiene dato de "
                f"{regla.nombre} ({indicador_id}) para {PAISES.get(pais, pais)} en el snapshot."
            )
        ultimo = con_valor[-1]
        dato = por_anio[ultimo]
        limitaciones = [
            f"Dato anual de {ultimo}: describe ese año, no la situación de hoy.",
            "Los datos del Banco Mundial pueden revisarse en publicaciones posteriores.",
        ]
        posteriores = sorted(a for a in por_anio if a > ultimo)
        if posteriores:
            anios = ", ".join(str(a) for a in posteriores)
            limitaciones.append(
                f"Sin dato publicado para {anios}; el último disponible es {ultimo}."
            )
        desde = ultimo - self.reglas.anios_serie
        serie = [(a, por_anio[a].valor) for a in sorted(por_anio) if desde <= a <= max(por_anio)]
        return Vinculo(
            tipo="indicador",
            id_evidencia=f"BM:{pais}:{indicador_id}:{ultimo}",
            campo="valor",
            cita=citar_indicador(dato, regla.nombre),
            regla=f"El titular menciona '{palabra}' ({self.reglas.version}).",
            limitaciones=limitaciones,
            serie=serie,
        )

    def _vinculos_usgs(self, palabra: str, fecha: datetime | None) -> list[Vinculo] | str:
        usgs = self.reglas.usgs
        if fecha is None:
            return (
                f"El titular menciona '{palabra}', pero la noticia no tiene fecha: no se puede "
                "ubicar un evento en el catálogo USGS."
            )
        inicio = fecha - timedelta(hours=usgs.horas_antes)
        fin = fecha + timedelta(hours=usgs.horas_despues)
        candidatos = [e for e in self._eventos if inicio <= e.time <= fin]
        if not candidatos:
            return (
                f"El titular menciona '{palabra}', pero USGS no registra sismos (M≥3, caja "
                f"regional) entre {inicio:%Y-%m-%d %H:%M} y {fin:%Y-%m-%d %H:%M} UTC. "
                "Verificar con otra fuente."
            )
        vinculos = []
        for evento in sorted(candidatos, key=lambda e: -e.magnitude):
            limitaciones = [usgs.limitacion.strip()]
            if evento.status != "reviewed":
                limitaciones.append(
                    f"Estado USGS '{evento.status or 'sin dato'}': el evento no ha sido "
                    "revisado por un sismólogo y sus datos pueden cambiar."
                )
            limitaciones.append(
                "Coincidencia por fecha: confirmar que la noticia se refiere a este evento."
            )
            vinculos.append(
                Vinculo(
                    tipo="evento_sismico",
                    id_evidencia=f"USGS:{evento.id}",
                    campo="magnitude, time, place",
                    cita=citar_evento(evento),
                    regla=(
                        f"El titular menciona '{palabra}' y el evento ocurrió entre "
                        f"{usgs.horas_antes} h antes y {usgs.horas_despues} h después de la "
                        f"noticia ({self.reglas.version})."
                    ),
                    limitaciones=limitaciones,
                )
            )
        return vinculos

    def vincular(
        self, texto: str, fecha: datetime | None, id_noticia: str | None = None
    ) -> Contexto:
        """Vincula un texto (titular o resumen) con evidencia oficial. Si no hay relación
        sustentada, no se vincula."""
        vinculos: list[Vinculo] = []
        pendientes: list[str] = []
        for indicador_id, regla in self.reglas.indicadores.items():
            if palabra := buscar_palabra(texto, regla.palabras_clave):
                resultado = self._vinculo_indicador(indicador_id, regla, palabra)
                if isinstance(resultado, str):
                    pendientes.append(resultado)
                else:
                    vinculos.append(resultado)
        if palabra := buscar_palabra(texto, self.reglas.usgs.palabras_clave):
            resultado = self._vinculos_usgs(palabra, fecha)
            if isinstance(resultado, str):
                pendientes.append(resultado)
            else:
                vinculos.extend(resultado)
        return Contexto(
            id_noticia=id_noticia,
            version_reglas=self.reglas.version,
            vinculos=vinculos,
            pendientes=pendientes,
        )

    def para_noticia(self, noticia: Noticia) -> Contexto:
        fecha = noticia.fecha_publicacion or noticia.fecha_deteccion
        return self.vincular(noticia.titulo, fecha, noticia.id_noticia)
