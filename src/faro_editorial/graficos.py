"""Gráficos de la interfaz (issue #16).

Altair viene con Streamlit y se dibuja en el navegador sin internet. Las funciones datos_* arman
las tablas (se prueban sin Streamlit); las grafico_* solo les dan forma. Los títulos de las
noticias se dibujan como texto del gráfico, nunca como HTML.
"""

from collections import Counter

import altair as alt

from faro_editorial.interfaz import ETIQUETAS_BANDA, ETIQUETAS_ESTADO, NOMBRES_COMPONENTES

COLORES_ESTADO = {
    "Insuficiente": "#F5A866",
    "Parcial": "#A995E0",
    "Suficiente para borrador": "#6CC48C",
}
COLORES_BANDA = {"Alto": "#2F6DB5", "Medio": "#8FB4E8", "Bajo": "#C9D0DC"}
COLORES_COMPONENTE = {
    "Relevancia": "#12355B",
    "Impacto potencial": "#2F6DB5",
    "Urgencia": "#5B8FD1",
    "Novedad": "#8FB4E8",
    "Evidencia disponible": "#6CC48C",
}
_TEXTO = "#26303B"
_SUAVE = "#5B6B80"
_REJILLA = "#D6E0EC"
ALTO = 200  # alto común de los gráficos de la bandeja, para que las tarjetas se alineen


def _escala(colores: dict[str, str]) -> alt.Scale:
    return alt.Scale(domain=list(colores), range=list(colores.values()))


def _estilo(grafico: alt.Chart, alto: int) -> alt.Chart:
    return (
        grafico.properties(height=alto, background="transparent")
        .configure_view(stroke=None)
        .configure_axis(
            labelColor=_SUAVE,
            titleColor=_SUAVE,
            gridColor=_REJILLA,
            domainColor=_REJILLA,
            tickColor=_REJILLA,
            labelFontSize=11,
            titleFontSize=11,
            titleFontWeight=600,
        )
        .configure_legend(
            labelColor=_TEXTO, titleColor=_SUAVE, labelFontSize=11, orient="bottom", title=None
        )
    )


def _corto(texto: str, largo: int = 46) -> str:
    return texto if len(texto) <= largo else texto[: largo - 1].rstrip() + "…"


# --- Datos ----------------------------------------------------------------------------


def datos_evidencia(temas: list[dict]) -> list[dict]:
    """Cuántos temas hay en cada estado de evidencia (en el orden del semáforo)."""
    conteo = Counter(t["estado_evidencia"] for t in temas)
    return [
        {"Estado": etiqueta, "Temas": conteo[clave]}
        for clave, etiqueta in ETIQUETAS_ESTADO.items()
        if conteo[clave]
    ]


def datos_noticias_por_dia(temas: list[dict]) -> list[dict]:
    """Noticias por día de publicación (hora de Panamá) y banda del tema al que pertenecen."""
    conteo: Counter[tuple[str, str]] = Counter()
    for t in temas:
        banda = ETIQUETAS_BANDA.get(t["banda"], t["banda"])
        for n in t["noticias"]:
            fecha = n.get("fecha_publicacion_panama") or n.get("fecha_deteccion_panama")
            if fecha:
                conteo[(fecha[:10], banda)] += 1
    return [{"Día": d, "Banda": b, "Noticias": c} for (d, b), c in sorted(conteo.items())]


def datos_aportes(temas: list[dict], limite: int = 8) -> list[dict]:
    """Aporte de cada componente al puntaje de los primeros temas del ranking."""
    filas = []
    for t in temas[:limite]:
        etiqueta = f"#{t['posicion']} · {_corto(t['titulo'])}"
        for clave, c in t["componentes"].items():
            filas.append(
                {
                    "Tema": etiqueta,
                    "Posición": t["posicion"],
                    "Componente": NOMBRES_COMPONENTES.get(clave, clave),
                    "Aporte": round(float(c["aporte"]), 1),
                }
            )
    return filas


def datos_desglose(tema: dict) -> list[dict]:
    """Aporte de cada componente frente a su peso máximo."""
    return [
        {
            "Componente": NOMBRES_COMPONENTES.get(clave, clave),
            "Aporte": round(float(c["aporte"]), 1),
            "Peso": float(c["peso"]),
        }
        for clave, c in tema["componentes"].items()
    ]


# --- Gráficos -------------------------------------------------------------------------


def grafico_evidencia(temas: list[dict]) -> alt.Chart:
    filas = datos_evidencia(temas)
    datos = alt.Data(values=filas)
    base = alt.Chart(datos).encode(
        theta=alt.Theta("Temas:Q", stack=True),
        color=alt.Color(
            "Estado:N",
            scale=_escala(COLORES_ESTADO),
            sort=list(COLORES_ESTADO),
            legend=alt.Legend(values=[f["Estado"] for f in filas], labelLimit=180, columns=1),
        ),
        tooltip=["Estado:N", "Temas:Q"],
    )
    dona = base.mark_arc(innerRadius=52, outerRadius=82, cornerRadius=4, padAngle=0.02)
    return _estilo(dona, ALTO)


def grafico_noticias_por_dia(temas: list[dict]) -> alt.Chart:
    datos = alt.Data(values=datos_noticias_por_dia(temas))
    barras = (
        alt.Chart(datos)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=22)
        .encode(
            x=alt.X("Día:T", title=None, axis=alt.Axis(format="%d %b", labelAngle=0, grid=False)),
            y=alt.Y("sum(Noticias):Q", title="Noticias", axis=alt.Axis(tickMinStep=1)),
            color=alt.Color("Banda:N", scale=_escala(COLORES_BANDA), sort=list(COLORES_BANDA)),
            order=alt.Order("Banda:N", sort="ascending"),
            tooltip=[alt.Tooltip("Día:T", format="%d/%m/%Y"), "Banda:N", "Noticias:Q"],
        )
    )
    return _estilo(barras, ALTO)


def grafico_aportes(temas: list[dict], limite: int = 8) -> alt.Chart:
    datos = alt.Data(values=datos_aportes(temas, limite))
    barras = (
        alt.Chart(datos)
        .mark_bar(size=18, cornerRadius=2)
        .encode(
            y=alt.Y(
                "Tema:N",
                title=None,
                sort=alt.EncodingSortField("Posición", order="ascending"),
                axis=alt.Axis(labelLimit=240, labelPadding=8, ticks=False, domain=False),
            ),
            x=alt.X("sum(Aporte):Q", title="Puntaje (0-100)", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color(
                "Componente:N",
                scale=_escala(COLORES_COMPONENTE),
                sort=list(COLORES_COMPONENTE),
                legend=alt.Legend(columns=3, labelLimit=160),
            ),
            order=alt.Order("Componente:N"),
            tooltip=["Tema:N", "Componente:N", "Aporte:Q"],
        )
    )
    return _estilo(barras, max(ALTO - 40, 34 * min(len(temas), limite)))


def grafico_desglose(tema: dict) -> alt.Chart:
    datos = alt.Data(values=datos_desglose(tema))
    orden = list(COLORES_COMPONENTE)
    y = alt.Y("Componente:N", title=None, sort=orden, axis=alt.Axis(labelLimit=200))
    fondo = (
        alt.Chart(datos)
        .mark_bar(size=14, cornerRadius=7, color="#E0E9F4")
        .encode(
            y=y,
            x=alt.X(
                "Peso:Q",
                title="Aporte sobre el peso máximo",
                axis=alt.Axis(grid=False, tickCount=6),
            ),
        )
    )
    aporte = (
        alt.Chart(datos)
        .mark_bar(size=14, cornerRadius=7)
        .encode(
            y=y,
            x="Aporte:Q",
            color=alt.Color(
                "Componente:N", scale=_escala(COLORES_COMPONENTE), legend=None, sort=orden
            ),
            tooltip=["Componente:N", "Aporte:Q", "Peso:Q"],
        )
    )
    return _estilo(alt.layer(fondo, aporte), 190)
