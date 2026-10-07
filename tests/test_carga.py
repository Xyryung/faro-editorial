"""T01 · Archivo con fechas inválidas y nulos: validar, separar errores y conservar nulos
sin bloquear toda la carga.

Los archivos de tests/datos/t01/ son sintéticos: cada fila inválida está ahí a propósito.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from faro_editorial.carga import cargar_snapshot
from faro_editorial.contrato import parse_fecha_utc
from faro_editorial.settings import Settings


@pytest.fixture
def resultado(raw: Path, tmp_path: Path):
    return cargar_snapshot(raw, tmp_path / "processed")


def _consulta(resultado, sql: str):
    with duckdb.connect(str(resultado.rutas["db"]), read_only=True) as con:
        return con.execute(sql).fetchall()


def test_t01_filas_invalidas_no_bloquean_la_carga(resultado):
    archivos = resultado.reporte["archivos"]
    assert archivos["noticias.csv"]["filas_leidas"] == 8
    assert archivos["noticias.csv"]["filas_validas"] == 3
    assert archivos["noticias.csv"]["filas_rechazadas"] == 5
    assert archivos["indicadores.csv"]["filas_validas"] == 3
    assert archivos["indicadores.csv"]["filas_rechazadas"] == 3
    assert archivos["eventos.geojson"]["filas_validas"] == 2
    assert archivos["eventos.geojson"]["filas_rechazadas"] == 2
    assert _consulta(resultado, "SELECT id_noticia FROM noticias ORDER BY 1") == [
        ("n001",),
        ("n002",),
        ("n007",),
    ]


def test_t01_rechazos_con_motivo(resultado):
    lineas = resultado.rutas["rechazos"].read_text(encoding="utf-8").splitlines()
    rechazos = [json.loads(linea) for linea in lineas]
    assert len(rechazos) == 10
    assert all(r["motivos"] for r in rechazos)

    por_id = {(r["archivo"], r["id"]): r for r in rechazos}
    assert "fecha_publicacion" in por_id[("noticias.csv", "n003")]["motivos"][0]
    assert "titulo" in por_id[("noticias.csv", "n004")]["motivos"][0]
    assert "URL inválida" in por_id[("noticias.csv", "n005")]["motivos"][0]
    assert "posterior a fecha_extraccion" in por_id[("noticias.csv", "n006")]["motivos"][0]
    assert por_id[("noticias.csv", "n001")]["motivos"] == ["id duplicado: n001"]
    # El duplicado se rechaza; se conserva la primera aparición.
    assert por_id[("noticias.csv", "n001")]["fila"] == 7
    assert "magnitude" in por_id[("eventos.geojson", "us7000bbbb")]["motivos"][0]
    assert "latitude" in por_id[("eventos.geojson", "us7000cccc")]["motivos"][0]


def test_t01_nulos_se_conservan_y_no_se_rellenan_con_cero(resultado):
    filas = _consulta(
        resultado,
        "SELECT indicador_id, anio, valor FROM indicadores ORDER BY indicador_id, anio",
    )
    assert filas == [
        ("NY.GDP.MKTP.KD.ZG", 2023, 7.3),
        ("NY.GDP.MKTP.KD.ZG", 2024, None),
        ("SL.UEM.TOTL.ZS", 2024, 0.0),  # un cero real sigue siendo cero
    ]
    ((tema, alcance),) = _consulta(
        resultado, "SELECT tema, alcance_texto FROM noticias WHERE id_noticia = 'n001'"
    )
    assert tema is None
    assert alcance == "titular_metadatos"
    ((depth,),) = _consulta(resultado, "SELECT depth FROM eventos WHERE id = 'us7000dddd'")
    assert depth is None


def test_t01_fechas_en_utc_y_publicacion_separada_de_deteccion(resultado):
    filas = _consulta(
        resultado,
        "SELECT id_noticia, fecha_publicacion, fecha_deteccion FROM noticias ORDER BY 1",
    )
    assert filas == [
        # RFC 2822 con -0500 (RSS de TVN) convertido a UTC; sin fecha de detección.
        ("n001", datetime(2025, 9, 15, 19, 30), None),
        # GDELT: solo seendate, que es detección; la publicación queda nula, no se copia.
        ("n002", None, datetime(2025, 9, 16, 12, 0)),
        ("n007", datetime(2025, 9, 20, 13, 0), datetime(2025, 9, 20, 13, 5)),
    ]
    ((tiempo,),) = _consulta(resultado, "SELECT time FROM eventos WHERE id = 'us7000aaaa'")
    assert tiempo == datetime(2024, 1, 1, 0, 0)


def test_t01_reporte_de_calidad(resultado):
    reporte = json.loads(resultado.rutas["reporte"].read_text(encoding="utf-8"))
    assert reporte["totales"] == {
        "filas_leidas": 18,
        "filas_validas": 8,
        "filas_rechazadas": 10,
    }
    noticias = reporte["archivos"]["noticias.csv"]
    assert noticias["nulos_por_campo"]["fecha_publicacion"] == 1
    assert noticias["nulos_por_campo"]["fecha_deteccion"] == 1
    assert noticias["nulos_por_campo"]["tema"] == 1
    assert noticias["motivos_rechazo"]["id duplicado: n001"] == 1
    assert reporte["archivos"]["indicadores.csv"]["nulos_por_campo"]["valor"] == 1
    assert reporte["archivos"]["fuentes.json"] == {"presente": True, "entradas": 2}
    assert reporte["integridad"]["ok"] is True


def test_integridad_detecta_archivo_alterado(raw: Path, tmp_path: Path):
    with (raw / "noticias.csv").open("a", encoding="utf-8") as f:
        f.write("\n")
    integridad = cargar_snapshot(raw, tmp_path / "processed").integridad
    assert integridad["ok"] is False
    assert integridad["archivos"]["noticias.csv"]["estado"] == "hash distinto"
    assert integridad["archivos"]["indicadores.csv"]["estado"] == "ok"


def test_sin_manifest_se_carga_igual_y_se_reporta(raw: Path, tmp_path: Path):
    (raw / "manifest.json").unlink()
    resultado = cargar_snapshot(raw, tmp_path / "processed")
    assert resultado.integridad == {"manifest": "ausente", "ok": False, "archivos": {}}
    assert len(resultado.noticias.validos) == 3


def test_archivo_ausente_no_rompe_la_carga(raw: Path, tmp_path: Path):
    (raw / "eventos.geojson").unlink()
    resultado = cargar_snapshot(raw, tmp_path / "processed")
    assert resultado.reporte["archivos"]["eventos.geojson"]["presente"] is False
    assert resultado.integridad["archivos"]["eventos.geojson"]["estado"] == "archivo ausente"
    assert _consulta(resultado, "SELECT count(*) FROM eventos") == [(0,)]


def test_ventana_de_fechas_configurable(raw: Path, tmp_path: Path):
    resultado = cargar_snapshot(
        raw,
        tmp_path / "processed",
        desde=datetime(2025, 9, 16, tzinfo=UTC),
        hasta=datetime(2025, 10, 1, tzinfo=UTC),
    )
    validas = sorted(n.id_noticia for n in resultado.noticias.validos)
    # n001 y su duplicado (15/09) quedan fuera; n002 entra por su fecha de detección.
    assert validas == ["n002", "n007"]
    motivos = resultado.reporte["archivos"]["noticias.csv"]["motivos_rechazo"]
    assert motivos["fuera de la ventana de fechas"] == 2


def test_fila_con_columnas_de_mas_se_rechaza(raw: Path, tmp_path: Path):
    with (raw / "noticias.csv").open("a", encoding="utf-8") as f:
        f.write("n099,Titulo,https://a.org/x,A,es,,,2025-10-01T00:00:00Z,,gdelt,,extra\n")
    resultado = cargar_snapshot(raw, tmp_path / "processed")
    rechazo = next(r for r in resultado.rechazos if r.id == "n099")
    assert "columnas" in rechazo.motivos[0]


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("2025-09-15T14:30:00Z", datetime(2025, 9, 15, 14, 30, tzinfo=UTC)),
        ("2025-09-15T09:30:00-05:00", datetime(2025, 9, 15, 14, 30, tzinfo=UTC)),
        ("2025-09-15T14:30:00", datetime(2025, 9, 15, 14, 30, tzinfo=UTC)),
        ("2025-09-15", datetime(2025, 9, 15, tzinfo=UTC)),
        ("20250915T143000Z", datetime(2025, 9, 15, 14, 30, tzinfo=UTC)),
        ("Mon, 15 Sep 2025 09:30:00 -0500", datetime(2025, 9, 15, 14, 30, tzinfo=UTC)),
        (1704067200000, datetime(2024, 1, 1, tzinfo=UTC)),
        ("", None),
        ("NaN", None),
        (None, None),
    ],
)
def test_formatos_de_fecha(entrada, esperado):
    assert parse_fecha_utc(entrada) == esperado


@pytest.mark.parametrize("entrada", ["2025-02-30", "ayer", "15/09/2025", True])
def test_fechas_invalidas_fallan(entrada):
    with pytest.raises(ValueError):
        parse_fecha_utc(entrada)


def test_ventana_por_defecto_es_la_demo_en_hora_de_panama(monkeypatch):
    monkeypatch.delenv("VENTANA_DESDE", raising=False)
    monkeypatch.delenv("VENTANA_HASTA", raising=False)
    s = Settings(_env_file=None)
    # Medianoche de Panamá (UTC-5) = 05:00 UTC.
    assert s.ventana_desde == datetime(2025, 10, 1, 5, 0, tzinfo=UTC)
    assert s.ventana_hasta == datetime(2026, 10, 1, 5, 0, tzinfo=UTC)


def test_ventana_vacia_desactiva_el_filtro():
    s = Settings(_env_file=None, ventana_desde="", ventana_hasta="")
    assert s.ventana_desde is None and s.ventana_hasta is None


def test_ventana_respeta_el_borde_en_hora_de_panama(raw: Path, tmp_path: Path):
    extraccion = "2026-10-02T00:00:00Z"
    with (raw / "noticias.csv").open("a", encoding="utf-8") as f:
        # 30/09 a las 21:00 en Panamá ya es 01/10 en UTC, pero es septiembre: entra.
        f.write(f"b01,Noche,https://a.org/b01,A,es,2026-09-30T21:00:00-05:00,,{extraccion},,x,\n")
        # 01/10 a las 00:30 en Panamá ya es octubre: queda fuera.
        f.write(f"b02,Octubre,https://a.org/b02,A,es,2026-10-01T00:30:00-05:00,,{extraccion},,x,\n")
    s = Settings(_env_file=None)
    resultado = cargar_snapshot(raw, tmp_path / "processed", s.ventana_desde, s.ventana_hasta)
    validas = {n.id_noticia for n in resultado.noticias.validos}
    assert "b01" in validas
    assert "b02" not in validas
    # Las noticias sintéticas de septiembre de 2025 quedan fuera de la ventana de la demo.
    assert not validas & {"n001", "n002", "n007"}


def test_ventana_tambien_filtra_sismos_pero_no_indicadores(raw: Path, tmp_path: Path):
    resultado = cargar_snapshot(
        raw, tmp_path / "processed", desde=datetime(2024, 1, 15, tzinfo=UTC), hasta=None
    )
    assert [e.id for e in resultado.eventos.validos] == ["us7000dddd"]
    rechazo = next(r for r in resultado.eventos.rechazos if r.id == "us7000aaaa")
    assert rechazo.motivos == ["fuera de la ventana de fechas"]
    assert len(resultado.indicadores.validos) == 3
    assert resultado.reporte["ventana"]["aplica_a"] == ["noticias.csv", "eventos.geojson"]


def test_descripcion_del_rss_se_carga(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "noticias.csv").write_text(
        "id_noticia,titulo,url,medio,fecha_extraccion,origen,alcance_texto,descripcion\n"
        "d01,Titular,https://a.org/d01,TVN,2026-10-02,tvn_rss,titular_descripcion,"
        '"Resumen del RSS, con coma"\n',
        encoding="utf-8",
    )
    resultado = cargar_snapshot(raw, tmp_path / "processed")
    assert _consulta(resultado, "SELECT descripcion, alcance_texto FROM noticias") == [
        ("Resumen del RSS, con coma", "titular_descripcion")
    ]
