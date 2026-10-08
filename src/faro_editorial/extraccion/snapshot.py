"""Extracción del snapshot (issue #3) con el formato de data/CONTRATO.md.

Escribe en la carpeta de salida noticias.csv, fuentes.json, indicadores.csv,
eventos.geojson y manifest.json (SHA-256, consultas y transformaciones), y luego valida
el resultado con la etapa de carga: lo que la carga acepta es lo que se entrega.

Ventana: la misma que usa la carga (VENTANA_DESDE y VENTANA_HASTA de .env), para noticias y
sismos. --desde/--hasta la reemplazan. El perfil entrenamiento no usa la ventana de la demo.

Uso (desde la raíz del repo):
    uv run python -m faro_editorial.extraccion                    # demo, ventana de .env
    uv run python -m faro_editorial.extraccion --perfil entrenamiento --desde 2026-07-01
    uv run python -m faro_editorial.extraccion --solo noticias --sobrescribir
"""

import argparse
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from faro_editorial.carga import (
    ARCHIVO_EVENTOS,
    ARCHIVO_FUENTES,
    ARCHIVO_INDICADORES,
    ARCHIVO_MANIFEST,
    ARCHIVO_NOTICIAS,
    cargar_snapshot,
)
from faro_editorial.contrato import parse_fecha_utc
from faro_editorial.extraccion.banco_mundial import escribir_indicadores, extraer_banco_mundial
from faro_editorial.extraccion.config import (
    PERFILES,
    RUTA_CONFIG,
    ConfigExtraccion,
    Perfil,
    cargar_config,
)
from faro_editorial.extraccion.gdelt import extraer_gdelt
from faro_editorial.extraccion.http import ClienteHTTP, ErrorExtraccion
from faro_editorial.extraccion.normalizar import (
    ALCANCE_DESCRIPCION,
    ALCANCE_TITULO_IMAGEN,
    COLUMNAS_NOTICIAS,
    FilaNoticia,
    dominio,
)
from faro_editorial.extraccion.usgs import extraer_usgs
from faro_editorial.extraccion.web import extraer_web

VERSION_SNAPSHOT = "panama-senales-evidencias-v1"
FAMILIAS = ("noticias", "indicadores", "eventos")
ARCHIVOS_POR_FAMILIA = {
    "noticias": (ARCHIVO_NOTICIAS, ARCHIVO_FUENTES),
    "indicadores": (ARCHIVO_INDICADORES,),
    "eventos": (ARCHIVO_EVENTOS,),
}
MINIMO_NOTICIAS, MINIMO_TVN = 100, 20  # mínimos operativos de la sección 6 del reto

LICENCIA_NOTICIAS = (
    "Metadatos (titular, URL, fechas, idioma) de feeds RSS, sitemaps de noticias y GDELT; "
    "sin licencia abierta sobre el contenido de los medios. La descripción del RSS es solo "
    "para análisis interno y no se redistribuye (decisión #35). GDELT: uso libre con cita a "
    "The GDELT Project (https://www.gdeltproject.org)."
)
LICENCIA_EVENTOS = (
    "Catálogo USGS (FDSN Event); confirmar condiciones de elementos de terceros. Uso solo "
    "para hechos sísmicos."
)
TRANSFORMACIONES = [
    "Fechas convertidas a UTC (ISO 8601). fecha_publicacion: pubDate del RSS o "
    "news:publication_date del sitemap. fecha_deteccion: seendate de GDELT.",
    "Titulares: entidades HTML decodificadas, etiquetas eliminadas, espacios normalizados.",
    "Deduplicación por URL normalizada (https, sin www, sin fragmento, sin barra final ni "
    "parámetros utm_*). Se conserva la URL original. Al fusionar, la fila principal es la "
    "que tiene fecha de publicación; la fecha de detección se completa con GDELT.",
    "id_noticia = prefijo del dominio + '-' + 12 primeros caracteres del SHA-256 de la URL "
    "normalizada (estable aunque la noticia llegue por otro origen).",
    "Ventana [desde, hasta) con la misma regla que la carga: fecha de publicación y, si "
    "falta, de detección. Una noticia sin ninguna fecha se conserva y se cuenta.",
    "tema queda vacío: lo asigna la clasificación, nunca la consulta de búsqueda.",
    "descripcion: <description> del RSS sin etiquetas HTML, recortada a 1000 caracteres; "
    "solo análisis interno (decisión #35). content:encoded se descarta. alcance_texto = "
    "titular_descripcion si hay descripción, titular_metadatos si no, "
    "titulo_imagen_sitemap si el título viene de image:title (sitemaps mensuales de TVN).",
    "Sitemaps mensuales de TVN: últimos meses de la ventana, sin las rutas excluidas en la "
    "configuración; fecha_publicacion = lastmod (igual o posterior a la publicación) solo si "
    "cae en el mes del sitemap, con un día de tolerancia.",
    "Fechas no estándar se leen con el formato y la zona horaria declarados por fuente en "
    "config/extraccion_v1.yaml (p. ej. Panamá América: mes/día/año, hora de Panamá).",
    "Banco Mundial: cuadrícula completa país × indicador × año; sin observación = valor "
    "vacío (sin gapfill ni mrv). Unidad tomada de la configuración.",
    "USGS: mismo período que las noticias; GeoJSON guardado tal como llega.",
]


@dataclass
class ResultadoExtraccion:
    salida: Path
    manifest: dict[str, Any]
    noticias: list[FilaNoticia] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


# --- Noticias: fusión y ventana -------------------------------------------------------


def combinar(
    filas: list[FilaNoticia], prioridad: Sequence[str]
) -> tuple[list[FilaNoticia], dict[str, int]]:
    """Una fila por URL normalizada. Principal: la que tiene fecha de publicación, luego la
    que tiene titular (no título de imagen), luego la de la fuente con mayor prioridad."""
    orden = {fuente: i for i, fuente in enumerate(prioridad)}
    grupos: dict[str, list[FilaNoticia]] = {}
    for fila in filas:
        grupos.setdefault(fila.clave, []).append(fila)

    resultado, entre_origenes = [], 0
    for grupo in grupos.values():
        principal = min(
            grupo,
            key=lambda f: (
                not isinstance(f.fecha_publicacion, datetime),
                f.alcance_texto == ALCANCE_TITULO_IMAGEN,
                orden.get(f.fuente, len(orden)),
            ),
        )
        fusionada = FilaNoticia(**vars(principal))
        for otra in grupo:
            if fusionada.fecha_deteccion is None and otra.fecha_deteccion is not None:
                fusionada.fecha_deteccion = otra.fecha_deteccion
            if fusionada.idioma is None and otra.idioma:
                fusionada.idioma = otra.idioma
            if fusionada.descripcion is None and otra.descripcion:
                fusionada.descripcion = otra.descripcion
                fusionada.alcance_texto = ALCANCE_DESCRIPCION
        entre_origenes += len({f.origen for f in grupo}) > 1
        resultado.append(fusionada)
    estadisticas = {
        "filas_extraidas": len(filas),
        "duplicados_fusionados": len(filas) - len(resultado),
        "noticias_vistas_en_varios_origenes": entre_origenes,
    }
    return resultado, estadisticas


def filtrar_ventana(
    filas: list[FilaNoticia], desde: datetime, hasta: datetime
) -> tuple[list[FilaNoticia], dict[str, int]]:
    """Misma regla que la carga: manda la fecha de publicación y, si falta (GDELT), la de
    detección. Así la carga no rechaza nada por ventana de lo que escribe el extractor."""
    dentro, fuera, sin_fecha = [], 0, 0
    for fila in filas:
        fecha = fila.fecha_referencia
        if fecha is None:
            sin_fecha += 1
            dentro.append(fila)
        elif desde <= fecha < hasta:
            dentro.append(fila)
        else:
            fuera += 1
    return dentro, {"fuera_de_ventana": fuera, "sin_fecha": sin_fecha}


def _orden(fila: FilaNoticia) -> tuple[bool, float, str]:
    fecha = fila.fecha_referencia
    return (fecha is None, -fecha.timestamp() if fecha else 0.0, fila.id_noticia)


def es_tvn(fila: FilaNoticia) -> bool:
    return fila.medio == "TVN" or dominio(fila.url).endswith("tvn-2.com")


# --- Escritura ------------------------------------------------------------------------


def escribir_noticias(filas: list[FilaNoticia], ruta: Path) -> None:
    with ruta.open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS_NOTICIAS, lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(fila.a_csv() for fila in filas)


def escribir_fuentes(
    filas: list[FilaNoticia], config: ConfigExtraccion, perfil: Perfil, ruta: Path
) -> int:
    """Una entrada por fuente consultada (formato sugerido en data/CONTRATO.md), con los
    registros que aportó al snapshot y su reparto por medio."""
    por_fuente: dict[str, Counter] = {}
    for f in filas:
        por_fuente.setdefault(f.fuente, Counter())[f.medio] += 1
    fuentes: list[dict[str, Any]] = [
        {
            "fuente": w.id,
            "origen": w.origen,
            "medio": w.medio,
            "url": w.url,
            "consulta": w.tipo,
            "registros": sum(por_fuente.get(w.id, Counter()).values()),
        }
        for w in config.web
        if perfil in w.perfiles
    ]
    if config.gdelt and perfil in config.gdelt.perfiles:
        medios = por_fuente.get("gdelt", Counter())
        fuentes.append(
            {
                "fuente": "gdelt",
                "origen": "gdelt",
                "url": config.gdelt.url,
                "consulta": " | ".join(config.gdelt.consultas),
                "registros": sum(medios.values()),
                "registros_por_medio": dict(medios.most_common()),
            }
        )
    ruta.write_text(json.dumps(fuentes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(fuentes)


def _sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _iso(fecha: datetime) -> str:
    return fecha.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# --- Orquestación ---------------------------------------------------------------------


def preparar_salida(salida: Path, familias: Sequence[str], sobrescribir: bool) -> dict | None:
    """Comprueba que no se pise un snapshot anterior sin permiso. Con --sobrescribir mueve
    a _anteriores/ solo los archivos que se van a regenerar. Devuelve el manifest previo."""
    salida.mkdir(parents=True, exist_ok=True)
    afectados = [a for fam in familias for a in ARCHIVOS_POR_FAMILIA[fam]] + [ARCHIVO_MANIFEST]
    existentes = [a for a in afectados if (salida / a).exists()]
    previo = None
    if (salida / ARCHIVO_MANIFEST).exists():
        try:
            previo = json.loads((salida / ARCHIVO_MANIFEST).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previo = None
    if existentes and not sobrescribir:
        raise FileExistsError(
            f"Ya existen {', '.join(existentes)} en {salida}. Use --sobrescribir para "
            "moverlos a _anteriores/ y generar un snapshot nuevo."
        )
    if existentes:
        destino = salida / "_anteriores" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destino.mkdir(parents=True, exist_ok=True)
        for archivo in existentes:
            if archivo == ARCHIVO_MANIFEST:
                shutil.copy2(salida / archivo, destino / archivo)
            else:
                shutil.move(salida / archivo, destino / archivo)
    return previo


def extraer(
    config: ConfigExtraccion,
    perfil: Perfil,
    desde: datetime,
    hasta: datetime,
    salida: Path,
    familias: Sequence[str] = FAMILIAS,
    sobrescribir: bool = False,
    cliente: ClienteHTTP | None = None,
    ahora: datetime | None = None,
    progreso: Callable[[str], None] | None = None,
) -> ResultadoExtraccion:
    ahora = ahora or datetime.now(UTC)
    salida = Path(salida)
    previo = preparar_salida(salida, familias, sobrescribir)
    cliente = cliente or ClienteHTTP(
        config.user_agent,
        salida / "_respuestas",
        timeout_s=config.timeout_s,
        pausa_s=config.pausa_s,
        reintentos=config.reintentos,
        max_bytes=config.max_bytes,
        espera_429_s=config.espera_429_s,
    )
    resultado = ResultadoExtraccion(salida=salida, manifest={})
    resumen_fuentes: dict[str, Any] = {}
    archivos: dict[str, dict[str, Any]] = {}

    if "noticias" in familias:
        filas: list[FilaNoticia] = []
        prioridad = [f.id for f in config.web] + ["gdelt"]
        for fuente in config.web:
            if perfil not in fuente.perfiles:
                continue
            if progreso:
                progreso(f"{fuente.id} ({fuente.tipo})")
            try:
                nuevas, resumen = extraer_web(cliente, fuente, desde, hasta)
            except ErrorExtraccion as e:
                resultado.errores.append(f"{fuente.id}: {e}")
                resumen_fuentes[fuente.id] = {"error": str(e)}
                continue
            resumen["filas"] = len(nuevas)
            resumen_fuentes[fuente.id] = resumen
            resultado.errores += [f"{fuente.id}: {e}" for e in resumen["errores"]]
            filas += nuevas
        if config.gdelt and perfil in config.gdelt.perfiles:
            try:
                nuevas, resumen = extraer_gdelt(
                    cliente, config.gdelt, desde, hasta, config.medios, ahora, progreso
                )
                resumen["filas"] = len(nuevas)
                resumen_fuentes["gdelt"] = resumen
                resultado.errores += [f"gdelt: {e}" for e in resumen["errores"]]
                resultado.avisos += resumen["avisos"]
                filas += nuevas
            except ErrorExtraccion as e:
                resultado.errores.append(f"gdelt: {e}")
                resumen_fuentes["gdelt"] = {"error": str(e)}

        unicas, estadisticas = combinar(filas, prioridad)
        noticias, ventana = filtrar_ventana(unicas, desde, hasta)
        noticias.sort(key=_orden)
        estadisticas |= ventana | {
            "noticias": len(noticias),
            "noticias_tvn": sum(es_tvn(f) for f in noticias),
            "por_origen": dict(Counter(f.origen for f in noticias).most_common()),
        }
        resumen_fuentes["noticias"] = estadisticas
        resultado.noticias = noticias
        escribir_noticias(noticias, salida / ARCHIVO_NOTICIAS)
        cantidad_fuentes = escribir_fuentes(noticias, config, perfil, salida / ARCHIVO_FUENTES)
        archivos[ARCHIVO_NOTICIAS] = {"cantidad": len(noticias), "licencia": LICENCIA_NOTICIAS}
        archivos[ARCHIVO_FUENTES] = {
            "cantidad": cantidad_fuentes,
            "licencia": "Metadatos propios del extractor.",
        }

    if "indicadores" in familias and config.banco_mundial:
        if progreso:
            progreso("banco_mundial")
        try:
            filas_bm, resumen = extraer_banco_mundial(cliente, config.banco_mundial)
            escribir_indicadores(filas_bm, salida / ARCHIVO_INDICADORES)
            resumen_fuentes["banco_mundial"] = resumen
            archivos[ARCHIVO_INDICADORES] = {
                "cantidad": len(filas_bm),
                "licencia": config.banco_mundial.licencia,
            }
        except ErrorExtraccion as e:
            resultado.errores.append(f"banco_mundial: {e}")
            resumen_fuentes["banco_mundial"] = {"error": str(e)}

    if "eventos" in familias and config.usgs:
        if progreso:
            progreso("usgs")
        try:
            respuesta, resumen = extraer_usgs(cliente, config.usgs, desde, hasta)
            (salida / ARCHIVO_EVENTOS).write_bytes(respuesta.contenido)
            resumen_fuentes["usgs"] = resumen
            if aviso := resumen.get("aviso"):
                resultado.avisos.append(aviso)
            archivos[ARCHIVO_EVENTOS] = {
                "cantidad": resumen["eventos_recibidos"],
                "licencia": LICENCIA_EVENTOS,
            }
        except ErrorExtraccion as e:
            resultado.errores.append(f"usgs: {e}")
            resumen_fuentes["usgs"] = {"error": str(e)}

    for nombre, info in archivos.items():
        info["sha256"] = _sha256(salida / nombre)
        info["generado_en_esta_ejecucion"] = True

    # Archivos de familias que no se regeneraron: se declaran con su hash actual para que
    # la verificación de integridad siga cubriendo el paquete completo.
    archivos_previos = (previo or {}).get("archivos", {})
    for familia in FAMILIAS:
        for nombre in ARCHIVOS_POR_FAMILIA[familia]:
            if nombre in archivos or not (salida / nombre).exists():
                continue
            info = {k: v for k, v in archivos_previos.get(nombre, {}).items() if k != "sha256"}
            archivos[nombre] = {
                **info,
                "sha256": _sha256(salida / nombre),
                "generado_en_esta_ejecucion": False,
                "conservado_de": (previo or {}).get("fecha_corte_utc"),
            }

    manifest = {
        "version": VERSION_SNAPSHOT,
        "fecha_corte_utc": _iso(ahora),
        "perfil": perfil,
        "version_config": config.version,
        "ventana": {
            "desde": _iso(desde),
            "hasta": _iso(hasta),
            "aplica_a": [ARCHIVO_NOTICIAS, ARCHIVO_EVENTOS],
        },
        "familias": list(familias),
        "archivos": dict(sorted(archivos.items())),
        "resumen_fuentes": resumen_fuentes,
        "robots_txt": cliente.robots,
        "errores": resultado.errores,
        "avisos": resultado.avisos,
        "transformaciones": TRANSFORMACIONES,
        "consultas": cliente.consultas,
    }
    (salida / ARCHIVO_MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    resultado.manifest = manifest
    return resultado


# --- Línea de comandos ----------------------------------------------------------------


def _fecha_argumento(texto: str) -> datetime:
    try:
        fecha = parse_fecha_utc(texto)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    assert fecha is not None
    return fecha


def calcular_ventana(
    dias: int | None,
    dias_por_defecto: int,
    desde: datetime | None,
    hasta: datetime | None,
    ahora: datetime,
    ventana: tuple[datetime | None, datetime | None] = (None, None),
) -> tuple[datetime, datetime]:
    """Prioridad: --desde/--hasta; luego la ventana de .env (la misma que usa la carga);
    si falta, los últimos días (--dias o el valor por defecto) hasta ahora."""
    v_desde, v_hasta = ventana
    hasta = hasta or v_hasta or ahora
    if desde is None:
        if v_desde is not None and dias is None:
            desde = v_desde
        else:
            desde = hasta - timedelta(days=dias or dias_por_defecto)
    if desde >= hasta:
        raise ValueError(f"La ventana está vacía: desde {desde} no es anterior a hasta {hasta}")
    return desde, hasta


def _imprimir(resultado: ResultadoExtraccion) -> None:
    m = resultado.manifest
    ventana = m["ventana"]
    print(f"Perfil {m['perfil']} · ventana [{ventana['desde']}, {ventana['hasta']}) UTC")
    for nombre, info in m["resumen_fuentes"].items():
        if nombre == "noticias":
            continue
        if "error" in info:
            print(f"  {nombre}: ERROR {info['error']}")
        elif nombre == "gdelt":
            print(
                f"  gdelt: {info['filas']} filas en {info['llamadas']} llamadas "
                f"({len(info['errores'])} con error; {info['tramos_partidos']} tramos partidos)"
            )
            for consulta, cantidad in info["articulos_por_consulta"].items():
                print(f"      {cantidad:>6}  {consulta}")
        elif "filas" in info:
            extra = []
            if info.get("excluidos_por_ruta"):
                extra.append(f"{info['excluidos_por_ruta']} excluidos por ruta")
            if info.get("fecha_de_lastmod"):
                extra.append(f"{info['fecha_de_lastmod']} con fecha de lastmod")
            detalle = f" ({'; '.join(extra)})" if extra else ""
            print(f"  {nombre}: {info['filas']} filas de {info.get('items', '?')} ítems{detalle}")
    n = m["resumen_fuentes"].get("noticias")
    if n:
        print(
            f"Noticias: {n['noticias']} únicas ({n['duplicados_fusionados']} duplicados "
            f"fusionados, {n['fuera_de_ventana']} fuera de la ventana, {n['sin_fecha']} sin "
            f"fecha); de TVN: {n['noticias_tvn']}"
        )
        if n["noticias"] < MINIMO_NOTICIAS:
            print(f"  AVISO: menos de {MINIMO_NOTICIAS} noticias (mínimo operativo del reto)")
        if n["noticias_tvn"] < MINIMO_TVN:
            print(f"  AVISO: menos de {MINIMO_TVN} noticias de TVN (mínimo del reto)")
    for nombre, info in m["archivos"].items():
        estado = "nuevo" if info.get("generado_en_esta_ejecucion") else "conservado"
        print(f"  {nombre}: {info.get('cantidad', '?')} registros ({estado})")
    for aviso in resultado.avisos:
        print(f"AVISO: {aviso}")
    if resultado.errores:
        print(f"Errores ({len(resultado.errores)}), detalle en manifest.json:")
        for error in resultado.errores[:10]:
            print(f"  - {error[:160]}")


def _validar(salida: Path, desde: datetime, hasta: datetime) -> None:
    procesados = salida.parent / "processed"
    carga = cargar_snapshot(salida, procesados, desde, hasta)
    integridad = carga.integridad
    print(f"Validación con la carga: integridad {'OK' if integridad['ok'] else 'REVISAR'}")
    for nombre, info in integridad.get("archivos", {}).items():
        if info["estado"] != "ok":
            print(f"  {nombre}: {info['estado']}")
    for nombre, info in carga.reporte["archivos"].items():
        if "filas_leidas" in info and info["presente"]:
            print(
                f"  {nombre}: {info['filas_validas']} válidas, "
                f"{info['filas_rechazadas']} rechazadas"
            )
    print(f"Reporte de calidad: {carga.rutas['reporte']}")


def _progreso(mensaje: str) -> None:
    """Una línea que se reescribe en la consola, para saber que la extracción avanza."""
    print(f"\r  … {mensaje[:110]:<110}", end="", file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    from faro_editorial.settings import get_settings

    parser = argparse.ArgumentParser(
        prog="python -m faro_editorial.extraccion",
        description="Extrae noticias, indicadores y sismos con el contrato del snapshot.",
    )
    parser.add_argument("--perfil", choices=PERFILES, default="demo")
    parser.add_argument("--dias", type=int, help="Días hacia atrás desde --hasta")
    parser.add_argument("--desde", type=_fecha_argumento, help="Inicio (ISO 8601, UTC)")
    parser.add_argument("--hasta", type=_fecha_argumento, help="Fin exclusivo (ISO 8601, UTC)")
    parser.add_argument("--salida", type=Path, help="Carpeta del snapshot")
    parser.add_argument("--config", type=Path, default=RUTA_CONFIG)
    parser.add_argument("--solo", choices=FAMILIAS, action="append", help="Repetible")
    parser.add_argument("--sobrescribir", action="store_true")
    parser.add_argument("--sin-validar", action="store_true")
    args = parser.parse_args(argv)

    config = cargar_config(args.config)
    settings = get_settings()
    ahora = datetime.now(UTC)
    salida = args.salida or (
        settings.raw_dir if args.perfil == "demo" else settings.data_dir / "entrenamiento" / "raw"
    )
    demo = args.perfil == "demo"
    try:
        desde, hasta = calcular_ventana(
            args.dias,
            config.dias_por_defecto if demo else config.dias_entrenamiento,
            args.desde,
            args.hasta,
            ahora,
            (settings.ventana_desde, settings.ventana_hasta) if demo else (None, None),
        )
        resultado = extraer(
            config,
            args.perfil,
            desde,
            hasta,
            salida,
            familias=args.solo or FAMILIAS,
            sobrescribir=args.sobrescribir,
            ahora=ahora,
            progreso=_progreso,
        )
    except (FileExistsError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print("\r" + " " * 116 + "\r", end="", file=sys.stderr, flush=True)
    _imprimir(resultado)
    if resultado.noticias:
        print(f"Por medio: {dict(Counter(f.medio for f in resultado.noticias).most_common(8))}")
    if not args.sin_validar:
        _validar(Path(salida), desde, hasta)
    sin_noticias = "noticias" in (args.solo or FAMILIAS) and not resultado.noticias
    return 1 if sin_noticias else 0
