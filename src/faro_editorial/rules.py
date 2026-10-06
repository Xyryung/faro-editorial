"""Carga y valida las reglas versionadas del puntaje de atención (config/rules_*.yaml).

P = 30R + 25I + 20U + 15N + 10E, con cada componente normalizado a 0-1.
Rangos sin solapamiento: bajo [0,40), medio [40,70), alto [70,100].
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

COMPONENTES = ("R", "I", "U", "N", "E")


class Rango(BaseModel):
    min: float
    max: float


class Reglas(BaseModel):
    version: str
    descripcion: str
    pesos: dict[str, int]
    rangos: dict[str, Rango]
    desempate: list[str]
    temas: list[str]
    estados_evidencia: list[str]
    estados_revision: list[str]

    @model_validator(mode="after")
    def _validar(self) -> "Reglas":
        if set(self.pesos) != set(COMPONENTES):
            raise ValueError(f"Los pesos deben cubrir exactamente {COMPONENTES}")
        if sum(self.pesos.values()) != 100:
            raise ValueError("Los pesos deben sumar 100")
        if list(self.rangos) != ["bajo", "medio", "alto"]:
            raise ValueError("Los rangos deben ser, en orden: bajo, medio, alto")
        bajo, medio, alto = self.rangos["bajo"], self.rangos["medio"], self.rangos["alto"]
        contiguos = bajo.max == medio.min and medio.max == alto.min
        if bajo.min != 0 or alto.max != 100 or not contiguos:
            raise ValueError("Los rangos deben cubrir 0-100 sin huecos ni solapamientos")
        return self

    def banda(self, puntaje: float) -> str:
        """Devuelve 'bajo', 'medio' o 'alto'. El límite superior de cada rango es abierto,
        salvo el de 'alto', que incluye 100."""
        if not 0 <= puntaje <= 100:
            raise ValueError(f"Puntaje fuera de rango: {puntaje}")
        for nombre, rango in self.rangos.items():
            if rango.min <= puntaje < rango.max:
                return nombre
        return "alto"


def load_rules(path: Path) -> Reglas:
    with Path(path).open(encoding="utf-8") as f:
        return Reglas.model_validate(yaml.safe_load(f))
