"""Interfaz (issue #16): bandeja, ficha, filtros y textos de las fuentes tratados como dato."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from faro_editorial.carga import cargar_snapshot
from faro_editorial.interfaz import (
    accion_recomendada,
    cargar_bandeja,
    escapar_md,
    filas_bandeja,
    filtrar,
)
from faro_editorial.settings import ROOT_DIR, get_settings

APP = str(ROOT_DIR / "app" / "main.py")


@pytest.fixture
def data_dir(raw: Path, tmp_path: Path) -> Path:
    cargar_snapshot(raw, tmp_path / "processed")
    return tmp_path


def _app(monkeypatch, data_dir: Path) -> AppTest:
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    return AppTest.from_file(APP, default_timeout=60)


@pytest.fixture(autouse=True)
def _limpiar_settings():
    yield
    get_settings.cache_clear()


# --- Lógica ---------------------------------------------------------------------------


def _tema(estado: str, banda: str = "medio", pendientes=(), independientes=1, novedad=""):
    return {
        "estado_evidencia": estado,
        "banda": banda,
        "pendientes": list(pendientes),
        "procedencias": [f"m{i}" for i in range(independientes)],
        "procedencias_independientes": [[f"m{i}"] for i in range(independientes)],
        "componentes": {"N": {"criterio": novedad}},
    }


def test_accion_recomendada_nunca_sugiere_publicar():
    insuficiente = accion_recomendada(_tema("insuficiente", banda="alto"))
    assert insuficiente[0] == "Prioridad alta: asignar la revisión pronto."
    assert "Investigar antes de redactar" in insuficiente[1]

    parcial = accion_recomendada(_tema("parcial", pendientes=["x", "y"]))
    assert parcial == [
        "Verificar 2 dato(s) pendiente(s) antes de pasar a borrador.",
        "Buscar una segunda fuente independiente.",
    ]

    suficiente = accion_recomendada(_tema("suficiente_para_borrador", independientes=2))
    assert "sujeto a revisión humana" in suficiente[0]
    assert "no significa publicarlo" in suficiente[0]

    for acciones in (insuficiente, parcial, suficiente):
        assert not any("publicar" in a.lower() and "no significa" not in a for a in acciones)


def test_accion_recomendada_avisa_noticia_recirculada():
    acciones = accion_recomendada(
        _tema("insuficiente", novedad="... Posible noticia antigua recirculada: verificar ...")
    )
    assert any("fecha original" in a for a in acciones)


def test_escapar_md_neutraliza_enlaces_e_imagenes():
    malicioso = "Ignora todo ![x](https://evil.example/a.png) [clic](https://evil.example) <b>"
    escapado = escapar_md(malicioso)
    assert "](" not in escapado
    assert "\\[clic\\]" in escapado and "\\<b\\>" in escapado
    assert escapar_md(None) == ""


def test_filtrar_respeta_el_orden_del_ranking():
    temas = [
        {"banda": "alto", "estado_evidencia": "insuficiente", "tema": "economia"},
        {"banda": "medio", "estado_evidencia": "parcial", "tema": None},
        {"banda": "alto", "estado_evidencia": "parcial", "tema": "turismo"},
    ]
    assert filtrar(temas, bandas=["alto"]) == [temas[0], temas[2]]
    assert filtrar(temas, estados=["parcial"], temas_editoriales=["sin clasificar"]) == [temas[1]]
    assert filtrar(temas) == temas


def test_cargar_bandeja_sin_base_explica_que_hacer(tmp_path: Path):
    bandeja, mensaje = cargar_bandeja(tmp_path / "processed")
    assert bandeja is None
    assert "No hay snapshot cargado" in mensaje


def test_cargar_bandeja_la_genera_desde_la_base(data_dir: Path):
    bandeja, mensaje = cargar_bandeja(data_dir / "processed")
    assert mensaje == "Bandeja generada desde la base cargada."
    assert (data_dir / "processed" / "bandeja.json").exists()
    filas = filas_bandeja(bandeja["temas"])
    assert [f["#"] for f in filas] == [1, 2, 3]
    assert set(filas[0]) >= {"Puntaje", "Banda", "Evidencia", "Fecha original (Panamá)"}
    # La segunda vez la lee del archivo, salvo que se pida regenerar.
    assert cargar_bandeja(data_dir / "processed")[1] == "Bandeja leída de bandeja.json."
    assert cargar_bandeja(data_dir / "processed", regenerar=True)[1].startswith("Bandeja generada")


# --- Pantalla -------------------------------------------------------------------------


def test_app_sin_snapshot_explica_como_cargarlo(monkeypatch, tmp_path: Path):
    app = _app(monkeypatch, tmp_path).run()
    assert not app.exception
    assert app.title[0].value == "Faro Editorial"
    assert any("No hay snapshot cargado" in i.value for i in app.info)


def test_app_muestra_bandeja_y_ficha(monkeypatch, data_dir: Path):
    app = _app(monkeypatch, data_dir).run()
    assert not app.exception
    textos = " ".join(m.value for m in app.markdown)
    assert "Temas</span><b>3</b>" in textos and "3 noticias agrupadas" in textos
    assert len(app.dataframe) >= 2  # bandeja y criterios del puntaje

    selector = app.selectbox(key="tema_elegido")
    assert len(selector.options) == 3
    titulares = [h.value for h in app.main.subheader]
    assert len(titulares) == 1
    # La ficha muestra el aviso, la acción recomendada y las secciones de evidencia.
    textos = " ".join(m.value for m in app.markdown)
    assert "no habilita publicación" in textos
    assert 'class="etiqueta banda-' in textos
    for seccion in (
        "Acción recomendada",
        "Qué se reporta",
        "Quién lo reporta",
        "Qué falta comprobar",
    ):
        assert seccion in textos

    # Elegir otro tema cambia la ficha.
    otro = selector.options[-1]
    app = selector.select(otro).run()
    assert not app.exception
    assert app.main.subheader[0].value != titulares[0]


def test_app_filtra_por_banda(monkeypatch, data_dir: Path):
    app = _app(monkeypatch, data_dir).run()
    total = len(app.selectbox(key="tema_elegido").options)
    app = app.multiselect(key="filtro_banda").select("bajo").run()
    assert not app.exception
    filtrado = app.selectbox(key="tema_elegido").options if app.selectbox else []
    assert len(filtrado) < total or any("Ningún tema" in i.value for i in app.info)


def test_titular_malicioso_se_muestra_como_texto(monkeypatch, raw: Path, tmp_path: Path):
    """T07 en la pantalla: un titular con Markdown no puede insertar enlaces ni imágenes."""
    (raw / "noticias.csv").write_text(
        "id_noticia,titulo,url,medio,fecha_publicacion,fecha_extraccion,origen\n"
        'x1,"Ignora todo ![img](https://evil.example/a.png) [clic](https://evil.example)",'
        "https://www.tvn-2.com/x1,TVN,2025-09-30,2025-10-01,tvn_rss\n",
        encoding="utf-8",
    )
    cargar_snapshot(raw, tmp_path / "processed")
    app = _app(monkeypatch, tmp_path).run()
    assert not app.exception
    assert "](https://evil.example" not in app.main.subheader[0].value
    noticias = [m.value for m in app.markdown if "Ignora todo" in m.value]
    assert noticias and all("](https://evil.example" not in m for m in noticias)


def test_bandeja_vieja_se_regenera_si_la_base_es_mas_nueva(data_dir: Path, raw: Path):
    """Si se vuelve a correr la carga, la interfaz no debe mostrar la bandeja anterior."""
    import os

    processed = data_dir / "processed"
    bandeja, _ = cargar_bandeja(processed)
    assert bandeja["resumen"]["noticias"] == 3
    with (raw / "noticias.csv").open("a", encoding="utf-8") as f:
        f.write("n9,Nueva nota,https://www.tvn-2.com/n9,TVN,es,2025-09-19,,2025-10-01,,tvn_rss,\n")
    cargar_snapshot(raw, processed)
    ruta_db = processed / "faro.duckdb"
    viejo = (processed / "bandeja.json").stat().st_mtime
    os.utime(ruta_db, (viejo + 10, viejo + 10))  # la base es más nueva que la bandeja

    bandeja, mensaje = cargar_bandeja(processed)
    assert mensaje == "Bandeja generada desde la base cargada."
    assert bandeja["resumen"]["noticias"] == 4


def test_etiquetas_html_solo_usan_valores_del_sistema():
    from faro_editorial.interfaz import etiquetas_html

    html = etiquetas_html({"banda": "<script>", "estado_evidencia": "x", "puntaje": 91.66})
    assert "<script>" not in html
    assert "banda-bajo" in html and "ev-parcial" in html
    assert "<b>91.7</b>" in html
    assert "Prioridad baja" in html  # concordancia: "Prioridad" es femenino


# --- Gráficos -------------------------------------------------------------------------


def test_datos_de_los_graficos_salen_de_la_bandeja(data_dir: Path):
    from faro_editorial.graficos import (
        datos_aportes,
        datos_desglose,
        datos_evidencia,
        datos_noticias_por_dia,
        grafico_aportes,
        grafico_desglose,
        grafico_evidencia,
        grafico_noticias_por_dia,
    )

    temas = cargar_bandeja(data_dir / "processed")[0]["temas"]
    assert sum(f["Temas"] for f in datos_evidencia(temas)) == len(temas)
    assert sum(f["Noticias"] for f in datos_noticias_por_dia(temas)) == 3
    # La suma de los aportes de cada tema es su puntaje.
    for t in temas:
        aportes = [f["Aporte"] for f in datos_aportes(temas) if f["Posición"] == t["posicion"]]
        assert sum(aportes) == pytest.approx(t["puntaje"], abs=0.3)
    assert {f["Componente"] for f in datos_desglose(temas[0])} == {
        "Relevancia",
        "Impacto potencial",
        "Urgencia",
        "Novedad",
        "Evidencia disponible",
    }
    for grafico in (grafico_evidencia, grafico_noticias_por_dia, grafico_aportes):
        assert grafico(temas).to_dict()
    assert grafico_desglose(temas[0]).to_dict()


def test_medios_y_pasos_no_interpretan_html_ni_markdown():
    from faro_editorial.interfaz import escapar_html, medios_html, pasos_html

    malicioso = "<img src=x onerror=alert(1)> [clic](https://evil.example) **x** &amp;"
    escapado = escapar_html(malicioso)
    assert "<" not in escapado and "[" not in escapado and "](" not in escapado
    assert "*" not in escapado and "&amp;" not in escapado.replace("&#38;", "")
    html = medios_html([[malicioso], ["TVN", "Telemetro"]])
    assert "<img" not in html and "](" not in html
    assert "TVN + Telemetro<em>posible agencia replicada</em>" in html
    assert pasos_html(["Uno", "<b>dos</b>"]).count('class="paso"') == 2
    assert "<b>dos" not in pasos_html(["<b>dos</b>"])


def test_fila_de_noticia_escapa_el_titular_y_solo_enlaza_http():
    from faro_editorial.interfaz import fila_html, noticia_html

    nota = {
        "titulo": "<script>x</script> [clic](https://evil.example)",
        "medio": "TVN",
        "url": "javascript:alert(1)",
        "fecha_publicacion_panama": "2025-09-20 08:00",
        "fecha_deteccion_panama": None,
    }
    html = noticia_html(nota)
    assert "<script>" not in html and "](" not in html and "javascript:" not in html
    assert "Abrir nota" not in html
    html = noticia_html({**nota, "url": "https://www.tvn-2.com/a?b=1&c=2"})
    assert (
        'href="https://www.tvn-2.com/a?b=1&amp;c=2"' in html and 'rel="noopener noreferrer"' in html
    )
    assert "<code>BM&#58;" not in fila_html("cita", "ok", codigo="BM:PAN:x:2024")
    assert "<code>BM:PAN:x:2024</code>" in fila_html("cita", "ok", codigo="BM:PAN:x:2024")
