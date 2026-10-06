"""Contrato de datos del snapshot "Panamá · Señales y Evidencias v1" (sección 7 del reto).

Reglas de integridad que aplican estos modelos:
- Fechas en UTC. Se aceptan ISO 8601 (con o sin zona; sin zona se asume UTC), el formato
  compacto de GDELT (20250915T143000Z), RFC 2822 del RSS y milisegundos epoch de USGS.
- Los nulos se conservan: un campo vacío es None, nunca 0 ni "".
- fecha_publicacion y fecha_deteccion (seendate de GDELT) son campos distintos.
- Las URLs se validan pero no se normalizan, para no alterar IDs ni deduplicación.
"""

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Annotated, Any
from urllib.parse import urlparse

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

VERSION_CONTRATO = "contrato-v1"

# Valores que significan "sin dato" en los archivos de origen.
TOKENS_NULOS = {"", "na", "n/a", "nan", "null", "none"}

_GDELT_COMPACTO = re.compile(r"^\d{8}T\d{6}Z$")


def es_nulo(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and valor.strip().lower() in TOKENS_NULOS)


def _texto_requerido(valor: Any) -> Any:
    if es_nulo(valor):
        raise ValueError("campo obligatorio vacío")
    return valor.strip() if isinstance(valor, str) else valor


def _texto_opcional(valor: Any) -> Any:
    if es_nulo(valor):
        return None
    return valor.strip() if isinstance(valor, str) else valor


def _numero_opcional(valor: Any) -> Any:
    return None if es_nulo(valor) else valor


def parse_fecha_utc(valor: Any) -> datetime | None:
    """Convierte cualquier formato de fecha aceptado a datetime con zona UTC."""
    if es_nulo(valor):
        return None
    if isinstance(valor, datetime):
        fecha = valor
    elif isinstance(valor, int | float) and not isinstance(valor, bool):
        fecha = datetime.fromtimestamp(valor / 1000, tz=UTC)  # USGS: ms desde epoch
    elif isinstance(valor, str):
        texto = valor.strip()
        if _GDELT_COMPACTO.match(texto):
            fecha = datetime.strptime(texto, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        else:
            try:
                fecha = datetime.fromisoformat(texto.replace("Z", "+00:00"))
            except ValueError:
                try:
                    fecha = parsedate_to_datetime(texto)
                except (TypeError, ValueError):
                    raise ValueError(f"fecha inválida: {texto!r}") from None
    else:
        raise ValueError(f"fecha inválida: {valor!r}")
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=UTC)
    return fecha.astimezone(UTC)


def _fecha_requerida(valor: Any) -> datetime:
    fecha = parse_fecha_utc(valor)
    if fecha is None:
        raise ValueError("campo obligatorio vacío")
    return fecha


def _url(valor: Any) -> str:
    valor = _texto_requerido(valor)
    partes = urlparse(valor)
    if partes.scheme not in ("http", "https") or not partes.netloc:
        raise ValueError(f"URL inválida: {valor!r}")
    return valor


def _url_opcional(valor: Any) -> str | None:
    return None if es_nulo(valor) else _url(valor)


TextoRequerido = Annotated[str, BeforeValidator(_texto_requerido)]
TextoOpcional = Annotated[str | None, BeforeValidator(_texto_opcional)]
FechaUTC = Annotated[datetime, BeforeValidator(_fecha_requerida)]
FechaUTCOpcional = Annotated[datetime | None, BeforeValidator(parse_fecha_utc)]
Url = Annotated[str, BeforeValidator(_url)]
UrlOpcional = Annotated[str | None, BeforeValidator(_url_opcional)]
FloatOpcional = Annotated[float | None, BeforeValidator(_numero_opcional)]


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Noticia(_Modelo):
    """Una fila de noticias.csv (TVN RSS o GDELT DOC 2.0)."""

    id_noticia: TextoRequerido
    titulo: TextoRequerido
    url: Url
    medio: TextoRequerido
    idioma: TextoOpcional = None
    fecha_publicacion: FechaUTCOpcional = None
    fecha_deteccion: FechaUTCOpcional = None  # seendate de GDELT: detección, no publicación
    fecha_extraccion: FechaUTC
    tema: TextoOpcional = None
    origen: TextoRequerido
    alcance_texto: TextoOpcional = None

    @model_validator(mode="after")
    def _fechas_coherentes(self) -> "Noticia":
        for campo in ("fecha_publicacion", "fecha_deteccion"):
            fecha = getattr(self, campo)
            if fecha is not None and fecha > self.fecha_extraccion:
                raise ValueError(f"{campo} posterior a fecha_extraccion")
        return self


class Indicador(_Modelo):
    """Una fila de indicadores.csv (Banco Mundial). valor es None si no hay observación."""

    pais_iso3: Annotated[str, Field(pattern=r"^[A-Z]{3}$"), BeforeValidator(_texto_requerido)]
    indicador_id: TextoRequerido
    anio: Annotated[int, Field(ge=1900, le=2100), BeforeValidator(_texto_requerido)]
    valor: FloatOpcional = None
    unidad: TextoOpcional = None
    fuente_url: Url
    fecha_extraccion: FechaUTC
    licencia: TextoRequerido


class Evento(_Modelo):
    """Un sismo de eventos.geojson (USGS), aplanado desde la Feature GeoJSON."""

    id: TextoRequerido
    magnitude: float
    time: FechaUTC
    updated: FechaUTCOpcional = None
    longitude: Annotated[float, Field(ge=-180, le=180)]
    latitude: Annotated[float, Field(ge=-90, le=90)]
    depth: FloatOpcional = None
    place: TextoOpcional = None
    status: TextoOpcional = None
    url: UrlOpcional = None

    @classmethod
    def desde_feature(cls, feature: dict[str, Any]) -> "Evento":
        """Aplana una Feature de USGS: propiedades + geometry.coordinates [lon, lat, prof]."""
        props = feature.get("properties") or {}
        coords = list((feature.get("geometry") or {}).get("coordinates") or [])
        coords += [None] * (3 - len(coords))
        return cls.model_validate(
            {
                "id": feature.get("id"),
                "magnitude": props.get("mag"),
                "time": props.get("time"),
                "updated": props.get("updated"),
                "longitude": coords[0],
                "latitude": coords[1],
                "depth": coords[2],
                "place": props.get("place"),
                "status": props.get("status"),
                "url": props.get("url"),
            }
        )


class ArchivoManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    sha256: Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]
    cantidad: int | None = None
    licencia: str | None = None


class Manifest(BaseModel):
    """manifest.json del snapshot. Se toleran campos adicionales de la organización."""

    model_config = ConfigDict(extra="allow")

    version: str
    fecha_corte_utc: FechaUTC
    archivos: dict[str, ArchivoManifest]
    consultas: list[Any] | dict[str, Any] | None = None
    transformaciones: list[Any] | str | None = None
