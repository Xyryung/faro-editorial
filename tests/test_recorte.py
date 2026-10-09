"""Recorte del snapshot para la demo: las N noticias más recientes, con integridad OK."""

import json
from pathlib import Path

import pytest

from faro_editorial.carga import cargar_snapshot, sha256_archivo
from faro_editorial.recorte import recortar


def test_recorte_deja_las_mas_recientes_y_la_carga_lo_acepta(raw: Path, tmp_path):
    destino = tmp_path / "demo" / "raw"
    resumen = recortar(raw, destino, 2)
    assert resumen["noticias_recortadas"] == 2
    manifest = json.loads((destino / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["archivos"]["noticias.csv"]["sha256"] == sha256_archivo(
        destino / "noticias.csv"
    )
    assert manifest["archivos"]["noticias.csv"]["cantidad"] == 2
    assert "Recorte para la demo" in manifest["transformaciones"][-1]
    # Los demás archivos no cambian.
    assert (destino / "indicadores.csv").read_bytes() == (raw / "indicadores.csv").read_bytes()
    resultado = cargar_snapshot(destino, tmp_path / "demo" / "processed")
    assert resultado.integridad["ok"]


def test_no_pisa_un_recorte_existente(raw: Path, tmp_path):
    destino = tmp_path / "demo" / "raw"
    recortar(raw, destino, 2)
    with pytest.raises(FileExistsError):
        recortar(raw, destino, 2)


def test_reparte_por_dia_y_no_solo_los_ultimos():
    from faro_editorial.recorte import repartir_por_dia

    filas = [
        {"id_noticia": f"d{d}-{i}", "fecha_publicacion": f"2026-09-{d:02d}T{10 + i}:00:00Z"}
        for d in (1, 2, 3)
        for i in range(5)
    ]
    elegidas = repartir_por_dia(filas, 6)
    # Dos por día (la más reciente de cada día en cada ronda), no las seis del último día.
    assert sorted(x["id_noticia"] for x in elegidas) == [
        "d1-3",
        "d1-4",
        "d2-3",
        "d2-4",
        "d3-3",
        "d3-4",
    ]


def test_dentro_del_dia_alterna_medios():
    from faro_editorial.recorte import repartir_por_dia

    filas = [
        {"id_noticia": f"tvn-{i}", "medio": "TVN", "fecha_publicacion": f"2026-09-01T2{i}:00:00Z"}
        for i in range(4)
    ] + [
        {
            "id_noticia": "prensa-0",
            "medio": "La Prensa",
            "fecha_publicacion": "2026-09-01T08:00:00Z",
        }
    ]
    # Aunque la de La Prensa sea la más vieja del día, entra antes que la segunda de TVN.
    assert [x["id_noticia"] for x in repartir_por_dia(filas, 2)] == ["tvn-3", "prensa-0"]
