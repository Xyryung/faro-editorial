"""Modelos de config/extraccion_v1.yaml. Un error de configuración falla al cargar, antes
de hacer cualquier llamada a la red."""

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from faro_editorial.settings import ROOT_DIR

RUTA_CONFIG = ROOT_DIR / "config" / "extraccion_v1.yaml"

Perfil = Literal["demo", "entrenamiento"]
PERFILES: tuple[Perfil, ...] = ("demo", "entrenamiento")


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FuenteWeb(_Modelo):
    id: str
    tipo: Literal["rss", "sitemap", "sitemap_mensual"]
    medio: str
    origen: str
    url: str
    idioma: str = "es"  # si el feed o el sitemap no lo declaran
    perfiles: list[Perfil] = Field(default_factory=lambda: list(PERFILES))
    # Fecha en un formato no estándar (strptime), p. ej. "%m/%d/%Y - %H:%M". Se prueba
    # también sin el día de la semana inicial ("Wed, ").
    formato_fecha: str | None = None
    # Zona de las fechas que no la declaran (solo con formato_fecha). Ej.: America/Panama.
    zona_horaria: str | None = None
    # URLs cuya ruta empieza con alguno de estos prefijos se omiten (secciones sin interés
    # editorial, p. ej. deportes o espectáculos).
    excluir_rutas: list[str] = Field(default_factory=list)
    # sitemap_mensual: usar solo los últimos N meses de la ventana.
    meses_maximos: int | None = None
    # sitemap sin news:publication_date: usar lastmod como fecha de publicación aproximada,
    # solo si cae dentro del mes del sitemap (si no, la fila queda sin fecha).
    fecha_lastmod: bool = False

    @field_validator("zona_horaria")
    @classmethod
    def _zona_valida(cls, zona: str | None) -> str | None:
        if zona is not None:
            try:
                ZoneInfo(zona)  # falla al cargar la configuración si la zona no existe
            except (ZoneInfoNotFoundError, ValueError) as e:
                raise ValueError(f"zona horaria desconocida: {zona!r}") from e
        return zona

    @model_validator(mode="after")
    def _plantilla(self) -> "FuenteWeb":
        tiene_plantilla = "{anio" in self.url and "{mes" in self.url
        if (self.tipo == "sitemap_mensual") != tiene_plantilla:
            raise ValueError(
                f"{self.id}: solo un sitemap_mensual usa una URL con {{anio}} y {{mes}}"
            )
        return self


class ConfigGdelt(_Modelo):
    url: str
    pausa_s: float = 5.0
    dias_por_tramo: float = 3.0
    horas_minimas_tramo: float = 6.0
    max_dias_atras: int = 88
    consultas: list[str]
    perfiles: list[Perfil] = Field(default_factory=lambda: list(PERFILES))


class ConfigBancoMundial(_Modelo):
    url: str
    paises: list[str]
    anio_desde: int
    anio_hasta: int
    indicadores: dict[str, str]  # ID -> unidad
    licencia: str


class ConfigUSGS(_Modelo):
    url: str
    # Vacíos = mismo período que las noticias (data/CONTRATO.md). Con valor, período fijo.
    inicio: str | None = None
    fin: str | None = None
    min_latitud: float
    max_latitud: float
    min_longitud: float
    max_longitud: float
    min_magnitud: float
    tipo_evento: str | None = None


class ConfigExtraccion(_Modelo):
    version: str
    user_agent: str
    timeout_s: float = 30.0
    pausa_s: float = 1.0
    reintentos: int = 2
    espera_429_s: float = 20.0
    max_bytes: int = 25_000_000
    dias_por_defecto: int = 30  # demo, solo si .env no define VENTANA_DESDE/VENTANA_HASTA
    dias_entrenamiento: int = 90  # perfil entrenamiento sin --desde
    medios: dict[str, str] = Field(default_factory=dict)
    web: list[FuenteWeb] = Field(default_factory=list)
    gdelt: ConfigGdelt | None = None
    banco_mundial: ConfigBancoMundial | None = None
    usgs: ConfigUSGS | None = None

    @model_validator(mode="after")
    def _ids_unicos(self) -> "ConfigExtraccion":
        ids = [f.id for f in self.web]
        repetidos = sorted({i for i in ids if ids.count(i) > 1})
        if repetidos:
            raise ValueError(f"IDs de fuente repetidos: {', '.join(repetidos)}")
        return self


def cargar_config(ruta: Path = RUTA_CONFIG) -> ConfigExtraccion:
    with Path(ruta).open(encoding="utf-8") as f:
        return ConfigExtraccion.model_validate(yaml.safe_load(f))
