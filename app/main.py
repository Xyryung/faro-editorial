"""Interfaz Streamlit de Faro Editorial: bandeja priorizada y ficha de evidencia (issue #16).

Ejecutar desde la raíz del repo:  uv run streamlit run app/main.py

Lee data/processed/bandeja.json (o lo genera desde la base cargada). Funciona sin internet.
"""

import html
from pathlib import Path

import streamlit as st

from faro_editorial import __version__
from faro_editorial.interfaz import (
    ETIQUETAS_BANDA,
    ETIQUETAS_ESTADO,
    accion_recomendada,
    cargar_bandeja,
    escapar_md,
    etiquetas_html,
    filas_bandeja,
    filas_componentes,
    filtrar,
    resumen_html,
)
from faro_editorial.rules import load_rules
from faro_editorial.settings import get_settings

st.set_page_config(page_title="Faro Editorial", layout="wide")

settings = get_settings()
reglas = load_rules(settings.rules_path)

# Estilo propio (los colores base están en .streamlit/config.toml).
estilo = (Path(__file__).parent / "estilo.css").read_text(encoding="utf-8")
st.markdown(f"<style>{estilo}</style>", unsafe_allow_html=True)


def mostrar_configuracion() -> None:
    with st.expander("Pesos del puntaje de atención"):
        st.table({"Componente": list(reglas.pesos), "Peso": list(reglas.pesos.values())})
    with st.expander("Configuración (sin secretos)"):
        st.write(
            {
                "Versión": __version__,
                "Reglas": reglas.version,
                "Modo": "Offline (caché)" if settings.offline else "En línea",
                "Modelo Jev": settings.jev_model,
                "Modelo LLM": settings.llm_model or "(sin definir)",
                "Modelo de embeddings": settings.embedding_model,
                "Clave de OpenRouter configurada": settings.has_openrouter_key,
                "Zona horaria de la interfaz": settings.display_timezone,
            }
        )


def tarjeta(clave: str):
    """Contenedor con aspecto de tarjeta (el estilo de .st-key-tarjeta_* está en estilo.css)."""
    return st.container(key=f"tarjeta_{clave}")


def mostrar_ficha(tema: dict) -> None:
    with tarjeta("ficha"):
        st.subheader(escapar_md(tema["titulo"]))
        st.markdown(etiquetas_html(tema), unsafe_allow_html=True)
        # T03: la fecha original siempre a la vista, para no presentar algo viejo como nuevo.
        fecha = html.escape(tema["fecha_original_panama"] or "sin fecha")
        st.markdown(
            f'<div class="meta">Fecha original <b>{fecha}</b> · hora de Panamá</div>',
            unsafe_allow_html=True,
        )
        st.markdown(f'<div class="nota">{html.escape(tema["aviso"])}</div>', unsafe_allow_html=True)

    izquierda, derecha = st.columns([3, 2], gap="medium")
    with izquierda:
        with tarjeta("reporta"):
            st.markdown("#### Qué se reporta")
            for n in tema["noticias"]:
                publicada = n["fecha_publicacion_panama"] or "sin fecha"
                detectada = (
                    f" · detectada {n['fecha_deteccion_panama']}"
                    if n["fecha_deteccion_panama"]
                    else ""
                )
                url = n["url"].replace(")", "%29")
                st.markdown(
                    f"- **{escapar_md(n['titulo'])}**  \n"
                    f"  {escapar_md(n['medio'])} · publicada {publicada}{detectada}"
                    f" · [abrir]({url})"
                )
            if any(n.get("alcance_texto") != "titular_descripcion" for n in tema["noticias"]):
                st.caption("Parte de este tema se basa únicamente en titular y metadatos.")

        with tarjeta("quien"):
            st.markdown("#### Quién lo reporta")
            independientes = tema.get("procedencias_independientes") or [
                [m] for m in tema["procedencias"]
            ]
            st.markdown(
                f"{len(tema['procedencias'])} medio(s), **{len(independientes)} procedencia(s) "
                "independiente(s)**."
            )
            for medios in independientes:
                nota = (
                    " (titulares casi idénticos: posible agencia replicada)"
                    if len(medios) > 1
                    else ""
                )
                st.markdown(f"- {escapar_md(', '.join(medios))}{nota}")

        with tarjeta("respaldo"):
            st.markdown("#### Qué está respaldado")
            if not tema["vinculos_oficiales"]:
                st.markdown("Sin respaldo oficial vinculado (Banco Mundial o USGS).")
            for v in tema["vinculos_oficiales"]:
                st.markdown(f"- `{v['id_evidencia']}` · {escapar_md(v['cita'])}")
                with st.expander(f"Limitaciones y regla · {v['id_evidencia']}"):
                    st.markdown(f"**Regla:** {escapar_md(v['regla'])}")
                    for limitacion in v["limitaciones"]:
                        st.markdown(f"- {escapar_md(limitacion)}")
                    if v.get("serie"):
                        st.dataframe(
                            [
                                {"Año": a, "Valor": "sin dato" if valor is None else valor}
                                for a, valor in v["serie"]
                            ],
                            hide_index=True,
                        )

    with derecha:
        with tarjeta("accion"):
            st.markdown("#### Acción recomendada")
            for accion in accion_recomendada(tema):
                st.markdown(f"- {accion}")

        with tarjeta("falta"):
            st.markdown("#### Qué falta comprobar")
            st.markdown(f"Estado de evidencia: **{escapar_md(tema['motivo_estado'])}**")
            if tema["pendientes"]:
                for pendiente in tema["pendientes"]:
                    st.markdown(f"- {escapar_md(pendiente)}")
            else:
                st.markdown("- No se detectaron datos pendientes de verificar.")

    with tarjeta("desglose"):
        st.markdown("#### Desglose del puntaje")
        st.dataframe(filas_componentes(tema), hide_index=True, width="stretch")


with st.sidebar:
    st.header("Bandeja")
    regenerar = st.button(
        "Regenerar bandeja", help="Vuelve a calcular la bandeja desde la base cargada."
    )

bandeja, mensaje = cargar_bandeja(settings.processed_dir, regenerar=regenerar)

st.title("Faro Editorial")
pastillas = ""
if bandeja is not None:
    pastillas = "".join(
        f'<span class="pastilla">{html.escape(texto)}</span>'
        for texto in (
            f"Corte {bandeja['referencia_panama']} · Panamá",
            bandeja["version_reglas"],
            bandeja["version_criterios"],
        )
    )
st.markdown(
    f'<div class="cabecera"><span class="marca">TVN Media · Mesa de evaluación editorial</span>'
    f"{pastillas}</div>",
    unsafe_allow_html=True,
)

if bandeja is None:
    st.info(mensaje)
    mostrar_configuracion()
    st.stop()

with st.sidebar:
    st.caption(mensaje)
    st.caption(
        f"Fecha de corte: {bandeja['origen_referencia']}. Agrupación: {bandeja['agrupacion']}."
    )
    st.divider()
    st.subheader("Filtros")
    bandas = st.multiselect(
        "Banda",
        list(ETIQUETAS_BANDA),
        format_func=ETIQUETAS_BANDA.get,
        placeholder="Todas",
        key="filtro_banda",
    )
    estados = st.multiselect(
        "Evidencia",
        list(ETIQUETAS_ESTADO),
        format_func=ETIQUETAS_ESTADO.get,
        placeholder="Todos",
        key="filtro_estado",
    )
    temas_disponibles = sorted({t["tema"] or "sin clasificar" for t in bandeja["temas"]})
    temas_sel = st.multiselect("Tema", temas_disponibles, placeholder="Todos", key="filtro_tema")

pestana_bandeja, pestana_consulta, pestana_borrador, pestana_config = st.tabs(
    ["Bandeja y ficha", "Consulta", "Borrador", "Configuración"]
)

with pestana_bandeja:
    st.markdown(resumen_html(bandeja["resumen"]), unsafe_allow_html=True)

    visibles = filtrar(bandeja["temas"], bandas, estados, temas_sel)
    with tarjeta("bandeja"):
        st.markdown("#### Bandeja priorizada")
        st.caption(
            "La prioridad ordena qué revisar; no es una probabilidad de verdad ni habilita "
            "publicación. La decisión editorial es de la persona revisora."
        )
        if bandeja["advertencias"]:
            with st.expander(f"Advertencias de la agrupación ({len(bandeja['advertencias'])})"):
                for advertencia in bandeja["advertencias"]:
                    st.markdown(f"- {escapar_md(advertencia)}")
        if not bandeja["temas"]:
            st.info("La base no tiene noticias válidas. Revisa reporte_calidad.json.")
        elif not visibles:
            st.info("Ningún tema coincide con los filtros.")
        else:
            st.dataframe(
                filas_bandeja(visibles),
                hide_index=True,
                width="stretch",
                column_config={
                    "#": st.column_config.NumberColumn(width="small"),
                    "Puntaje": st.column_config.ProgressColumn(
                        format="%.1f", min_value=0, max_value=100, width="small"
                    ),
                    "Banda": st.column_config.TextColumn(width="small"),
                    "Fuentes indep.": st.column_config.NumberColumn(width="small"),
                    "Titular": st.column_config.TextColumn(width="large"),
                },
            )
            por_id = {t["id_grupo"]: t for t in visibles}
            elegido = st.selectbox(
                "Abrir ficha",
                list(por_id),
                format_func=lambda i: (
                    f"#{por_id[i]['posicion']} · {por_id[i]['puntaje']:.1f} · {por_id[i]['titulo']}"
                ),
                placeholder="Elige un tema",
                key="tema_elegido",
            )
    if visibles:
        mostrar_ficha(por_id[elegido])

with pestana_consulta:
    st.info(
        "Pendiente: consulta en español con citas o abstención (búsqueda híbrida, #13). "
        "Mientras tanto, cualquier cita se puede verificar con  "
        "uv run python -m faro_editorial.evidencia <ID>"
    )

with pestana_borrador:
    st.info("Pendiente: borrador con citas por afirmación y verificación (#15).")

with pestana_config:
    mostrar_configuracion()
