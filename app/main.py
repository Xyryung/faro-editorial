"""Interfaz Streamlit de Faro Editorial.

Ejecutar desde la raíz del repo:  uv run streamlit run app/main.py
"""

import streamlit as st

from faro_editorial import __version__
from faro_editorial.rules import load_rules
from faro_editorial.settings import get_settings

st.set_page_config(page_title="Faro Editorial", layout="wide")

settings = get_settings()
reglas = load_rules(settings.rules_path)

st.title("Faro Editorial")
st.caption("Copiloto de inteligencia informativa para TVN Media · hackIAthon")

col1, col2, col3 = st.columns(3)
col1.metric("Versión", __version__)
col2.metric("Reglas", reglas.version)
col3.metric("Modo", "Offline (caché)" if settings.offline else "En línea")

st.info(
    "Esqueleto inicial. La bandeja priorizada, las fichas de evidencia y los "
    "borradores se construyen durante el evento."
)

with st.expander("Pesos del puntaje de atención"):
    st.table({"Componente": list(reglas.pesos), "Peso": list(reglas.pesos.values())})

with st.expander("Configuración (sin secretos)"):
    st.write(
        {
            "Modelo Jev": settings.jev_model,
            "Modelo LLM": settings.llm_model or "(sin definir)",
            "Modelo de embeddings": settings.embedding_model,
            "Clave de OpenRouter configurada": settings.has_openrouter_key,
            "Zona horaria de la interfaz": settings.display_timezone,
        }
    )
