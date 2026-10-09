"""Interfaz Streamlit de Faro Editorial: bandeja priorizada y ficha de evidencia (issue #16).

Ejecutar desde la raíz del repo:  uv run streamlit run app/main.py

Lee data/processed/bandeja.json (o lo genera desde la base cargada). Funciona sin internet.
"""

import html
from pathlib import Path

import streamlit as st

from faro_editorial import __version__
from faro_editorial.bandeja import nombres_medios
from faro_editorial.borradores import leer_borradores
from faro_editorial.contrato import plural
from faro_editorial.graficos import (
    colores_prioridad,
    grafico_desglose,
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
    fecha_legible,
    fila_html,
    filas_bandeja,
    filas_componentes,
    filtrar,
    leyenda_html,
    lineas_consulta,
    medios_html,
    noticia_html,
    pasos_html,
    tarjetas_kpi,
    texto_datos,
    version_legible,
)
from faro_editorial.revision import (
    ESTADO_INICIAL,
    Revision,
    avance_revision_html,
    conteo_revision,
    etiqueta_revision,
    fecha_revision_legible,
    guardar_revision,
    historial,
    texto_para_notion,
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
        fecha = html.escape(fecha_legible(tema["fecha_original_panama"]))
        st.markdown(
            f'<div class="meta">Fecha original: <b>{fecha}</b> (hora de Panamá)</div>',
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
        procedencias = plural(
            len(independientes), "procedencia independiente", "procedencias independientes"
        )
        st.markdown(
            f"{plural(len(tema['procedencias']), 'medio')} y **{procedencias}**. "
            "Los titulares casi idénticos cuentan como una sola."
        )
        st.markdown(medios_html(nombres_medios(tema)), unsafe_allow_html=True)
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

    mostrar_revision(tema)


def mostrar_revision(tema: dict) -> None:
    """Control humano (issue #17): la persona revisora registra su decisión sobre el tema."""
    decisiones = historial(settings.processed_dir).get(tema["id_grupo"], [])
    ultima = decisiones[-1] if decisiones else None
    with tarjeta("revision"):
        st.markdown("#### Revisión humana")
        aviso = st.session_state.pop("revision_guardada", None)
        if aviso:
            st.success(aviso)
        if ultima is None:
            st.markdown("Estado actual: **Nuevo** (sin revisar).")
        else:
            st.markdown(
                f"Estado actual: **{etiqueta_revision(ultima['estado_revision'])}** · "
                f"{escapar_md(ultima['revisor'])} · "
                f"{fecha_revision_legible(ultima['fecha_revision_utc'])} (hora de Panamá)"
            )

        estados = reglas.estados_revision
        actual = ultima["estado_revision"] if ultima else ESTADO_INICIAL
        caso = tema["id_grupo"]
        # Claves por tema: al cambiar de ficha, el formulario no arrastra la decisión anterior.
        with st.form(key=f"form_revision_{caso}", border=False, clear_on_submit=True):
            estado = st.selectbox(
                "Decisión",
                estados,
                index=estados.index(actual) if actual in estados else 0,
                format_func=etiqueta_revision,
                key=f"revision_estado_{caso}",
            )
            revisor = st.text_input(
                "Persona revisora",
                value=st.session_state.get("ultimo_revisor", ""),
                max_chars=80,
                key=f"revision_revisor_{caso}",
            )
            comentario = st.text_area(
                "Comentario", max_chars=2000, height=80, key=f"revision_comentario_{caso}"
            )
            st.caption("Aprobar como borrador no publica nada: solo registra la decisión.")
            guardar = st.form_submit_button("Guardar revisión", key=f"revision_guardar_{caso}")
        if guardar:
            if not revisor.strip():
                st.error("Escribe el nombre de la persona revisora.")
            else:
                revision = Revision(estado_revision=estado, revisor=revisor, comentario=comentario)
                guardar_revision(settings.processed_dir, tema, revision, reglas)
                st.session_state["ultimo_revisor"] = revision.revisor
                st.session_state["revision_guardada"] = (
                    f"Revisión guardada: {etiqueta_revision(estado)}."
                )
                st.rerun()  # para que la bandeja muestre el estado nuevo

        if len(decisiones) > 1:
            with st.expander(f"Historial de revisiones ({len(decisiones)})"):
                filas = "".join(
                    fila_html(
                        f"{etiqueta_revision(d['estado_revision'])} · {d['revisor']} · "
                        f"{fecha_revision_legible(d['fecha_revision_utc'])}"
                        + (f" · {d['comentario']}" if d.get("comentario") else "")
                    )
                    for d in reversed(decisiones)
                )
                st.markdown(f'<div class="filas">{filas}</div>', unsafe_allow_html=True)
        borrador = leer_borradores(settings.processed_dir).get(caso)
        if borrador:
            st.caption(
                f"El borrador sugiere: {etiqueta_revision(borrador['estado_sugerido'])}. "
                "Se guarda junto con tu decisión; míralo en la pestaña Borrador."
            )
        with st.expander("Copiar ficha para Notion"):
            st.caption("Usa el botón de copiar del recuadro y pégalo en 'Casos y evidencias'.")
            st.code(texto_para_notion(tema, ultima, borrador), language="markdown", wrap_lines=True)


@st.cache_resource(show_spinner=False)
def buscador_en_memoria(processed_dir: str, marca: float):
    """Un solo buscador por base cargada: el índice semántico se prepara una vez y no en cada
    consulta. `marca` (fecha de la base) hace que se rehaga si se vuelve a cargar."""
    from faro_editorial.busqueda import Buscador, leer_corpus, load_config

    buscador = Buscador(leer_corpus(Path(processed_dir)), load_config())
    buscador.preparar()
    return buscador


def marca_base() -> float:
    from faro_editorial.carga import NOMBRE_DB

    ruta = settings.processed_dir / NOMBRE_DB
    return ruta.stat().st_mtime if ruta.exists() else 0.0


ESTADOS_JEV = {
    "respaldada": "Respaldada",
    "dudosa": "Dudosa",
    "no_respaldada": "No respaldada",
    "sin_verificar": "Sin verificar",
    "no_aplica": "No aplica (hipótesis)",
}


def _jev_legible(verificacion: dict) -> str:
    texto = ESTADOS_JEV.get(verificacion["estado"], verificacion["estado"])
    if "probabilidad" in verificacion:
        texto += f" ({verificacion['probabilidad']:.2f})"
    return texto


def mostrar_borrador(id_elegido: str | None) -> None:
    """Borrador con cita por afirmación (issue #15). Todo texto del LLM se muestra escapado:
    puede repetir un titular con Markdown o enlaces (T07)."""
    borradores = leer_borradores(settings.processed_dir)
    temas_por_id = {t["id_grupo"]: t for t in bandeja["temas"]}
    opciones = sorted(
        (i for i in borradores if i in temas_por_id), key=lambda i: temas_por_id[i]["posicion"]
    )
    if not opciones:
        st.info(
            "Todavía no hay borradores. Genéralos desde la terminal con  "
            "`uv run python -m faro_editorial.borradores --top 5`  y recarga la página."
        )
        return
    indice = opciones.index(id_elegido) if id_elegido in opciones else 0
    if id_elegido in temas_por_id and id_elegido not in opciones:
        st.caption(
            "El tema abierto en la bandeja no tiene borrador; se muestra el primero que sí. "
            f"Para generarlo: `uv run python -m faro_editorial.borradores --grupo {id_elegido}`"
        )
    caso = st.selectbox(
        "Tema",
        opciones,
        index=indice,
        format_func=lambda i: f"#{temas_por_id[i]['posicion']} · {temas_por_id[i]['titulo']}",
        key="borrador_elegido",
    )
    f = borradores[caso]
    e = escapar_md

    with tarjeta("borrador"):
        st.caption(f["aviso"])
        if f["abstencion"]:
            st.warning(f"**Abstención.** {e(f['motivo_abstencion'])}")
            return
        b = f["borrador"]
        if b.get("aviso_alcance"):
            st.markdown(f"**{b['aviso_alcance']}**")
        modo = "Nota de investigación" if b["modo"] == "investigacion" else "Paquete editorial"
        st.markdown(f"#### {e(b['titulo'])}")
        st.markdown(
            f"{modo} · sugiere: **{etiqueta_revision(f['estado_sugerido'])}** · "
            f"generado {fecha_revision_legible(f['generado_utc'])} (hora de Panamá)"
        )
        st.markdown(f"**Enfoque de interés público:** {e(b['enfoque_interes_publico'])}")
        st.markdown(f"**Brief** ({b['palabras']['brief']} palabras)")
        st.markdown(e(b["brief"]))
        st.markdown("**Preguntas de investigación**")
        preguntas = b["preguntas_investigacion"]
        st.markdown("\n".join(f"{i}. {e(p)}" for i, p in enumerate(preguntas, 1)))
        if b["guion"]:
            st.markdown(f"**Guion** (~{b['guion_duracion_s']} s)")
            st.markdown(e(b["guion"]))
        if b["copy_digital"]:
            st.markdown(f"**Copy digital** ({b['palabras']['copy_digital']} palabras)")
            st.markdown(e(b["copy_digital"]))
        for c in b["contradicciones"]:
            versiones = "\n".join(
                f"- {e(v['texto'])} ({e(', '.join(x['id_evidencia'] for x in v['citas']))})"
                for v in c["versiones"]
            )
            st.markdown(
                f"**Versiones incompatibles:** {e(c['descripcion'])}\n\n{versiones}\n\n"
                f"Pendiente: {e(c['verificacion_pendiente'])}"
            )

    with tarjeta("borrador_citas"):
        st.markdown("#### Afirmaciones y citas")
        filas = [
            {
                "ID": a["id"],
                "Tipo": a["tipo"],
                "Afirmación": a["texto"],
                "Citas": "; ".join(f"{c['id_evidencia']} · {c['campo']}" for c in a["citas"])
                or "—",
                "Jev": _jev_legible(a["verificacion"]),
            }
            for a in f["afirmaciones"]
        ]
        st.dataframe(filas, hide_index=True, width="stretch")
        st.caption(
            "Para ver el registro detrás de una cita:  "
            "`uv run python -m faro_editorial.evidencia <ID>`"
        )
        if f["afirmaciones_rechazadas"]:
            st.markdown("**Afirmaciones rechazadas** (citaban evidencia que no existe)")
            st.markdown(
                "\n".join(
                    f"- {r['id']}: {e(r['texto'])} — {e(r['motivo'])}"
                    for r in f["afirmaciones_rechazadas"]
                )
            )
        if b["verificaciones_pendientes"]:
            st.markdown("**Verificaciones pendientes**")
            st.markdown("\n".join(f"- {e(p)}" for p in b["verificaciones_pendientes"]))
        with st.expander("Comprobaciones automáticas"):
            st.markdown(
                "\n".join(
                    f"- {'OK' if c['ok'] else 'FALLA'} · {c['nombre']}: {e(c['detalle'])}"
                    for c in f["comprobaciones"]
                )
            )
            ia = f.get("ia", {})
            st.caption(
                f"Modelo: {ia.get('modelo')} · intentos: {ia.get('intentos')} · "
                f"costo LLM USD {ia.get('costo_usd_llm')} · Jev USD {ia.get('costo_usd_jev')} · "
                f"{f['version_borradores']}"
            )


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

# Cabecera: título y, a la derecha, el corte y las versiones como texto (sin pastillas).
modo = "Sin conexión" if settings.offline else "En línea"
if bandeja is None:
    lineas = ["Sin snapshot cargado", modo]
else:
    lineas = [
        f"Corte: {fecha_legible(bandeja['referencia_panama'])} (hora de Panamá)",
        f"{version_legible(bandeja['version_reglas'])} · "
        f"{version_legible(bandeja['version_criterios'])} · {modo}",
    ]
with st.container(key="cabecera"):
    marca, estado = st.columns([1, 1], vertical_alignment="bottom")
    with marca:
        st.title("Faro Editorial")
        st.markdown(
            '<div class="marca">TVN Media · Mesa de evaluación editorial</div>',
            unsafe_allow_html=True,
        )
    with estado:
        texto = "".join(f"<span>{html.escape(linea)}</span>" for linea in lineas)
        st.markdown(f'<div class="cabecera">{texto}</div>', unsafe_allow_html=True)

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
    revisiones_sel = st.multiselect(
        "Revisión",
        reglas.estados_revision,
        format_func=etiqueta_revision,
        placeholder="Todas",
        key="filtro_revision",
    )

# Estado de revisión vigente de cada tema (los no revisados están en "nuevo").
vigentes = {
    caso: decisiones[-1]["estado_revision"]
    for caso, decisiones in historial(settings.processed_dir).items()
}

pestana_bandeja, pestana_consulta, pestana_borrador, pestana_config = st.tabs(
    ["Bandeja y ficha", "Consulta", "Borrador", "Configuración"]
)

with pestana_bandeja:
    # Indicadores y gráficos sobre la misma cuadrícula de cuatro columnas.
    for columna, kpi in zip(cuadricula(), tarjetas_kpi(bandeja["resumen"]), strict=True):
        with columna:
            st.markdown(kpi, unsafe_allow_html=True)

    visibles = [
        t
        for t in filtrar(bandeja["temas"], bandas, estados, temas_sel)
        if not revisiones_sel or vigentes.get(t["id_grupo"], ESTADO_INICIAL) in revisiones_sel
    ]
    if visibles:
        # Dos mitades: sus bordes coinciden con los de la fila de indicadores.
        evidencia, por_dia = st.columns(2, gap="small")
        with evidencia, tarjeta("g_revision"):
            # Avance de la revisión humana: cuánto le falta al editor (cambia al revisar).
            st.markdown("#### Revisión")
            st.markdown(
                avance_revision_html(conteo_revision(visibles, vigentes)),
                unsafe_allow_html=True,
            )
        with por_dia, tarjeta("g_dias"):
            periodo = periodo_noticias(visibles)
            st.markdown(f"#### Noticias por {periodo}")
            grafico(grafico_noticias_por_dia(visibles), f"Noticias por {periodo} de publicación")
            st.markdown(leyenda_html(colores_prioridad(visibles)), unsafe_allow_html=True)

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
            filas = filas_bandeja(visibles)
            for fila, tema in zip(filas, visibles, strict=True):
                fila["Revisión"] = etiqueta_revision(vigentes.get(tema["id_grupo"], ESTADO_INICIAL))
            st.dataframe(
                filas,
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
                    "Revisión": st.column_config.TextColumn(width=150),
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
    st.markdown("#### Consulta en español")
    st.caption(
        "Búsqueda híbrida (BM25 + semántica) con compuerta de abstención (#13). "
        "Sin evidencia suficiente, el sistema se abstiene y explica qué falta."
    )
    pregunta = st.text_input(
        "Pregunta",
        placeholder="p. ej. ¿Qué lluvias hubo en Chiriquí?",
        key="consulta_pregunta",
    )
    if st.button("Consultar", key="consulta_boton") and (pregunta or "").strip():
        with st.spinner("Buscando evidencia… (la primera consulta prepara el índice)"):
            buscador = buscador_en_memoria(str(settings.processed_dir), marca_base())
            cliente = None
            if not settings.offline:
                from faro_editorial.proveedores import crear_cliente

                cliente = crear_cliente("jev")
            respuesta = buscador.responder(pregunta.strip(), cliente)
        # El texto de las fuentes y de la pregunta se escapa (T07): es dato, no formato.
        textos = lineas_consulta(respuesta)
        if textos["abstencion"]:
            st.warning(f"**Abstención.** {textos['motivo']}")
            st.markdown(f"**Qué haría falta:** {textos['falta']}")
        else:
            if textos["nota"]:
                st.caption(f"Nota: {textos['nota']}")
            for linea in textos["citas"]:
                st.markdown(linea)

with pestana_borrador:
    mostrar_borrador(st.session_state.get("tema_elegido"))

with pestana_config:
    mostrar_configuracion()
