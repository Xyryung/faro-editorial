"""Interfaz Streamlit de Faro Editorial: bandeja priorizada y ficha de evidencia (issue #16).

Ejecutar desde la raíz del repo:  uv run streamlit run app/main.py

Lee data/processed/bandeja.json (o lo genera desde la base cargada). Funciona sin internet.
"""

import html
from pathlib import Path

import streamlit as st

from faro_editorial import __version__
from faro_editorial.graficos import (
    COLORES_COMPONENTE,
    colores_evidencia,
    colores_prioridad,
    grafico_aportes,
    grafico_desglose,
    grafico_evidencia,
    grafico_noticias_por_dia,
    periodo_noticias,
)
from faro_editorial.interfaz import (
    ETIQUETAS_BANDA,
    ETIQUETAS_ESTADO,
    accion_recomendada,
    cargar_bandeja,
    escapar_md,
    etiqueta_tema,
    etiquetas_html,
    fila_html,
    filas_bandeja,
    filas_componentes,
    filtrar,
    leyenda_html,
    medios_html,
    noticia_html,
    pasos_html,
    tarjetas_kpi,
    texto_datos,
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


PROPORCION_FICHA = [1, 1]  # mitades: el borde coincide con la cuadrícula de arriba


def cuadricula(dividir_derecha: bool = True) -> list:
    """Cuatro columnas armadas como dos mitades de dos. Con dividir_derecha=False la mitad
    derecha queda entera; así sus bordes coinciden con los de una fila de cuatro."""
    izquierda, derecha = st.columns(2, gap="small")
    with izquierda:
        columnas = list(st.columns(2, gap="small"))
    if not dividir_derecha:
        return [*columnas, derecha]
    with derecha:
        return [*columnas, *st.columns(2, gap="small")]


def grafico(chart, descripcion: str) -> None:
    st.altair_chart(chart, width="stretch", theme=None, alt=descripcion)


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

    # La ficha va por filas: en cada fila las dos tarjetas tienen la misma altura y los bordes
    # coinciden con los de la fila de abajo (mismas proporciones en todas).
    reporta, accion = st.columns(PROPORCION_FICHA, gap="small")
    with reporta, tarjeta("reporta"):
        st.markdown("#### Qué se reporta")
        filas = "".join(noticia_html(n) for n in tema["noticias"])
        st.markdown(f'<div class="filas">{filas}</div>', unsafe_allow_html=True)
        if any(n.get("alcance_texto") != "titular_descripcion" for n in tema["noticias"]):
            st.caption("Parte de este tema se basa únicamente en titular y metadatos.")
    with accion, tarjeta("accion"):
        st.markdown("#### Acción recomendada")
        st.markdown(pasos_html(accion_recomendada(tema)), unsafe_allow_html=True)

    quien, falta = st.columns(PROPORCION_FICHA, gap="small")
    with quien, tarjeta("quien"):
        st.markdown("#### Quién lo reporta")
        independientes = tema.get("procedencias_independientes") or [
            [m] for m in tema["procedencias"]
        ]
        st.markdown(
            f"{len(tema['procedencias'])} medio(s) y **{len(independientes)} procedencia(s) "
            "independiente(s)**. Los titulares casi idénticos cuentan como una sola."
        )
        st.markdown(medios_html(independientes), unsafe_allow_html=True)
    with falta, tarjeta("falta"):
        st.markdown("#### Qué falta comprobar")
        st.markdown(f"**{escapar_md(tema['motivo_estado'])}**")
        if tema["pendientes"]:
            filas = "".join(fila_html(p, "aviso") for p in tema["pendientes"])
            st.markdown(f'<div class="filas">{filas}</div>', unsafe_allow_html=True)
        else:
            st.caption("No se detectaron datos concretos pendientes de verificar.")

    with tarjeta("respaldo"):
        st.markdown("#### Qué está respaldado")
        if not tema["vinculos_oficiales"]:
            st.markdown("Sin respaldo oficial vinculado (Banco Mundial o USGS).")
        for v in tema["vinculos_oficiales"]:
            st.markdown(
                fila_html(v["cita"], "ok", codigo=v["id_evidencia"]), unsafe_allow_html=True
            )
            with st.expander(f"Limitaciones y regla · {v['id_evidencia']}"):
                st.markdown(f"**Regla:** {escapar_md(v['regla'])}")
                st.markdown(" ".join(escapar_md(lim) for lim in v["limitaciones"]))
                if v.get("serie"):
                    st.dataframe(
                        [
                            {"Año": a, "Valor": "sin dato" if valor is None else valor}
                            for a, valor in v["serie"]
                        ],
                        hide_index=True,
                    )

    with tarjeta("desglose"):
        st.markdown("#### Desglose del puntaje")
        grafico(grafico_desglose(tema), "Aporte de cada componente al puntaje del tema")
        with st.expander("Ver criterios de cada componente"):
            st.dataframe(filas_componentes(tema), hide_index=True, width="stretch")


# Barra lateral: filtros arriba, datos abajo (el botón se crea antes de cargar la bandeja).
zona_filtros = st.sidebar.container()
zona_datos = st.sidebar.container(key="zona_datos")
with zona_datos:
    st.subheader("Datos")
    regenerar = st.button(
        "Regenerar bandeja",
        help="Vuelve a calcular la bandeja desde la base cargada.",
        width="stretch",
    )

bandeja, mensaje = cargar_bandeja(settings.processed_dir, regenerar=regenerar)

estados_sistema = [
    ("ok", "Bandeja lista") if bandeja is not None else ("aviso", "Sin snapshot"),
    ("ok", "Modo sin conexión") if settings.offline else ("info", "En línea"),
]
pastillas = "".join(
    f'<span class="estado estado-{clase}">{html.escape(texto)}</span>'
    for clase, texto in estados_sistema
)
if bandeja is not None:
    pastillas += "".join(
        f'<span class="pastilla">{html.escape(texto)}</span>'
        for texto in (
            f"Corte {bandeja['referencia_panama']} · Panamá",
            f"{bandeja['version_reglas']} · {bandeja['version_criterios']}",
        )
    )
with tarjeta("cabecera"):
    marca, estado = st.columns([1, 2], vertical_alignment="center")
    with marca:
        st.title("Faro Editorial")
        st.markdown(
            '<div class="marca">TVN Media · Mesa de evaluación editorial</div>',
            unsafe_allow_html=True,
        )
    with estado:
        st.markdown(f'<div class="cabecera">{pastillas}</div>', unsafe_allow_html=True)

if bandeja is None:
    st.info(mensaje)
    mostrar_configuracion()
    st.stop()

with zona_datos:
    st.caption(texto_datos(bandeja))
if regenerar:
    st.toast("Bandeja recalculada desde la base cargada.")

with zona_filtros:
    st.subheader("Filtros")
    bandas = st.multiselect(
        "Prioridad",
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
    temas_disponibles = sorted(
        {t["tema"] or "sin clasificar" for t in bandeja["temas"]}, key=etiqueta_tema
    )
    temas_sel = st.multiselect(
        "Tema",
        temas_disponibles,
        format_func=etiqueta_tema,
        placeholder="Todos",
        key="filtro_tema",
    )

pestana_bandeja, pestana_consulta, pestana_borrador, pestana_config = st.tabs(
    ["Bandeja y ficha", "Consulta", "Borrador", "Configuración"]
)

with pestana_bandeja:
    # Indicadores y gráficos sobre la misma cuadrícula de cuatro columnas.
    for columna, kpi in zip(cuadricula(), tarjetas_kpi(bandeja["resumen"]), strict=True):
        with columna:
            st.markdown(kpi, unsafe_allow_html=True)

    visibles = filtrar(bandeja["temas"], bandas, estados, temas_sel)
    if visibles:
        evidencia, por_dia, aportes = cuadricula(dividir_derecha=False)
        with evidencia, tarjeta("g_evidencia"):
            st.markdown("#### Evidencia")
            grafico(grafico_evidencia(visibles), "Temas por estado de evidencia")
            st.markdown(leyenda_html(colores_evidencia(visibles)), unsafe_allow_html=True)
        with por_dia, tarjeta("g_dias"):
            periodo = periodo_noticias(visibles)
            st.markdown(f"#### Noticias por {periodo}")
            grafico(grafico_noticias_por_dia(visibles), f"Noticias por {periodo} de publicación")
            st.markdown(leyenda_html(colores_prioridad(visibles)), unsafe_allow_html=True)
        with aportes, tarjeta("g_aportes"):
            st.markdown("#### Qué compone el puntaje")
            grafico(grafico_aportes(visibles), "Aporte de cada componente por tema")
            st.markdown(leyenda_html(COLORES_COMPONENTE), unsafe_allow_html=True)

    with tarjeta("bandeja"):
        st.markdown("#### Bandeja priorizada")
        st.caption(
            "La prioridad ordena qué revisar; no es una probabilidad de verdad ni habilita "
            "publicación. La decisión editorial es de la persona revisora."
        )
        if bandeja["advertencias"]:
            with st.expander(f"Advertencias de la agrupación ({len(bandeja['advertencias'])})"):
                filas = "".join(fila_html(a, "aviso") for a in bandeja["advertencias"])
                st.markdown(f'<div class="filas">{filas}</div>', unsafe_allow_html=True)
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
                    "#": st.column_config.NumberColumn(width=44, alignment="left"),
                    "Puntaje": st.column_config.ProgressColumn(
                        format="%.1f", min_value=0, max_value=100, width=120
                    ),
                    "Prioridad": st.column_config.TextColumn(width=84),
                    "Evidencia": st.column_config.TextColumn(width=110),
                    "Tema": st.column_config.TextColumn(width=140),
                    "Titular": st.column_config.TextColumn(),  # ocupa el ancho que sobra
                    "Fuentes": st.column_config.NumberColumn(
                        width=72, alignment="left", help="Procedencias independientes"
                    ),
                    "Fecha original": st.column_config.TextColumn(width=136, help="Hora de Panamá"),
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
