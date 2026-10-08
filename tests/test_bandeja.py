"""Bandeja priorizada de punta a punta (CU-01): de la base cargada a bandeja.json."""

import json
from pathlib import Path

import pytest

from faro_editorial.bandeja import escribir_bandeja, generar_bandeja, main
from faro_editorial.carga import cargar_snapshot


@pytest.fixture
def processed(raw: Path, tmp_path: Path) -> Path:
    destino = tmp_path / "processed"
    cargar_snapshot(raw, destino)
    return destino


def test_bandeja_ordenada_con_todo_lo_que_necesita_la_ficha(processed: Path):
    bandeja = generar_bandeja(processed)
    temas = bandeja["temas"]

    assert bandeja["agrupacion"] == "una noticia por grupo"
    assert bandeja["resumen"]["noticias"] == bandeja["resumen"]["grupos"] == 3
    assert [t["posicion"] for t in temas] == [1, 2, 3]
    assert [t["puntaje"] for t in temas] == sorted((t["puntaje"] for t in temas), reverse=True)
    for t in temas:
        assert set(t["componentes"]) == {"R", "I", "U", "N", "E"}
        assert all(c["criterio"] for c in t["componentes"].values())
        assert t["estado_evidencia"] in {"insuficiente", "parcial", "suficiente_para_borrador"}
        assert t["habilita_publicacion"] is False
        assert t["noticias"] and t["procedencias"]
    assert bandeja["version_reglas"] == "reglas-v1.0"


def test_referencia_es_el_corte_del_snapshot(processed: Path):
    bandeja = generar_bandeja(processed)
    assert bandeja["origen_referencia"] == "corte del snapshot, según el manifest"
    assert bandeja["referencia_utc"] == "2025-10-01T00:00:00+00:00"
    assert bandeja["referencia_panama"] == "2025-09-30 19:00"


def test_fechas_en_utc_y_hora_de_panama(processed: Path):
    temas = {t["id_grupo"]: t for t in generar_bandeja(processed)["temas"]}
    n001 = temas["n001"]["noticias"][0]
    assert n001["fecha_publicacion_utc"] == "2025-09-15T19:30:00+00:00"
    assert n001["fecha_publicacion_panama"] == "2025-09-15 14:30"
    # GDELT: solo detección; la publicación sigue vacía.
    n002 = temas["n002"]["noticias"][0]
    assert n002["fecha_publicacion_utc"] is None
    assert n002["fecha_deteccion_panama"] == "2025-09-16 07:00"
    assert temas["n002"]["fecha_original_panama"] == "2025-09-16 07:00"


def test_grupos_de_la_agrupacion_y_advertencias(processed: Path):
    (processed / "grupos.jsonl").write_text(
        '{"id_grupo": "g1", "ids_noticias": ["n001", "n007", "no-existe"], "tema": "economia"}\n'
        '{"id_grupo": "g2", "ids_noticias": ["n007"]}\n'
        "esto no es json\n",
        encoding="utf-8",
    )
    bandeja = generar_bandeja(processed)
    temas = {t["id_grupo"]: t for t in bandeja["temas"]}

    assert set(temas) == {"g1", "n002"}  # n002 sin grupo: forma el suyo
    assert [n["id_noticia"] for n in temas["g1"]["noticias"]] == ["n001", "n007"]
    assert temas["g1"]["tema"] == "economia"
    # Dos noticias del mismo medio: una sola procedencia.
    assert temas["g1"]["procedencias"] == ["tvn"]
    assert temas["g1"]["procedencias_independientes"] == [["tvn"]]
    advertencias = " | ".join(bandeja["advertencias"])
    assert "no-existe no está en la base" in advertencias
    assert "n007 ya estaba en otro grupo" in advertencias
    assert "g2: sin noticias válidas" in advertencias
    assert "línea 3: inválida" in advertencias
    assert "1 noticia(s) sin grupo" in advertencias


def test_no_incluye_la_descripcion_del_rss(raw: Path, tmp_path: Path):
    """Decisión #35: la descripción es solo para análisis interno."""
    (raw / "noticias.csv").write_text(
        "id_noticia,titulo,url,medio,fecha_publicacion,fecha_extraccion,origen,descripcion\n"
        "d1,Sube el PIB de Panamá,https://www.tvn-2.com/d1,TVN,2025-09-30,2025-10-01,"
        "tvn_rss,Texto privado del RSS\n",
        encoding="utf-8",
    )
    processed = tmp_path / "processed"
    cargar_snapshot(raw, processed)
    bandeja = generar_bandeja(processed)
    texto = json.dumps(bandeja, ensure_ascii=False)
    assert "Texto privado del RSS" not in texto
    assert "descripcion" not in bandeja["temas"][0]["noticias"][0]


def test_cita_oficial_llega_a_la_bandeja(raw: Path, tmp_path: Path):
    (raw / "noticias.csv").write_text(
        "id_noticia,titulo,url,medio,fecha_publicacion,fecha_extraccion,origen\n"
        "p1,El PIB de Panamá creció,https://www.tvn-2.com/p1,TVN,2025-09-30,2025-10-01,tvn_rss\n",
        encoding="utf-8",
    )
    processed = tmp_path / "processed"
    cargar_snapshot(raw, processed)
    (vinculo,) = generar_bandeja(processed)["temas"][0]["vinculos_oficiales"]
    assert vinculo["id_evidencia"] == "BM:PAN:NY.GDP.MKTP.KD.ZG:2023"
    assert "no una medición actual" in vinculo["cita"]


def test_comando_escribe_bandeja_y_muestra_top(processed: Path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(processed.parent))
    from faro_editorial.settings import get_settings

    get_settings.cache_clear()
    try:
        main(["--top", "2"])
    finally:
        get_settings.cache_clear()
    salida = capsys.readouterr().out
    assert "Los 2 temas que merecen revisión" in salida
    assert " 1. [" in salida and " 3. [" not in salida
    assert "no habilita publicación" in salida
    assert json.loads((processed / "bandeja.json").read_text(encoding="utf-8"))["temas"]


def test_escribir_bandeja(processed: Path):
    ruta = escribir_bandeja(generar_bandeja(processed), processed)
    assert ruta.name == "bandeja.json"
    assert json.loads(ruta.read_text(encoding="utf-8"))["version_reglas"] == "reglas-v1.0"


def test_comando_con_base_vacia_explica_que_revisar(raw: Path, tmp_path: Path, monkeypatch, capsys):
    (raw / "noticias.csv").write_text(
        "id_noticia,titulo,url,medio,fecha_extraccion,origen\n", encoding="utf-8"
    )
    cargar_snapshot(raw, tmp_path / "processed")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from faro_editorial.settings import get_settings

    get_settings.cache_clear()
    try:
        main([])
    finally:
        get_settings.cache_clear()
    salida = capsys.readouterr().out
    assert "No hay noticias en la base" in salida
    assert "temas que merecen revisión" not in salida


def test_ids_de_grupo_repetidos_no_cruzan_datos(processed: Path):
    # "n002" choca con el grupo propio de la noticia suelta n002, y "g1" se repite.
    (processed / "grupos.jsonl").write_text(
        '{"id_grupo": "n002", "ids_noticias": ["n001"]}\n'
        '{"id_grupo": "g1", "ids_noticias": ["n007"]}\n'
        '{"id_grupo": "g1", "ids_noticias": ["n002"]}\n',
        encoding="utf-8",
    )
    bandeja = generar_bandeja(processed)
    temas = {t["id_grupo"]: [n["id_noticia"] for n in t["noticias"]] for t in bandeja["temas"]}
    assert temas == {"n002": ["n001"], "g1": ["n007"], "g1-2": ["n002"]}
    assert "ID de grupo repetido 'g1': se renombra a 'g1-2'" in bandeja["advertencias"]


def test_id_de_grupo_que_choca_con_una_noticia_suelta(processed: Path):
    (processed / "grupos.jsonl").write_text(
        '{"id_grupo": "n002", "ids_noticias": ["n001", "n007"]}\n', encoding="utf-8"
    )
    bandeja = generar_bandeja(processed)
    temas = {t["id_grupo"]: [n["id_noticia"] for n in t["noticias"]] for t in bandeja["temas"]}
    assert temas == {"n002": ["n001", "n007"], "n002-2": ["n002"]}
