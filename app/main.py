"""Interfaz Streamlit de Faro Editorial: bandeja priorizada y ficha de evidencia (issue #16).

Ejecutar desde la raíz del repo:  uv run streamlit run app/main.py

Lee data/processed/bandeja.json (o lo genera desde la base cargada). Funciona sin internet.
"""

import streamlit as st

from faro_editorial import __version__
from faro_editorial.interfaz import (
    ETIQUETAS_BANDA,
    ETIQUETAS_ESTADO,
    accion_recomendada,
    cargar_bandeja,
    escapar_md,
    filas_bandeja,
    filas_componentes,
    filtrar,
)
from faro_editorial.rules import load_rules
from faro_editorial.settings import get_settings

st.set_page_config(page_title="Faro Editorial", layout="wide")

settings = get_settings()
reglas = load_rules(settings.rules_path)

st.title("Faro Editorial")
st.caption("Copiloto de inteligencia informativa para TVN Media · hackIAthon")


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


def mostrar_ficha(tema: dict) -> None:
    st.subheader(escapar_md(tema["titulo"]))
    c1, c2, c3 = st.columns(3)
    c1.metric("Puntaje", f"{tema['puntaje']:.1f}")
    c2.metric("Banda", ETIQUETAS_BANDA.get(tema["banda"], tema["banda"]))
    c3.metric("Evidencia", ETIQUETAS_ESTADO.get(tema["estado_evidencia"], tema["estado_evidencia"]))
    # T03: la fecha original siempre a la vista, para no presentar algo viejo como nuevo.
    st.markdown(
        f"**Fecha original:** {tema['fecha_original_panama'] or 'sin fecha'} (hora de Panamá)"
    )
    st.warning(tema["aviso"])

    st.markdown("#### Acción recomendada")
    for accion in accion_recomendada(tema):
        st.markdown(f"- {accion}")

    st.markdown("#### Qué se reporta")
    for n in tema["noticias"]:
        publicada = n["fecha_publicacion_panama"] or "sin fecha"
        detectada = (
            f" · detectada {n['fecha_deteccion_panama']}" if n["fecha_deteccion_panama"] else ""
        )
        url = n["url"].replace(")", "%29")
        st.markdown(
            f"- **{escapar_md(n['titulo'])}** · {escapar_md(n['medio'])} · publicada {publicada}"
            f"{detectada} · [abrir]({url})"
        )
    if any(n.get("alcance_texto") != "titular_descripcion" for n in tema["noticias"]):
        st.caption("Parte de este tema se basa únicamente en titular y metadatos.")

    st.markdown("#### Quién lo reporta")
    independientes = tema.get("procedencias_independientes") or [[m] for m in tema["procedencias"]]
    st.markdown(
        f"{len(tema['procedencias'])} medio(s), **{len(independientes)} procedencia(s) "
        "independiente(s)**."
    )
    for medios in independientes:
        nota = " (titulares casi idénticos: posible agencia replicada)" if len(medios) > 1 else ""
        st.markdown(f"- {escapar_md(', '.join(medios))}{nota}")

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

    st.markdown("#### Qué falta comprobar")
    st.markdown(f"Estado de evidencia: **{escapar_md(tema['motivo_estado'])}**")
    if tema["pendientes"]:
        for pendiente in tema["pendientes"]:
            st.markdown(f"- {escapar_md(pendiente)}")
    else:
        st.markdown("- No se detectaron datos pendientes de verificar.")

    st.markdown("#### Desglose del puntaje")
    st.dataframe(filas_componentes(tema), hide_index=True, width="stretch")


with st.sidebar:
    st.header("Bandeja")
    regenerar = st.button(
        "Regenerar bandeja", help="Vuelve a calcular la bandeja desde la base cargada."
    )

bandeja, mensaje = cargar_bandeja(settings.processed_dir, regenerar=regenerar)

if bandeja is None:
    st.info(mensaje)
    mostrar_configuracion()
    st.stop()

with st.sidebar:
    st.caption(mensaje)
    st.markdown(
        f"**Referencia:** {bandeja['referencia_panama']} (hora de Panamá)  \n"
        f"{bandeja['origen_referencia']}"
    )
    st.markdown(
        f"**Reglas:** {bandeja['version_reglas']} · {bandeja['version_criterios']}  \n"
        f"**Agrupación:** {bandeja['agrupacion']}"
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
    resumen = bandeja["resumen"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Temas", resumen["grupos"])
    m2.metric("Prioridad alta", resumen["por_banda"].get("alto", 0))
    m3.metric("Evidencia insuficiente", resumen["por_estado_evidencia"].get("insuficiente", 0))
    m4.metric("Noticias", resumen["noticias"])
    st.caption(
        "La prioridad ordena qué revisar; no es una probabilidad de verdad ni habilita "
        "publicación. La decisión editorial es de la persona revisora."
    )
    if bandeja["advertencias"]:
        with st.expander(f"Advertencias de la agrupación ({len(bandeja['advertencias'])})"):
            for advertencia in bandeja["advertencias"]:
                st.markdown(f"- {escapar_md(advertencia)}")

    visibles = filtrar(bandeja["temas"], bandas, estados, temas_sel)
    if not bandeja["temas"]:
        st.info("La base no tiene noticias válidas. Revisa reporte_calidad.json.")
    elif not visibles:
        st.info("Ningún tema coincide con los filtros.")
    else:
        st.dataframe(filas_bandeja(visibles), hide_index=True, width="stretch")
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
        st.divider()
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
