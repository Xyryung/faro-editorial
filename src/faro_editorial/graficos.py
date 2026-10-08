"""Gráficos de la interfaz (issue #16).

Altair viene con Streamlit y se dibuja en el navegador sin internet. Las funciones datos_* arman
las tablas (se prueban sin Streamlit); las grafico_* solo les dan forma. Los títulos de las
noticias se dibujan como texto del gráfico, nunca como HTML.
"""

from collections import Counter
from datetime import date, timedelta

import altair as alt

from faro_editorial.interfaz import (
    ETIQUETAS_BANDA,
    ETIQUETAS_ESTADO,
    MESES_CORTOS,
    NOMBRES_COMPONENTES,
)

COLORES_ESTADO = {
    "Insuficiente": "#C98A4B",
    "Parcial": "#8E86B0",
    "Suficiente para borrador": "#5E9C76",
}
COLORES_BANDA = {"Alta": "#1F3A5F", "Media": "#7D8FA6", "Baja": "#C9C2B4"}
COLORES_COMPONENTE = {
    "Relevancia": "#1F3A5F",
    "Impacto potencial": "#3D5A80",
    "Urgencia": "#6B83A3",
    "Novedad": "#A3B3C7",
    "Evidencia disponible": "#5E9C76",
}
_TEXTO = "#2B2926"
_SUAVE = "#6E6A62"
_REJILLA = "#E2DCCF"
ALTO = 200  # alto común de los gráficos de la bandeja, para que las tarjetas se alineen


def _escala(colores: dict[str, str]) -> alt.Scale:
    return alt.Scale(domain=list(colores), range=list(colores.values()))


def _estilo(grafico: alt.Chart, alto: int) -> alt.Chart:
    return (
        grafico.properties(height=alto, background="transparent")
        .configure(font="IBM Plex Sans, sans-serif")
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
            labelColor=_TEXTO,
            titleColor=_SUAVE,
            labelFontSize=11,
            orient="bottom",
            title=None,
            symbolType="circle",
            symbolSize=70,
            columnPadding=14,
            rowPadding=4,
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
    """Noticias por día de publicación (hora de Panamá) y prioridad del tema al que pertenecen."""
    orden = {etiqueta: i for i, etiqueta in enumerate(COLORES_BANDA)}
    conteo: Counter[tuple[str, str]] = Counter()
    for t in temas:
        prioridad = ETIQUETAS_BANDA.get(t["banda"], t["banda"])
        for n in t["noticias"]:
            fecha = n.get("fecha_publicacion_panama") or n.get("fecha_deteccion_panama")
            if fecha:
                conteo[(fecha[:10], prioridad)] += 1
    return [
        {"Día": d, "Prioridad": p, "Noticias": c, "Orden": orden.get(p, 9)}
        for (d, p), c in sorted(conteo.items())
    ]


def periodo_noticias(temas: list[dict]) -> str:
    """Agrupa por día, semana o mes según el rango de fechas, para que las barras se lean
    igual con 3 noticias que con un año de snapshot."""
    dias = sorted({f["Día"] for f in datos_noticias_por_dia(temas)})
    if len(dias) < 2:
        return "día"
    rango = (date.fromisoformat(dias[-1]) - date.fromisoformat(dias[0])).days
    return "día" if rango <= 45 else "semana" if rango <= 210 else "mes"


_MESES = MESES_CORTOS


def _inicio_periodo(dia: date, periodo: str) -> date:
    if periodo == "semana":
        return dia - timedelta(days=dia.weekday())  # lunes
    if periodo == "mes":
        return dia.replace(day=1)
    return dia


def _siguiente(inicio: date, periodo: str) -> date:
    if periodo == "semana":
        return inicio + timedelta(days=7)
    if periodo == "mes":
        return (inicio + timedelta(days=32)).replace(day=1)
    return inicio + timedelta(days=1)


def _etiqueta_periodo(inicio: date, periodo: str) -> str:
    mes = _MESES[inicio.month - 1]
    if periodo == "mes":
        return f"{mes} {inicio:%y}"
    return f"{inicio.day} {mes}"


def datos_noticias_por_periodo(temas: list[dict]) -> list[dict]:
    """Noticias por periodo y prioridad, con los periodos vacíos en cero para que los huecos
    se vean (un día sin noticias no desaparece del eje)."""
    periodo = periodo_noticias(temas)
    orden = {etiqueta: i for i, etiqueta in enumerate(COLORES_BANDA)}
    conteo: Counter[tuple[date, str]] = Counter()
    for f in datos_noticias_por_dia(temas):
        inicio = _inicio_periodo(date.fromisoformat(f["Día"]), periodo)
        conteo[(inicio, f["Prioridad"])] += f["Noticias"]
    if not conteo:
        return []
    inicios = [i for i, _ in conteo]
    filas = []
    actual, ultimo = min(inicios), max(inicios)
    while actual <= ultimo:
        etiqueta = _etiqueta_periodo(actual, periodo)
        presentes = [(p, c) for (i, p), c in conteo.items() if i == actual] or [("Alta", 0)]
        for prioridad, cantidad in presentes:
            filas.append(
                {
                    "Periodo": actual.isoformat(),
                    "Etiqueta": etiqueta,
                    "Prioridad": prioridad,
                    "Noticias": cantidad,
                    "Orden": orden.get(prioridad, 9),
                }
            )
        actual = _siguiente(actual, periodo)
    return filas


def datos_aportes(temas: list[dict], limite: int = 8) -> list[dict]:
    """Aporte de cada componente al puntaje de los primeros temas del ranking."""
    orden = {nombre: i for i, nombre in enumerate(COLORES_COMPONENTE)}
    filas = []
    for t in temas[:limite]:
        etiqueta = f"#{t['posicion']} {_corto(t['titulo'], 28)}"
        for clave, c in t["componentes"].items():
            nombre = NOMBRES_COMPONENTES.get(clave, clave)
            filas.append(
                {
                    "Tema": etiqueta,
                    "Titular": t["titulo"],
                    "Posición": t["posicion"],
                    "Componente": nombre,
                    "Orden": orden.get(nombre, 9),
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


def colores_evidencia(temas: list[dict]) -> dict[str, str]:
    """Colores de los estados que aparecen en el gráfico (para su leyenda)."""
    presentes = {f["Estado"] for f in datos_evidencia(temas)}
    return {e: c for e, c in COLORES_ESTADO.items() if e in presentes}


def colores_prioridad(temas: list[dict]) -> dict[str, str]:
    presentes = {f["Prioridad"] for f in datos_noticias_por_dia(temas)}
    return {f"Prioridad {p.lower()}": c for p, c in COLORES_BANDA.items() if p in presentes}


def grafico_evidencia(temas: list[dict]) -> alt.Chart:
    datos = alt.Data(values=datos_evidencia(temas))
    dona = (
        alt.Chart(datos)
        .mark_arc(innerRadius=56, outerRadius=82, padAngle=0.01)
        .encode(
            theta=alt.Theta("Temas:Q", stack=True),
            color=alt.Color("Estado:N", scale=_escala(COLORES_ESTADO), legend=None),
            tooltip=["Estado:N", "Temas:Q"],
        )
    )
    return _estilo(dona, ALTO)


def grafico_noticias_por_dia(temas: list[dict]) -> alt.Chart:
    filas = datos_noticias_por_periodo(temas)
    totales: Counter[str] = Counter()
    for f in filas:
        totales[f["Periodo"]] += f["Noticias"]
    # Aire sobre la barra más alta, para que no toque el borde del gráfico.
    tope = max(totales.values(), default=0)
    tope = tope + max(1, round(tope * 0.15))
    etiquetas = list(dict.fromkeys(f["Etiqueta"] for f in filas))
    barras = (
        alt.Chart(alt.Data(values=filas))
        .mark_bar(cornerRadiusTopLeft=1, cornerRadiusTopRight=1)
        .encode(
            # Eje por bandas: cada etiqueta queda centrada bajo su barra.
            x=alt.X(
                "Etiqueta:O",
                title=None,
                sort=etiquetas,  # cronológico
                scale=alt.Scale(paddingInner=0.25, paddingOuter=0.15),
                axis=alt.Axis(
                    # Con muchos periodos las etiquetas se inclinan para no tocarse.
                    labelAngle=-40 if len(etiquetas) > 4 else 0,
                    labelAlign="right" if len(etiquetas) > 4 else "center",
                    labelOverlap="greedy",
                    ticks=False,
                ),
            ),
            y=alt.Y(
                "sum(Noticias):Q",
                title=None,
                scale=alt.Scale(domain=[0, tope]),
                axis=alt.Axis(tickMinStep=1, format="d", domain=False, ticks=False),
            ),
            color=alt.Color("Prioridad:N", scale=_escala(COLORES_BANDA), legend=None),
            order=alt.Order("Orden:Q"),
            tooltip=[
                alt.Tooltip("Etiqueta:O", title="Periodo"),
                "Prioridad:N",
                alt.Tooltip("sum(Noticias):Q", title="Noticias"),
            ],
        )
    )
    return _estilo(barras, ALTO)


def grafico_aportes(temas: list[dict], limite: int = 8) -> alt.Chart:
    datos = alt.Data(values=datos_aportes(temas, limite))
    barras = (
        alt.Chart(datos)
        .mark_bar(size=16, cornerRadius=1)
        .encode(
            y=alt.Y(
                "Tema:N",
                title=None,
                sort=alt.EncodingSortField("Posición", order="ascending"),
                axis=alt.Axis(labelLimit=190, labelPadding=8, ticks=False, domain=False),
            ),
            x=alt.X(
                "sum(Aporte):Q",
                title="Puntaje (0-100)",
                scale=alt.Scale(domain=[0, 100]),
                axis=alt.Axis(tickCount=5),
            ),
            color=alt.Color("Componente:N", scale=_escala(COLORES_COMPONENTE), legend=None),
            order=alt.Order("Orden:Q"),
            tooltip=["Titular:N", "Componente:N", "Aporte:Q"],
        )
    )
    return _estilo(barras, max(ALTO - 30, 34 * min(len(temas), limite)))


def grafico_desglose(tema: dict) -> alt.Chart:
    datos = alt.Data(values=datos_desglose(tema))
    orden = list(COLORES_COMPONENTE)
    y = alt.Y("Componente:N", title=None, sort=orden, axis=alt.Axis(labelLimit=200))
    fondo = (
        alt.Chart(datos)
        .mark_bar(size=12, cornerRadius=1, color="#E8E3D8")
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
        .mark_bar(size=12, cornerRadius=1)
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
