"""Bandeja priorizada de punta a punta (CU-01): carga → grupos → contexto oficial → puntaje.

Lee la base que genera la carga y escribe data/processed/bandeja.json, listo para la interfaz
(#16): cada tema con su puntaje desglosado, estado de evidencia, citas oficiales, pendientes y
noticias con fecha en UTC y en hora de Panamá. Funciona sin internet (T10).

Grupos: si existe data/processed/grupos.jsonl (lo produce la agrupación, #9) se usa; cada
línea es {"id_grupo": "...", "ids_noticias": ["..."], "tema": "..." (opcional)}. Las noticias
que no estén en ningún grupo forman su propio grupo, para que nada quede oculto.

La descripción del RSS no se incluye en la salida (decisión #35): la interfaz muestra el
titular y el enlace.

Uso:  uv run python -m faro_editorial.bandeja [--top 5]
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, ValidationError

from faro_editorial import __version__
from faro_editorial.carga import NOMBRE_DB, NOMBRE_REPORTE
from faro_editorial.contexto import ZONA_PANAMA, ContextoOficial
from faro_editorial.contrato import Noticia
from faro_editorial.puntaje import GrupoNoticias, MotorPuntaje, Puntuacion

NOMBRE_GRUPOS = "grupos.jsonl"
NOMBRE_BANDEJA = "bandeja.json"


class GrupoArchivo(BaseModel):
    id_grupo: str
    ids_noticias: list[str]
    tema: str | None = None


def _hora_panama(fecha: datetime | None) -> str | None:
    return fecha.astimezone(ZONA_PANAMA).strftime("%Y-%m-%d %H:%M") if fecha else None


def _iso(fecha: datetime | None) -> str | None:
    return fecha.astimezone(UTC).isoformat() if fecha else None


def leer_noticias(ruta_db: Path) -> list[Noticia]:
    with duckdb.connect(str(ruta_db), read_only=True) as con:
        cursor = con.execute("SELECT * FROM noticias ORDER BY id_noticia")
        columnas = [c[0] for c in cursor.description]
        filas = [dict(zip(columnas, f, strict=True)) for f in cursor.fetchall()]
    return [Noticia.model_validate(f) for f in filas]


def _id_unico(id_grupo: str, usados: set[str], advertencias: list[str]) -> str:
    """Evita que dos grupos compartan ID: la bandeja cruzaría puntajes y noticias."""
    nuevo, n = id_grupo, 2
    while nuevo in usados:
        nuevo, n = f"{id_grupo}-{n}", n + 1
    if nuevo != id_grupo:
        advertencias.append(f"ID de grupo repetido '{id_grupo}': se renombra a '{nuevo}'")
    usados.add(nuevo)
    return nuevo


def armar_grupos(
    noticias: list[Noticia], ruta_grupos: Path | None
) -> tuple[list[GrupoNoticias], str, list[str]]:
    """Devuelve los grupos, cómo se agruparon y advertencias (IDs desconocidos, etc.)."""
    por_id = {n.id_noticia: n for n in noticias}
    if ruta_grupos is None or not ruta_grupos.exists():
        return [GrupoNoticias.de_noticia(n) for n in noticias], "una noticia por grupo", []

    grupos: list[GrupoNoticias] = []
    advertencias: list[str] = []
    asignadas: set[str] = set()
    ids_usados: set[str] = set()
    lineas = ruta_grupos.read_text(encoding="utf-8").splitlines()
    for numero, linea in enumerate(lineas, start=1):
        if not linea.strip():
            continue
        try:
            g = GrupoArchivo.model_validate_json(linea)
        except ValidationError as e:
            advertencias.append(
                f"{ruta_grupos.name}, línea {numero}: inválida ({e.errors()[0]['msg']})"
            )
            continue
        miembros = []
        for id_noticia in g.ids_noticias:
            if id_noticia not in por_id:
                advertencias.append(f"Grupo {g.id_grupo}: noticia {id_noticia} no está en la base")
            elif id_noticia in asignadas:
                advertencias.append(
                    f"Grupo {g.id_grupo}: noticia {id_noticia} ya estaba en otro grupo"
                )
            else:
                asignadas.add(id_noticia)
                miembros.append(por_id[id_noticia])
        if miembros:
            id_grupo = _id_unico(g.id_grupo, ids_usados, advertencias)
            grupos.append(GrupoNoticias(id_grupo=id_grupo, noticias=miembros, tema=g.tema))
        else:
            advertencias.append(f"Grupo {g.id_grupo}: sin noticias válidas, se omite")

    sueltas = [n for n in noticias if n.id_noticia not in asignadas]
    for n in sueltas:
        id_grupo = _id_unico(n.id_noticia, ids_usados, advertencias)
        grupos.append(GrupoNoticias(id_grupo=id_grupo, noticias=[n]))
    if sueltas:
        advertencias.append(f"{len(sueltas)} noticia(s) sin grupo: cada una forma su propio grupo")
    return grupos, f"{ruta_grupos.name} ({len(grupos) - len(sueltas)} grupos)", advertencias


def fecha_referencia(processed_dir: Path, noticias: list[Noticia]) -> tuple[datetime, str]:
    """El corte del snapshot (del manifest) hace la urgencia reproducible; si falta, la
    noticia más reciente."""
    ruta = processed_dir / NOMBRE_REPORTE
    if ruta.exists():
        corte = json.loads(ruta.read_text(encoding="utf-8")).get("integridad", {})
        if corte.get("fecha_corte_utc"):
            fecha_corte = datetime.fromisoformat(corte["fecha_corte_utc"])
            return fecha_corte, "corte del snapshot, según el manifest"
    fechas = [f for n in noticias if (f := n.fecha_publicacion or n.fecha_deteccion)]
    if fechas:
        return max(fechas), "noticia más reciente; el manifest no trae fecha de corte"
    return datetime.now(UTC), "momento de ejecución; el snapshot no tiene fechas"


def _tema_salida(posicion: int, grupo: GrupoNoticias, p: Puntuacion) -> dict[str, Any]:
    ordenadas = sorted(
        grupo.noticias,
        key=lambda n: n.fecha_publicacion or n.fecha_deteccion or datetime.max.replace(tzinfo=UTC),
    )
    fechas = grupo.todas_las_fechas
    return {
        "posicion": posicion,
        "id_grupo": p.id_grupo,
        "titulo": ordenadas[0].titulo,
        "tema": grupo.tema_efectivo,
        "puntaje": p.puntaje,
        "banda": p.banda,
        "estado_evidencia": p.estado_evidencia,
        "motivo_estado": p.motivo_estado,
        "componentes": {k: c.model_dump() for k, c in p.componentes.items()},
        "procedencias": p.procedencias,
        "procedencias_independientes": p.procedencias_independientes,
        # T03: la fecha original se muestra siempre, para no presentar algo viejo como nuevo.
        "fecha_original_utc": _iso(min(fechas)) if fechas else None,
        "fecha_original_panama": _hora_panama(min(fechas)) if fechas else None,
        "vinculos_oficiales": [v.model_dump() for v in p.vinculos],
        "pendientes": p.pendientes,
        "noticias": [
            {
                "id_noticia": n.id_noticia,
                "titulo": n.titulo,
                "medio": n.medio,
                "url": n.url,
                "origen": n.origen,
                "alcance_texto": n.alcance_texto,
                "fecha_publicacion_utc": _iso(n.fecha_publicacion),
                "fecha_publicacion_panama": _hora_panama(n.fecha_publicacion),
                "fecha_deteccion_utc": _iso(n.fecha_deteccion),
                "fecha_deteccion_panama": _hora_panama(n.fecha_deteccion),
            }
            for n in ordenadas
        ],
        "habilita_publicacion": p.habilita_publicacion,
        "aviso": p.aviso,
    }


def generar_bandeja(processed_dir: Path, motor: MotorPuntaje | None = None) -> dict[str, Any]:
    processed_dir = Path(processed_dir)
    ruta_db = processed_dir / NOMBRE_DB
    noticias = leer_noticias(ruta_db)
    grupos, agrupacion, advertencias = armar_grupos(noticias, processed_dir / NOMBRE_GRUPOS)
    referencia, origen_referencia = fecha_referencia(processed_dir, noticias)
    motor = motor or MotorPuntaje(contexto_oficial=ContextoOficial.desde_duckdb(ruta_db))

    ranking = motor.ranking(grupos, referencia)
    por_id = {g.id_grupo: g for g in grupos}
    estados = [p.estado_evidencia for p in ranking]
    return {
        "version": __version__,
        "generado_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "referencia_utc": _iso(referencia),
        "referencia_panama": _hora_panama(referencia),
        "origen_referencia": origen_referencia,
        "version_reglas": motor.reglas.version,
        "version_criterios": motor.criterios.version,
        "agrupacion": agrupacion,
        "advertencias": advertencias,
        "resumen": {
            "noticias": len(noticias),
            "grupos": len(grupos),
            "por_banda": {b: sum(p.banda == b for p in ranking) for b in motor.reglas.rangos},
            "por_estado_evidencia": {e: estados.count(e) for e in motor.reglas.estados_evidencia},
        },
        "temas": [_tema_salida(i, por_id[p.id_grupo], p) for i, p in enumerate(ranking, 1)],
    }


def escribir_bandeja(bandeja: dict[str, Any], processed_dir: Path) -> Path:
    ruta = Path(processed_dir) / NOMBRE_BANDEJA
    ruta.write_text(json.dumps(bandeja, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ruta


def main(argv: list[str] | None = None) -> None:
    from faro_editorial.settings import get_settings

    parser = argparse.ArgumentParser(description="Genera la bandeja priorizada (bandeja.json).")
    parser.add_argument("--top", type=int, default=5, help="temas a mostrar en consola")
    args = parser.parse_args(argv)

    s = get_settings()
    if not (s.processed_dir / NOMBRE_DB).exists():
        raise SystemExit(
            "No hay base cargada: ejecuta antes  uv run python -m faro_editorial.carga"
        )
    bandeja = generar_bandeja(s.processed_dir)
    ruta = escribir_bandeja(bandeja, s.processed_dir)

    referencia = f"{bandeja['referencia_panama']} hora de Panamá"
    print(f"Referencia: {referencia} ({bandeja['origen_referencia']})")
    print(f"Agrupación: {bandeja['agrupacion']} · {bandeja['version_reglas']}")
    for advertencia in bandeja["advertencias"]:
        print(f"  Aviso: {advertencia}")
    if not bandeja["temas"]:
        reporte = s.processed_dir / NOMBRE_REPORTE
        archivos = (
            json.loads(reporte.read_text(encoding="utf-8")).get("archivos", {})
            if reporte.exists()
            else {}
        )
        if not archivos.get("noticias.csv", {}).get("presente"):
            print(
                f"\nNo hay snapshot cargado: copia los archivos en {s.raw_dir} (o la carpeta "
                "raw/ del paquete de datos) y ejecuta  uv run python -m faro_editorial.carga"
            )
        else:
            print(
                "\nNo hay noticias válidas en la base. Revisa en reporte_calidad.json los "
                "rechazos (p. ej. 'fuera de la ventana de fechas') y VENTANA_DESDE/HASTA."
            )
        return
    print(f"\nLos {min(args.top, len(bandeja['temas']))} temas que merecen revisión:")
    for t in bandeja["temas"][: args.top]:
        print(
            f"{t['posicion']:>2}. [{t['puntaje']:5.1f} {t['banda']:<5}] {t['titulo']}\n"
            f"    evidencia: {t['estado_evidencia']} · fuentes: {', '.join(t['procedencias'])}"
            f" ({len(t['procedencias_independientes'])} independiente/s)"
        )
    print(f"\nBandeja completa: {ruta}")
    print("La prioridad ordena qué revisar; no habilita publicación.")


if __name__ == "__main__":
    main()
