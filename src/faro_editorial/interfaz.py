"""Lógica de la interfaz (issue #16), separada de Streamlit para poder probarla.

La interfaz lee data/processed/bandeja.json (lo genera faro_editorial.bandeja). Si no existe pero
hay una base cargada, la genera; si no hay base, explica cómo cargar el snapshot. Todo funciona
sin internet.

El texto de las fuentes es dato, no formato: se escapa antes de mostrarlo como Markdown para que
un titular no pueda inyectar enlaces, imágenes ni formato en la pantalla.
"""

import json
import re
from pathlib import Path
from typing import Any

from faro_editorial.bandeja import NOMBRE_BANDEJA, escribir_bandeja, generar_bandeja
from faro_editorial.carga import NOMBRE_DB

ETIQUETAS_ESTADO = {
    "insuficiente": "Insuficiente",
    "parcial": "Parcial",
    "suficiente_para_borrador": "Suficiente para borrador",
}
ETIQUETAS_BANDA = {"alto": "Alto", "medio": "Medio", "bajo": "Bajo"}
# "Prioridad" es femenino: "Prioridad alta", no "Prioridad alto".
PRIORIDAD = {"alto": "alta", "medio": "media", "bajo": "baja"}
NOMBRES_COMPONENTES = {
    "R": "Relevancia",
    "I": "Impacto potencial",
    "U": "Urgencia",
    "N": "Novedad",
    "E": "Evidencia disponible",
}

_MARKDOWN_ESPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<])")


def escapar_md(texto: str | None) -> str:
    """Muestra el texto de una fuente tal cual, sin que se interprete como Markdown o HTML."""
    if not texto:
        return ""
    return _MARKDOWN_ESPECIAL.sub(r"\\\1", texto.replace("\n", " "))


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
        and (not temas_editoriales or (t["tema"] or "sin clasificar") in temas_editoriales)
    ]


def filas_bandeja(temas: list[dict]) -> list[dict[str, Any]]:
    """Filas de la tabla de la bandeja (texto plano: la tabla no interpreta Markdown)."""
    return [
        {
            "#": t["posicion"],
            "Puntaje": t["puntaje"],
            "Banda": ETIQUETAS_BANDA.get(t["banda"], t["banda"]),
            "Evidencia": ETIQUETAS_ESTADO.get(t["estado_evidencia"], t["estado_evidencia"]),
            "Tema": t["tema"] or "sin clasificar",
            "Titular": t["titulo"],
            "Fuentes indep.": len(t.get("procedencias_independientes") or t["procedencias"]),
            "Fecha original (Panamá)": t["fecha_original_panama"] or "sin fecha",
        }
        for t in temas
    ]


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
                f"Verificar {len(pendientes)} dato(s) pendiente(s) antes de pasar a borrador."
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
    """Etiquetas de banda y evidencia con el puntaje. Solo usa valores propios del sistema
    (banda, estado, puntaje), nunca texto de las fuentes, así que el HTML es seguro."""
    banda = tema["banda"] if tema["banda"] in ETIQUETAS_BANDA else "bajo"
    estado = tema["estado_evidencia"] if tema["estado_evidencia"] in ETIQUETAS_ESTADO else "parcial"
    puntaje = float(tema["puntaje"])
    ancho = min(max(puntaje, 0.0), 100.0)
    return (
        '<div class="chips">'
        f'<span class="etiqueta banda-{banda}">Prioridad {PRIORIDAD[banda]}</span>'
        f'<span class="etiqueta ev-{estado}">Evidencia {ETIQUETAS_ESTADO[estado].lower()}</span>'
        f'<span class="puntaje">Puntaje <b>{puntaje:.1f}</b><small>/100</small>'
        f'<span class="barra"><span style="width:{ancho:.0f}%"></span></span></span>'
        "</div>"
    )


def resumen_html(resumen: dict) -> str:
    """Tarjetas con los totales de la bandeja (solo números propios del sistema)."""
    altos = int(resumen["por_banda"].get("alto", 0))
    insuficientes = int(resumen["por_estado_evidencia"].get("insuficiente", 0))
    return (
        '<div class="kpis">'
        f'<div class="kpi"><b>{int(resumen["grupos"])}</b> temas</div>'
        f'<div class="kpi kpi-alto"><b>{altos}</b> de prioridad alta</div>'
        f'<div class="kpi kpi-insuficiente"><b>{insuficientes}</b> con evidencia insuficiente</div>'
        f'<div class="kpi"><b>{int(resumen["noticias"])}</b> noticias</div>'
        "</div>"
    )
