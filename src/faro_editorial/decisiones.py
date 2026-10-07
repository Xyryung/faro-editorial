"""Capa de decisiones intercambiable con caché (issue #8, T10).

Todo llamado a Jev o al LLM pasa por ClienteDecisiones:

    proveedor (Jev, LLM o simulado) -> ClienteDecisiones (caché + registro) -> resto del sistema

- Las preguntas usan los tres tipos de Jev: Choice (elegir una opción), Score (nivel en una
  escala ordenada) y Noul (probabilidad de que una afirmación sea verdadera). Cambiar de
  proveedor no cambia las preguntas ni el código que usa las respuestas.
- Clave de caché: SHA-256 de proveedor + modelo + versión de prompt + estado + preguntas.
  Cambiar el texto de una pregunta cambia la clave aunque se olvide subir la versión.
- OFFLINE=1: solo se lee la caché. Si falta una respuesta, la decisión es una abstención
  explícita con su motivo; nunca una excepción. Lo mismo ocurre si el proveedor falla o
  devuelve una respuesta que no cumple las preguntas: se abstiene y no se guarda en caché.
- Cada llamada en vivo (exitosa o fallida) se registra en registro_llamadas.jsonl con
  proveedor, modelo, versión de prompt, costo, tokens y latencia. Nunca se registran claves.
"""

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

NOMBRE_REGISTRO = "registro_llamadas.jsonl"

# El estado es lo que se evalúa: texto o un objeto JSON (Jev acepta ambos).
Estado = str | dict[str, Any]


# --- Preguntas ------------------------------------------------------------------------


class _Modelo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PreguntaChoice(_Modelo):
    """Elegir exactamente una opción. `opciones` es clave -> descripción (criteria de Jev)."""

    tipo: Literal["choice"] = "choice"
    instrucciones: str
    opciones: dict[str, str]

    @field_validator("opciones")
    @classmethod
    def _al_menos_dos(cls, opciones: dict[str, str]) -> dict[str, str]:
        if len(opciones) < 2:
            raise ValueError("una pregunta choice necesita al menos dos opciones")
        return opciones


class PreguntaScore(_Modelo):
    """Ubicar el estado en una escala ordenada. `niveles[0]` es el nivel más bajo."""

    tipo: Literal["score"] = "score"
    instrucciones: str
    niveles: list[str]

    @field_validator("niveles")
    @classmethod
    def _entre_dos_y_diez(cls, niveles: list[str]) -> list[str]:
        if not 2 <= len(niveles) <= 10:  # Jev admite como máximo 10 niveles
            raise ValueError("una pregunta score necesita entre 2 y 10 niveles")
        return niveles


class PreguntaNoul(_Modelo):
    """Afirmación de sí/no. La respuesta es la probabilidad de que sea verdadera."""

    tipo: Literal["noul"] = "noul"
    instrucciones: str
    criterio_si: str | None = None
    criterio_no: str | None = None


Pregunta = Annotated[PreguntaChoice | PreguntaScore | PreguntaNoul, Field(discriminator="tipo")]


# --- Respuestas -----------------------------------------------------------------------


class RespuestaChoice(_Modelo):
    tipo: Literal["choice"] = "choice"
    opcion: str
    # None cuando el proveedor no entrega probabilidades (por ejemplo, el LLM).
    probabilidades: dict[str, float] | None = None
    confianza: float | None = None


class RespuestaScore(_Modelo):
    tipo: Literal["score"] = "score"
    valor: float  # posición ponderada por probabilidad: 0 .. niveles - 1
    niveles: int
    probabilidades: dict[str, float] | None = None
    confianza: float | None = None

    @property
    def nivel(self) -> int:
        """Nivel más cercano a la posición ponderada."""
        return round(self.valor)

    @property
    def normalizado(self) -> float:
        """Posición en la escala llevada a 0-1, lista para el motor de puntaje."""
        return self.valor / (self.niveles - 1)


class RespuestaNoul(_Modelo):
    tipo: Literal["noul"] = "noul"
    probabilidad: float  # probabilidad de "sí", entre 0 y 1


Respuesta = Annotated[RespuestaChoice | RespuestaScore | RespuestaNoul, Field(discriminator="tipo")]

_PREGUNTAS = TypeAdapter(dict[str, Pregunta])


class ResultadoProveedor(BaseModel):
    """Lo que devuelve un proveedor tras una llamada en vivo."""

    respuestas: dict[str, Respuesta]
    modelo_servido: str | None = None  # versión exacta que respondió (p. ej. con fecha)
    costo_usd: float | None = None
    tokens_entrada: int | None = None
    tokens_salida: int | None = None
    id_generacion: str | None = None


class Proveedor(Protocol):
    """Interfaz común: Jev, el LLM y el simulado de las pruebas la cumplen."""

    nombre: str
    modelo: str

    def decidir(self, estado: Estado, preguntas: dict[str, Pregunta]) -> ResultadoProveedor: ...


# --- Decisión -------------------------------------------------------------------------


class Decision(BaseModel):
    """Resultado que recibe el resto del sistema: respuestas o una abstención explícita."""

    respuestas: dict[str, Respuesta] = Field(default_factory=dict)
    abstencion: bool = False
    motivo: str | None = None
    proveedor: str
    modelo: str
    modelo_servido: str | None = None
    version_prompt: str
    clave: str
    desde_cache: bool = False
    # Costo de producir la respuesta. Una lectura de caché conserva el costo original;
    # el gasto real está en el registro de llamadas en vivo.
    costo_usd: float | None = None
    tokens_entrada: int | None = None
    tokens_salida: int | None = None
    latencia_s: float | None = None
    creado_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def respuesta(self, clave: str) -> Respuesta | None:
        return self.respuestas.get(clave)


def validar_preguntas(preguntas: Mapping[str, Pregunta | dict[str, Any]]) -> dict[str, Pregunta]:
    """Acepta modelos o diccionarios y devuelve preguntas validadas."""
    if not preguntas:
        raise ValueError("se necesita al menos una pregunta")
    return _PREGUNTAS.validate_python(dict(preguntas))


def clave_cache(
    proveedor: str,
    modelo: str,
    version_prompt: str,
    estado: Estado,
    preguntas: dict[str, Pregunta],
) -> str:
    contenido = {
        "proveedor": proveedor,
        "modelo": modelo,
        "version_prompt": version_prompt,
        "estado": estado,
        "preguntas": {k: p.model_dump(mode="json") for k, p in preguntas.items()},
    }
    # default=str: un estado con fechas u otros objetos (p. ej. noticia.model_dump()) da una
    # clave estable en vez de lanzar TypeError fuera del manejo de errores del cliente.
    texto = json.dumps(
        contenido, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def errores_de_respuesta(
    preguntas: dict[str, Pregunta], respuestas: Mapping[str, Respuesta]
) -> list[str]:
    """Comprueba que cada pregunta tenga una respuesta coherente con su tipo y opciones."""
    errores = []
    for clave, pregunta in preguntas.items():
        r = respuestas.get(clave)
        if r is None:
            errores.append(f"{clave}: sin respuesta")
        elif r.tipo != pregunta.tipo:
            errores.append(f"{clave}: se esperaba {pregunta.tipo} y llegó {r.tipo}")
        elif isinstance(pregunta, PreguntaChoice) and r.opcion not in pregunta.opciones:
            errores.append(f"{clave}: opción desconocida {r.opcion!r}")
        elif isinstance(pregunta, PreguntaScore) and not (
            r.niveles == len(pregunta.niveles) and 0 <= r.valor <= r.niveles - 1
        ):
            errores.append(f"{clave}: valor {r.valor} fuera de la escala de {r.niveles} niveles")
        elif isinstance(r, RespuestaNoul) and not 0 <= r.probabilidad <= 1:
            errores.append(f"{clave}: probabilidad {r.probabilidad} fuera de 0-1")
    extra = sorted(set(respuestas) - set(preguntas))
    if extra:
        errores.append(f"respuestas no pedidas: {', '.join(extra)}")
    return errores


# --- Caché ----------------------------------------------------------------------------


class CacheDecisiones:
    """Un archivo JSON por clave en <directorio>/<2 primeros caracteres>/<clave>.json."""

    def __init__(self, directorio: Path) -> None:
        self.directorio = Path(directorio)

    def ruta(self, clave: str) -> Path:
        return self.directorio / clave[:2] / f"{clave}.json"

    def leer(self, clave: str) -> Decision | None:
        ruta = self.ruta(clave)
        if not ruta.exists():
            return None
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            return Decision.model_validate(datos["decision"])
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError):
            return None  # archivo dañado: se trata como ausente y se vuelve a pedir

    def guardar(self, decision: Decision, solicitud: dict[str, Any]) -> None:
        ruta = self.ruta(decision.clave)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        contenido = {"decision": decision.model_dump(mode="json"), "solicitud": solicitud}
        temporal = ruta.with_suffix(".tmp")
        temporal.write_text(
            json.dumps(contenido, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        os.replace(temporal, ruta)  # escritura atómica: nunca queda un JSON a medias


# --- Cliente --------------------------------------------------------------------------


class ClienteDecisiones:
    """Punto único de acceso a decisiones de IA, con caché, modo offline y registro."""

    def __init__(
        self,
        proveedor: Proveedor,
        directorio_cache: Path,
        offline: bool,
        reloj: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.proveedor = proveedor
        self.offline = offline
        self.cache = CacheDecisiones(Path(directorio_cache) / "decisiones")
        self.ruta_registro = Path(directorio_cache) / NOMBRE_REGISTRO
        self._reloj = reloj

    def decidir(
        self,
        estado: Estado,
        preguntas: Mapping[str, Pregunta | dict[str, Any]],
        version_prompt: str,
    ) -> Decision:
        preguntas = validar_preguntas(preguntas)
        p = self.proveedor
        clave = clave_cache(p.nombre, p.modelo, version_prompt, estado, preguntas)

        guardada = self.cache.leer(clave)
        if guardada is not None:
            return guardada.model_copy(update={"desde_cache": True})

        base = {
            "proveedor": p.nombre,
            "modelo": p.modelo,
            "version_prompt": version_prompt,
            "clave": clave,
        }
        if self.offline:
            return Decision(
                **base,
                abstencion=True,
                motivo="Modo offline: no hay respuesta guardada en caché para esta consulta.",
            )

        inicio = self._reloj()
        try:
            resultado = p.decidir(estado, preguntas)
        except Exception as e:  # cualquier fallo del proveedor termina en abstención
            latencia = self._reloj() - inicio
            error = f"{type(e).__name__}: {str(e)[:300]}"
            self._registrar(base, latencia, ok=False, error=error)
            return Decision(
                **base,
                abstencion=True,
                motivo=f"El proveedor falló ({type(e).__name__}); no se inventa una respuesta.",
                latencia_s=latencia,
            )
        latencia = self._reloj() - inicio

        metricas = {
            "modelo_servido": resultado.modelo_servido,
            "costo_usd": resultado.costo_usd,
            "tokens_entrada": resultado.tokens_entrada,
            "tokens_salida": resultado.tokens_salida,
            "latencia_s": latencia,
        }
        errores = errores_de_respuesta(preguntas, resultado.respuestas)
        if errores:
            self._registrar(base, latencia, ok=False, error="; ".join(errores), **metricas)
            return Decision(
                **base,
                abstencion=True,
                motivo="Respuesta inválida del proveedor: " + "; ".join(errores),
                **metricas,
            )

        decision = Decision(**base, respuestas=dict(resultado.respuestas), **metricas)
        solicitud = {
            "estado": estado,
            "preguntas": {k: v.model_dump(mode="json") for k, v in preguntas.items()},
            "id_generacion": resultado.id_generacion,
        }
        self.cache.guardar(decision, solicitud)
        self._registrar(base, latencia, ok=True, **metricas)
        return decision

    def _registrar(self, base: dict[str, Any], latencia: float, **campos: Any) -> None:
        campos.pop("latencia_s", None)
        fila = {
            "ts_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            **base,
            "latencia_s": round(latencia, 4),
            **campos,
        }
        self.ruta_registro.parent.mkdir(parents=True, exist_ok=True)
        with self.ruta_registro.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")
