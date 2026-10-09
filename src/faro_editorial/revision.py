"""Revisión humana de las fichas (issue #17, secciones 7 y 8 del reto).

Una persona revisora decide el estado de cada tema: nuevo, en revisión, requiere evidencia,
aprobado como borrador o descartado (config/rules_v1.yaml). Aprobar un borrador no publica nada:
solo deja registrada la decisión.

Cada decisión se agrega como una línea a data/processed/fichas.jsonl con los campos del contrato
(sección 7): id_caso, modalidad, ids_fuente, afirmaciones, citas, puntaje, componentes,
estado_evidencia, borrador y estado_revision, más quién revisó, cuándo y su comentario. El
archivo solo crece: una decisión nueva no borra la anterior, así queda el historial ("documentar
revisiones"). El estado vigente de un tema es su última línea.

afirmaciones, citas y borrador se copian del borrador vigente del tema (borradores.jsonl, #15)
en el momento de guardar la decisión, así cada línea conserva el texto exacto que se revisó. Si
el tema no tiene borrador (o el borrador es una abstención), quedan vacíos: no se rellenan con
contenido inventado.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from faro_editorial.borradores import borrador_markdown, leer_borradores
from faro_editorial.contexto import ZONA_PANAMA
from faro_editorial.interfaz import (
    ETIQUETAS_ESTADO,
    NOMBRES_COMPONENTES,
    escapar_md,
    fecha_legible,
)
from faro_editorial.rules import Reglas

NOMBRE_FICHAS = "fichas.jsonl"
MODALIDAD = "TVN · principal (editorial)"
ESTADO_INICIAL = "nuevo"
ETIQUETAS_REVISION = {
    "nuevo": "Nuevo",
    "en_revision": "En revisión",
    "requiere_evidencia": "Requiere evidencia",
    "aprobado_como_borrador": "Aprobado como borrador",
    "descartado": "Descartado",
}


def fecha_revision_legible(fecha_utc: str) -> str:
    """La fecha de la revisión (guardada en UTC) en hora de Panamá, como el resto de la
    interfaz: "2026-10-09T00:44:49Z" → "8 oct 2026, 19:44"."""
    try:
        fecha = datetime.fromisoformat(fecha_utc.replace("Z", "+00:00")).astimezone(ZONA_PANAMA)
    except (ValueError, AttributeError):
        return str(fecha_utc)
    return fecha_legible(fecha.strftime("%Y-%m-%d %H:%M"))


def etiqueta_revision(estado: str) -> str:
    return ETIQUETAS_REVISION.get(estado, estado)


class Revision(BaseModel):
    """Lo que decide la persona revisora sobre un tema."""

    estado_revision: str
    revisor: str = Field(min_length=1, max_length=80)
    comentario: str = Field(default="", max_length=2000)

    @field_validator("revisor", "comentario", mode="before")
    @classmethod
    def _sin_espacios_sobrantes(cls, valor: object) -> object:
        return valor.strip() if isinstance(valor, str) else valor


def validar_estado(estado: str, reglas: Reglas) -> str:
    if estado not in reglas.estados_revision:
        raise ValueError(
            f"Estado de revisión no válido: {estado!r}. "
            f"Opciones: {', '.join(reglas.estados_revision)}"
        )
    return estado


def ids_fuente(tema: dict) -> list[str]:
    """IDs de las noticias del tema y de la evidencia oficial vinculada (BM, USGS)."""
    ids = [n["id_noticia"] for n in tema["noticias"]]
    ids += [v["id_evidencia"] for v in tema.get("vinculos_oficiales") or []]
    return list(dict.fromkeys(ids))


def ficha(
    tema: dict,
    revision: Revision,
    reglas: Reglas,
    ahora: datetime | None = None,
    borrador: dict | None = None,
) -> dict:
    """La ficha del tema con la decisión de revisión, en el formato de la sección 7. `borrador`
    es la línea vigente de borradores.jsonl para este tema (#15), si existe."""
    validar_estado(revision.estado_revision, reglas)
    con_borrador = bool(borrador) and not borrador.get("abstencion")
    return {
        "id_caso": tema["id_grupo"],
        "modalidad": MODALIDAD,
        "titulo": tema["titulo"],
        "ids_fuente": ids_fuente(tema),
        "afirmaciones": borrador["afirmaciones"] if con_borrador else [],
        "citas": borrador["citas"] if con_borrador else [],
        "puntaje": tema["puntaje"],
        "componentes": tema["componentes"],
        "estado_evidencia": tema["estado_evidencia"],
        "borrador": borrador["borrador"] if con_borrador else None,
        # Qué versión del borrador se revisó (None si no había borrador).
        "borrador_generado_utc": borrador.get("generado_utc") if con_borrador else None,
        "estado_revision": revision.estado_revision,
        "revisor": revision.revisor,
        "comentario": revision.comentario,
        "fecha_revision_utc": (ahora or datetime.now(UTC))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        # Aprobar un borrador no significa publicar (sección 8).
        "habilita_publicacion": False,
    }


def guardar_revision(
    processed_dir: Path,
    tema: dict,
    revision: Revision,
    reglas: Reglas,
    ahora: datetime | None = None,
) -> dict:
    """Agrega la decisión al historial (fichas.jsonl) y devuelve la ficha guardada. Copia el
    borrador vigente del tema (borradores.jsonl), si existe."""
    borrador = leer_borradores(processed_dir).get(tema["id_grupo"])
    registro = ficha(tema, revision, reglas, ahora, borrador)
    ruta = Path(processed_dir) / NOMBRE_FICHAS
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    return registro


def historial(processed_dir: Path) -> dict[str, list[dict]]:
    """Todas las decisiones guardadas, por id_caso y en orden. Una línea dañada se ignora (no
    tumba la interfaz)."""
    ruta = Path(processed_dir) / NOMBRE_FICHAS
    por_caso: dict[str, list[dict]] = {}
    if not ruta.exists():
        return por_caso
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        try:
            registro = json.loads(linea)
            por_caso.setdefault(registro["id_caso"], []).append(registro)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return por_caso


def estados_vigentes(processed_dir: Path) -> dict[str, str]:
    """Estado de revisión actual de cada tema revisado (su última decisión)."""
    return {caso: regs[-1]["estado_revision"] for caso, regs in historial(processed_dir).items()}


def texto_para_notion(tema: dict, ultima: dict | None, borrador: dict | None = None) -> str:
    """La ficha en Markdown, lista para pegar en la página "Casos y evidencias" de Notion. El
    texto de las fuentes va escapado para que no se convierta en enlaces o formato al pegarlo."""
    evidencia = ETIQUETAS_ESTADO.get(tema["estado_evidencia"], tema["estado_evidencia"])
    lineas = [
        f"## {escapar_md(tema['titulo'])}",
        "",
        f"- **ID del caso:** {tema['id_grupo']}",
        f"- **Modalidad:** {MODALIDAD}",
        f"- **Fecha original (Panamá):** {fecha_legible(tema.get('fecha_original_panama'))}",
        f"- **Puntaje:** {float(tema['puntaje']):.1f} de 100 (no es probabilidad de verdad)",
        f"- **Estado de evidencia:** {evidencia} — {escapar_md(tema['motivo_estado'])}",
        "",
        "**Fuentes**",
    ]
    for n in tema["noticias"]:
        lineas.append(
            f"- {n['id_noticia']} · {escapar_md(n.get('medio'))} · "
            f"{fecha_legible(n.get('fecha_publicacion_panama'))} · {n['url']}"
        )
    for v in tema.get("vinculos_oficiales") or []:
        lineas.append(f"- {v['id_evidencia']} · {escapar_md(v['cita'])}")
    lineas += ["", "**Puntaje desglosado**"]
    for clave, c in tema["componentes"].items():
        lineas.append(
            f"- {NOMBRES_COMPONENTES.get(clave, clave)}: {c['aporte']} de {c['peso']} — "
            f"{escapar_md(c['criterio'])}"
        )
    if tema.get("pendientes"):
        lineas += ["", "**Qué falta comprobar**"]
        lineas += [f"- {escapar_md(p)}" for p in tema["pendientes"]]
    if borrador:
        lineas += ["", borrador_markdown(borrador)]
    else:
        lineas += ["", "**Borrador:** pendiente (todavía no hay borrador con citas)."]
    lineas += ["", "**Revisión humana**"]
    if ultima is None:
        lineas.append("- Estado: Nuevo (sin revisar)")
    else:
        lineas += [
            f"- Estado: {etiqueta_revision(ultima['estado_revision'])}",
            f"- Persona revisora: {escapar_md(ultima['revisor'])}",
            f"- Fecha (hora de Panamá): {fecha_revision_legible(ultima['fecha_revision_utc'])}",
        ]
        if ultima.get("comentario"):
            lineas.append(f"- Comentario: {escapar_md(ultima['comentario'])}")
    lineas.append("- Aprobar como borrador no significa publicar.")
    return "\n".join(lineas)


# Colores del avance: gris lo pendiente, verde lo aprobado, ámbar lo que pide más evidencia.
COLORES_REVISION = {
    "nuevo": "#C9D0DC",
    "en_revision": "#7D8FA6",
    "requiere_evidencia": "#C98A4B",
    "aprobado_como_borrador": "#5E9C76",
    "descartado": "#8E86B0",
}


def conteo_revision(temas: list[dict], vigentes: dict[str, str]) -> dict[str, int]:
    """Cuántos temas hay en cada estado de revisión (los no revisados cuentan como "nuevo")."""
    conteo = dict.fromkeys(ETIQUETAS_REVISION, 0)
    for t in temas:
        estado = vigentes.get(t["id_grupo"], ESTADO_INICIAL)
        conteo[estado if estado in conteo else ESTADO_INICIAL] += 1
    return conteo


def avance_revision_html(conteo: dict[str, int]) -> str:
    """Avance de la revisión: cuántos temas ya decidió una persona, una barra con cada estado y
    el detalle con números. Solo usa valores propios del sistema."""
    total = sum(conteo.values())
    revisados = total - conteo.get(ESTADO_INICIAL, 0)
    partes = "".join(
        f'<span style="width:{100 * n / total:.2f}%;background:{COLORES_REVISION[e]}" '
        f'title="{ETIQUETAS_REVISION[e]}: {n}"></span>'
        for e, n in conteo.items()
        if n and total
    )
    filas = "".join(
        f'<li><i style="background:{COLORES_REVISION[e]}"></i>{ETIQUETAS_REVISION[e]}'
        f"<b>{n}</b></li>"
        for e, n in conteo.items()
    )
    return (
        '<div class="avance">'
        f'<p class="avance-total"><b>{revisados}</b> de {total} '
        f"{'tema revisado' if total == 1 else 'temas revisados'}</p>"
        f'<div class="avance-barra">{partes}</div>'
        f'<ul class="avance-lista">{filas}</ul>'
        "</div>"
    )
