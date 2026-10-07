"""Configuración central leída desde variables de entorno y .env.

Las claves se guardan como SecretStr para que nunca aparezcan en logs,
reprs ni capturas de la interfaz.
"""

from datetime import datetime
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from faro_editorial.contrato import parse_fecha_utc

# src/faro_editorial/settings.py -> parents[2] es la raíz del repositorio.
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Seguro por defecto: sin .env, la app funciona solo con caché local.
    offline: bool = True

    openrouter_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    jev_model: str = "typesafe/jev-1.13"
    jev_url: str = "https://openrouter.ai/api/alpha/decisions"
    llm_model: str = ""
    llm_base_url: str = "https://openrouter.ai/api/v1"
    embedding_model: str = "intfloat/multilingual-e5-small"
    # Tiempo máximo por llamada a Jev o al LLM; si se agota, la decisión es una abstención.
    ia_timeout_s: float = 30.0
    
    data_dir: Path = ROOT_DIR / "data"
    rules_path: Path = ROOT_DIR / "config" / "rules_v1.yaml"
    display_timezone: str = "America/Panama"

    # Ventana [desde, hasta) de noticias y sismos para la demo: octubre 2025 a septiembre
    # 2026, confirmada por la organización; los límites van en hora de Panamá (decisión del
    # equipo, issue #3). Para el conjunto de desarrollo se desactiva dejándola vacía en .env.
    ventana_desde: datetime | None = datetime.fromisoformat("2025-10-01T00:00:00-05:00")
    ventana_hasta: datetime | None = datetime.fromisoformat("2026-10-01T00:00:00-05:00")

    @field_validator("ventana_desde", "ventana_hasta", mode="before")
    @classmethod
    def _fecha_utc(cls, valor: object) -> datetime | None:
        return parse_fecha_utc(valor)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def has_openrouter_key(self) -> bool:
        return bool(self.openrouter_api_key and self.openrouter_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
