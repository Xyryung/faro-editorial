"""Lógica de la interfaz (issue #16), separada de Streamlit para poder probarla.

La interfaz lee data/processed/bandeja.json (lo genera faro_editorial.bandeja). Si no existe pero
hay una base cargada, la genera; si no hay base, explica cómo cargar el snapshot. Todo funciona
sin internet.

El texto de las fuentes es dato, no formato: se escapa antes de mostrarlo como Markdown para que
un titular no pueda inyectar enlaces, imágenes ni formato en la pantalla.
"""

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from faro_editorial.bandeja import NOMBRE_BANDEJA, escribir_bandeja, generar_bandeja
from faro_editorial.carga import NOMBRE_DB
from faro_editorial.contrato import plural

ETIQUETAS_ESTADO = {
    # En pantalla "Sin corroborar": una sola fuente y sin dato oficial. El valor interno
    # sigue siendo "insuficiente" (reglas, fichas y matriz no cambian).
    "insuficiente": "Sin corroborar",
    "parcial": "Parcial",
    "suficiente_para_borrador": "Suficiente para borrador",
}
# En pantalla la banda se llama "Prioridad" (femenino): Alta, Media, Baja.
ETIQUETAS_BANDA = {"alto": "Alta", "medio": "Media", "bajo": "Baja"}
MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
SIN_TEMA = "sin clasificar"
ETIQUETAS_TEMA = {
    "economia": "Economía",
    "logistica_canal": "Logística y Canal",
    "turismo": "Turismo",
    "servicios_publicos": "Servicios públicos",
    "eventos_naturales": "Eventos naturales",
    "regulacion": "Regulación",
    "otro": "Otro",
    SIN_TEMA: "Sin clasificar",
}
NOMBRES_COMPONENTES = {
    "R": "Relevancia",
    "I": "Impacto potencial",
    "U": "Urgencia",
    "N": "Novedad",
    "E": "Evidencia disponible",
}

_MARKDOWN_ESPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<])")
# Signos de HTML y de Markdown que se convierten en entidades dentro de HTML propio.
_ESPECIAL_HTML = set("&<>\"'\\`*_{}[]()#!|~")


def escapar_md(texto: str | None) -> str:
    """Muestra el texto de una fuente tal cual, sin que se interprete como Markdown o HTML."""
    if not texto:
        return ""
    return _MARKDOWN_ESPECIAL.sub(r"\\\1", texto.replace("\n", " "))


def _codigo(texto: str | None) -> str:
    """ID dentro de `código`: ahí Markdown no procesa escapes (la barra se vería), así que en vez
    de escapar se dejan solo los caracteres de un ID (letras, números, punto, dos puntos y
    guion). Un ID manipulado no puede cerrar el bloque ni formar un enlace."""
    return re.sub(r"[^\w.:-]", "", texto or "")


def escapar_html(texto: str | None) -> str:
    """Texto de una fuente dentro de HTML propio: escapa el HTML y además convierte en entidades
    los signos de Markdown, así no se interpreta ni como etiqueta ni como enlace."""
    if not texto:
        return ""
    return "".join(f"&#{ord(c)};" if c in _ESPECIAL_HTML else c for c in texto.replace("\n", " "))


def pasos_html(pasos: list[str]) -> str:
    """Lista numerada de pasos (texto propio del sistema, escapado de todas formas)."""
    filas = "".join(
        f'<div class="paso"><span>{i}</span><p>{escapar_html(p)}</p></div>'
        for i, p in enumerate(pasos, 1)
    )
    return f'<div class="pasos">{filas}</div>'


def fila_html(texto: str, tipo: str = "info", codigo: str | None = None) -> str:
    """Una fila con borde de color (tipo: info, aviso u ok). Todo el texto va escapado."""
    tipo = tipo if tipo in {"info", "aviso", "ok"} else "info"
    prefijo = f"<code>{escapar_html(codigo)}</code> " if codigo else ""
    return f'<div class="fila fila-{tipo}"><p>{prefijo}{escapar_html(texto)}</p></div>'


def noticia_html(noticia: dict) -> str:
    """Fila de una noticia: titular, medio, fechas y enlace (solo http/https)."""
    partes = [escapar_html(noticia.get("medio"))]
    partes.append(
        f"publicada {escapar_html(noticia.get('fecha_publicacion_panama') or 'sin fecha')}"
    )
    if noticia.get("fecha_deteccion_panama"):
        partes.append(f"detectada {escapar_html(noticia['fecha_deteccion_panama'])}")
    url = str(noticia.get("url") or "")
    if re.match(r"https?://", url, re.IGNORECASE):
        partes.append(
            f'<a href="{html.escape(url, quote=True)}" target="_blank" '
            'rel="noopener noreferrer">Abrir nota</a>'
        )
    return (
        f'<div class="fila fila-info"><p class="fila-titulo">{escapar_html(noticia.get("titulo"))}'
        f'</p><p class="fila-meta">{" · ".join(partes)}</p></div>'
    )


def medios_html(grupos: list[list[str]]) -> str:
    """Una pastilla por procedencia independiente; si agrupa varios medios, lo indica."""
    pastillas = []
    for medios in grupos:
        nombre = " + ".join(escapar_html(m) for m in medios)
        extra = "<em>posible agencia replicada</em>" if len(medios) > 1 else ""
        pastillas.append(f'<span class="medio">{nombre}{extra}</span>')
    return f'<div class="chips">{"".join(pastillas)}</div>'


def cargar_bandeja(processed_dir: Path, regenerar: bool = False) -> tuple[dict | None, str]:
    """Devuelve (bandeja, mensaje). Sin base cargada, bandeja es None y el mensaje dice
    qué hacer."""
    processed_dir = Path(processed_dir)
    ruta_bandeja = processed_dir / NOMBRE_BANDEJA
    ruta_db = processed_dir / NOMBRE_DB
    # Si la base se volvió a cargar después de generar la bandeja, la bandeja está vieja.
    desactualizada = (
        ruta_bandeja.exists()
        and ruta_db.exists()
        and (ruta_db.stat().st_mtime > ruta_bandeja.stat().st_mtime)
    )
    if ruta_db.exists() and (regenerar or desactualizada or not ruta_bandeja.exists()):
        bandeja = generar_bandeja(processed_dir)
        escribir_bandeja(bandeja, processed_dir)
        return bandeja, "Bandeja generada desde la base cargada."
    if ruta_bandeja.exists():
        return json.loads(
            ruta_bandeja.read_text(encoding="utf-8")
        ), "Bandeja leída de bandeja.json."
    return None, (
        "No hay snapshot cargado. Copia los archivos en data/raw/ (o la carpeta raw/ del paquete "
        "de datos) y ejecuta:  uv run python -m faro_editorial.carga"
    )


def filtrar(
    temas: list[dict],
    bandas: list[str] | None = None,
    estados: list[str] | None = None,
    temas_editoriales: list[str] | None = None,
) -> list[dict]:
    """Filtra la bandeja sin cambiar su orden (el ranking lo define el puntaje)."""
    return [
        t
        for t in temas
        if (not bandas or t["banda"] in bandas)
        and (not estados or t["estado_evidencia"] in estados)
        and (not temas_editoriales or (t["tema"] or SIN_TEMA) in temas_editoriales)
    ]


def etiqueta_tema(tema: str | None) -> str:
    """Nombre legible del tema editorial ("logistica_canal" → "Logística y Canal")."""
    clave = tema or SIN_TEMA
    return ETIQUETAS_TEMA.get(clave, clave.replace("_", " ").capitalize())


def filas_bandeja(temas: list[dict]) -> list[dict[str, Any]]:
    """Filas de la tabla de la bandeja (texto plano: la tabla no interpreta Markdown)."""
    return [
        {
            "#": t["posicion"],
            "Puntaje": t["puntaje"],
            "Prioridad": ETIQUETAS_BANDA.get(t["banda"], t["banda"]),
            "Evidencia": ETIQUETAS_ESTADO.get(t["estado_evidencia"], t["estado_evidencia"]),
            "Tema": etiqueta_tema(t["tema"]),
            "Titular": t["titulo"],
            "Fuentes": len(t.get("procedencias_independientes") or t["procedencias"]),
            "Fecha original": t["fecha_original_panama"] or "sin fecha",
        }
        for t in temas
    ]


def texto_datos(bandeja: dict) -> str:
    """Explica de dónde salen los datos de la bandeja, sin jerga interna."""
    fecha = bandeja["referencia_panama"]
    origen = bandeja["origen_referencia"]
    if origen.startswith("corte del snapshot"):
        corte = f"Datos con corte al {fecha} (hora de Panamá)."
    elif origen.startswith("noticia más reciente"):
        corte = f"Datos hasta la noticia más reciente: {fecha} (hora de Panamá)."
    else:
        corte = "El snapshot no tiene fechas; la urgencia se calcula con la hora actual."
    if bandeja["agrupacion"].startswith("una noticia por grupo"):
        grupos = "Cada noticia es un tema: todavía no se agrupan las del mismo evento."
    else:
        grupos = "Las noticias del mismo evento están agrupadas en un tema."
    return f"{corte} {grupos}"


def leyenda_html(colores: dict[str, str]) -> str:
    """Leyenda de un gráfico como HTML propio: se acomoda al ancho y no se corta."""
    items = "".join(
        f'<span><i style="background:{color}"></i>{escapar_html(nombre)}</span>'
        for nombre, color in colores.items()
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", color)
    )
    return f'<div class="leyenda">{items}</div>'


def filas_componentes(tema: dict) -> list[dict[str, Any]]:
    return [
        {
            "Componente": f"{k} · {NOMBRES_COMPONENTES.get(k, k)}",
            "Valor (0-1)": round(c["valor"], 2),
            "Peso": c["peso"],
            "Aporte": c["aporte"],
            "Criterio": c["criterio"],
            "Calculado por": c.get("fuente", "reglas"),
        }
        for k, c in tema["componentes"].items()
    ]


def accion_recomendada(tema: dict) -> list[str]:
    """Acción sugerida al editor según la evidencia y la prioridad. Nunca recomienda publicar:
    lo más que sugiere es pasar a borrador, sujeto a revisión humana."""
    acciones: list[str] = []
    estado = tema["estado_evidencia"]
    independientes = len(tema.get("procedencias_independientes") or tema["procedencias"])
    pendientes = tema.get("pendientes") or []

    if tema["banda"] == "bajo":
        acciones.append("Prioridad baja: revisar solo si hay capacidad.")
    elif tema["banda"] == "alto":
        acciones.append("Prioridad alta: asignar la revisión pronto.")

    if estado == "insuficiente":
        acciones.append(
            "Investigar antes de redactar: buscar una segunda fuente independiente o un dato "
            "oficial que respalde el hecho."
        )
    elif estado == "parcial":
        if pendientes:
            acciones.append(
                f"Verificar {plural(len(pendientes), 'dato pendiente', 'datos pendientes')} "
                "antes de pasar a borrador."
            )
        if independientes < 2:
            acciones.append("Buscar una segunda fuente independiente.")
    else:
        acciones.append(
            "Puede pasar a borrador, sujeto a revisión humana. Aprobar un borrador no significa "
            "publicarlo."
        )

    novedad = tema["componentes"].get("N", {}).get("criterio", "")
    if "recirculada" in novedad:
        acciones.append(
            "Verificar la fecha original: puede ser una noticia antigua que vuelve a circular."
        )
    return acciones


def etiquetas_html(tema: dict) -> str:
    """Prioridad, evidencia y puntaje del tema como una línea de datos (rótulo y valor). Solo
    usa valores propios del sistema, nunca texto de las fuentes, así que el HTML es seguro."""
    banda = tema["banda"] if tema["banda"] in ETIQUETAS_BANDA else "bajo"
    estado = tema["estado_evidencia"] if tema["estado_evidencia"] in ETIQUETAS_ESTADO else "parcial"
    puntaje = float(tema["puntaje"])
    ancho = min(max(puntaje, 0.0), 100.0)
    return (
        '<div class="datos-tema">'
        f'<div class="dato"><span>Prioridad</span><b class="banda-{banda}">'
        f"{ETIQUETAS_BANDA[banda]}</b></div>"
        f'<div class="dato"><span>Evidencia</span><b class="ev-{estado}">'
        f"{ETIQUETAS_ESTADO[estado]}</b></div>"
        f'<div class="dato"><span>Puntaje</span><em><b>{puntaje:.1f}</b> de 100'
        f'<i class="barra"><i style="width:{ancho:.0f}%"></i></i></em></div>'
        "</div>"
    )


def fecha_legible(texto: str | None) -> str:
    """ "2025-09-20 08:00" → "20 sep 2025, 08:00". Si no tiene ese formato, la deja igual."""
    if not texto:
        return "sin fecha"
    try:
        fecha = datetime.strptime(texto[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return texto
    return f"{fecha.day} {MESES_CORTOS[fecha.month - 1]} {fecha.year}, {fecha:%H:%M}"


def version_legible(version: str) -> str:
    """ "reglas-v1.0" → "Reglas v1.0"."""
    return version.replace("-v", " v", 1).capitalize()


def tarjetas_kpi(resumen: dict) -> list[str]:
    """Los cuatro indicadores de la bandeja, cada uno como su propia tarjeta (solo números
    propios del sistema)."""
    grupos = int(resumen["grupos"])
    altos = int(resumen["por_banda"].get("alto", 0))
    insuficientes = int(resumen["por_estado_evidencia"].get("insuficiente", 0))
    suficientes = int(resumen["por_estado_evidencia"].get("suficiente_para_borrador", 0))

    def tarjeta(clase: str, titulo: str, valor: int, detalle: str) -> str:
        return (
            f'<div class="kpi {clase}"><span class="kpi-titulo">{titulo}</span>'
            f'<b>{valor}</b><span class="kpi-detalle">{detalle}</span></div>'
        )

    return [
        tarjeta("kpi-temas", "Temas", grupos, f"{int(resumen['noticias'])} noticias agrupadas"),
        tarjeta("kpi-alto", "Prioridad alta", altos, f"de {grupos} temas"),
        tarjeta(
            "kpi-insuficiente", "Sin corroborar", insuficientes, "una sola fuente, sin dato oficial"
        ),
        tarjeta("kpi-suficiente", "Listos para borrador", suficientes, "sujetos a revisión humana"),
    ]


def resumen_html(resumen: dict) -> str:
    """Los cuatro indicadores juntos, en una sola fila."""
    return f'<div class="kpis">{"".join(tarjetas_kpi(resumen))}</div>'


def lineas_consulta(respuesta: Any) -> dict[str, Any]:
    """Textos de la respuesta de la consulta (búsqueda híbrida, #13), listos para mostrarse
    como Markdown. Titular, medio, ID y motivos pueden traer texto de las fuentes o de la
    pregunta: se escapan para que no se conviertan en enlaces, imágenes ni formato (T07)."""
    if respuesta.abstencion:
        return {
            "abstencion": True,
            "motivo": escapar_md(respuesta.motivo),
            "falta": escapar_md(respuesta.falta),
        }
    citas = []
    for cita in respuesta.citas:
        extra = f" · Noul {cita.prob_noul}" if cita.prob_noul is not None else ""
        citas.append(
            f"- **{cita.puntaje:.2f}** `{_codigo(cita.id_noticia)}` "
            f"{escapar_md(cita.titulo)} ({escapar_md(cita.medio)}){extra}"
        )
    return {
        "abstencion": False,
        "nota": escapar_md(respuesta.motivo_respaldo),
        "citas": citas,
    }
