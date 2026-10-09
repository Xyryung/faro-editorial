"""Recorte del snapshot para la demo: N noticias repartidas en los días previos al corte.

Como pide el reto, las noticias son de los 30 días previos al corte (meta: 200 registros únicos;
mínimo operativo: 100, con al menos 20 de TVN) y se registra la cobertura efectiva. Las N
noticias se reparten por día (en cada ronda, una de cada día) y, dentro de cada día, alternando
medios, así todos los días y todos los medios quedan representados y un medio con muchas notas
no tapa a los demás.

Indicadores, sismos y fuentes se copian sin cambios. El manifest se actualiza con el SHA-256 y
la cantidad del nuevo noticias.csv y registra el recorte y su cobertura, así la carga verifica la
integridad igual que con el snapshot completo.

Uso (desde la raíz del repo):
    uv run python -m faro_editorial.recorte --n 300 --dias 30 --destino data/demo300/raw
    # luego, con DATA_DIR=data/demo300:  carga, agrupacion, clasificacion y bandeja
"""

import argparse
import csv
import json
import shutil
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from faro_editorial.carga import sha256_archivo
from faro_editorial.settings import get_settings

NOTICIAS = "noticias.csv"
MANIFEST = "manifest.json"
SIN_CAMBIOS = ("indicadores.csv", "eventos.geojson", "fuentes.json")
# Sección 5 del reto: meta y mínimo operativo de la muestra de noticias.
META, MINIMO, MINIMO_TVN = 200, 100, 20


def _fecha(fila: dict) -> str:
    # Fechas ISO 8601 en UTC: el orden de texto es el orden cronológico.
    return fila.get("fecha_publicacion") or fila.get("fecha_deteccion") or ""


def _iso(fecha: datetime) -> str:
    return fecha.isoformat().replace("+00:00", "Z")


def _alternar_medios(filas: list[dict]) -> list[dict]:
    """Ordena las filas de un día alternando medios (la más reciente de cada medio, luego la
    siguiente…), para que un medio con muchas notas no tape a los demás."""
    por_medio: dict[str, list[dict]] = {}
    for x in filas:  # ya vienen de la más reciente a la más antigua
        por_medio.setdefault((x.get("medio") or "").strip().lower(), []).append(x)
    orden: list[dict] = []
    while any(por_medio.values()):
        for medio in sorted(por_medio, key=lambda m: -len(por_medio[m])):
            if por_medio[medio]:
                orden.append(por_medio[medio].pop(0))
    return orden


def repartir_por_dia(filas: list[dict], n: int) -> list[dict]:
    """Hasta n filas repartidas por día: en cada ronda se toma una de cada día, alternando
    medios dentro del día (de la más reciente a la más antigua de cada medio)."""
    por_dia: dict[str, list[dict]] = {}
    for x in sorted(filas, key=lambda x: (_fecha(x), x["id_noticia"]), reverse=True):
        por_dia.setdefault(_fecha(x)[:10], []).append(x)
    por_dia = {dia: _alternar_medios(lista) for dia, lista in por_dia.items()}
    elegidas: list[dict] = []
    while len(elegidas) < n and any(por_dia.values()):
        for dia in sorted(por_dia, reverse=True):
            if por_dia[dia] and len(elegidas) < n:
                elegidas.append(por_dia[dia].pop(0))
    return elegidas


def recortar(origen: Path, destino: Path, n: int, dias: int = 30) -> dict:
    """Escribe en destino el snapshot con n noticias de los `dias` previos al corte del
    manifest, repartidas por día. Devuelve el resumen con la cobertura efectiva."""
    origen, destino = Path(origen), Path(destino)
    if n < 1 or dias < 1:
        raise ValueError("n y dias deben ser al menos 1")
    if (destino / MANIFEST).exists():
        raise FileExistsError(f"{destino} ya tiene un snapshot: no se sobrescribe.")
    manifest = json.loads((origen / MANIFEST).read_text(encoding="utf-8"))
    corte = datetime.fromisoformat(manifest["fecha_corte_utc"].replace("Z", "+00:00"))
    desde, hasta = _iso(corte - timedelta(days=dias)), _iso(corte)

    with (origen / NOTICIAS).open(encoding="utf-8", newline="") as f:
        lector = csv.DictReader(f)
        columnas = lector.fieldnames or []
        filas = list(lector)
    en_ventana = [x for x in filas if _fecha(x) and desde <= _fecha(x) < hasta]
    elegidas = repartir_por_dia(en_ventana, n)
    ids = {x["id_noticia"] for x in elegidas}

    destino.mkdir(parents=True, exist_ok=True)
    with (destino / NOTICIAS).open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=columnas)
        escritor.writeheader()
        escritor.writerows(x for x in filas if x["id_noticia"] in ids)  # orden original
    for nombre in SIN_CAMBIOS:
        if (origen / nombre).exists():
            shutil.copy2(origen / nombre, destino / nombre)

    fechas = sorted(_fecha(x) for x in elegidas)
    medios = Counter(x.get("medio", "") for x in elegidas)
    de_tvn = sum(c for m, c in medios.items() if m.strip().upper() == "TVN")
    recorte = {
        "criterio": (
            f"{n} noticias de los {dias} días previos al corte, repartidas por día (en cada "
            "ronda, una de cada día alternando medios dentro del día, de la más reciente a la más "
            "antigua; fecha de publicación o, si falta, detección)"
        ),
        "ventana": {"desde": desde, "hasta": hasta},
        "noticias_originales": len(filas),
        "noticias_en_la_ventana": len(en_ventana),
        "noticias_recortadas": len(elegidas),
        # Cobertura efectiva (el reto pide registrarla).
        "cobertura": {
            "desde": fechas[0] if fechas else None,
            "hasta": fechas[-1] if fechas else None,
            "dias_con_noticias": len({f[:10] for f in fechas}),
            "por_medio": dict(medios.most_common()),
            "de_tvn": de_tvn,
        },
        "meta_reto": {"meta": META, "minimo": MINIMO, "minimo_tvn": MINIMO_TVN},
        "cumple_minimo": len(elegidas) >= MINIMO and de_tvn >= MINIMO_TVN,
        "sha256_noticias_original": sha256_archivo(origen / NOTICIAS),
    }
    archivo = manifest["archivos"][NOTICIAS]
    archivo["cantidad"] = len(elegidas)
    archivo["sha256"] = sha256_archivo(destino / NOTICIAS)
    manifest["recorte"] = recorte
    manifest.setdefault("transformaciones", []).append(
        f"Recorte para la demo: {recorte['criterio']}, {len(elegidas)} de {len(en_ventana)} "
        "noticias de la ventana. Indicadores, sismos y fuentes sin cambios. Las respuestas "
        "crudas siguen en el snapshot completo."
    )
    (destino / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return recorte


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=300, help="noticias que se conservan")
    parser.add_argument("--dias", type=int, default=30, help="días previos al corte")
    parser.add_argument("--origen", type=Path, help="carpeta raw del snapshot completo")
    parser.add_argument("--destino", type=Path, required=True, help="carpeta raw del recorte")
    args = parser.parse_args(argv)
    origen = args.origen or get_settings().raw_dir
    try:
        resumen = recortar(origen, args.destino, args.n, args.dias)
    except FileExistsError as e:
        raise SystemExit(str(e)) from None
    cobertura = resumen["cobertura"]
    print(
        f"Recorte: {resumen['noticias_recortadas']} de {resumen['noticias_en_la_ventana']} "
        f"noticias de la ventana ({resumen['ventana']['desde']} → {resumen['ventana']['hasta']})"
    )
    print(
        f"Cobertura: {cobertura['desde']} → {cobertura['hasta']}, "
        f"{cobertura['dias_con_noticias']} días, {cobertura['de_tvn']} de TVN, "
        f"{len(cobertura['por_medio'])} medios"
    )
    if not resumen["cumple_minimo"]:
        print(f"AVISO: no llega al mínimo del reto ({MINIMO} registros, {MINIMO_TVN} de TVN).")
    print(f"Snapshot recortado en: {args.destino}")


if __name__ == "__main__":
    main()
