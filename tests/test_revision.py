"""Revisión humana (issue #17): estados del reto, historial y ficha para Notion."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from faro_editorial.carga import cargar_snapshot
from faro_editorial.interfaz import cargar_bandeja
from faro_editorial.revision import (
    NOMBRE_FICHAS,
    Revision,
    estados_vigentes,
    guardar_revision,
    historial,
    texto_para_notion,
)
from faro_editorial.rules import load_rules
from faro_editorial.settings import ROOT_DIR, get_settings

APP = str(ROOT_DIR / "app" / "main.py")
CAMPOS_CONTRATO = {
    "id_caso",
    "modalidad",
    "ids_fuente",
    "afirmaciones",
    "citas",
    "puntaje",
    "componentes",
    "estado_evidencia",
    "borrador",
    "estado_revision",
}


@pytest.fixture
def reglas():
    return load_rules(ROOT_DIR / "config" / "rules_v1.yaml")


@pytest.fixture
def data_dir(raw: Path, tmp_path: Path) -> Path:
    cargar_snapshot(raw, tmp_path / "processed")
    return tmp_path


@pytest.fixture(autouse=True)
def _limpiar_settings():
    yield
    get_settings.cache_clear()


def _tema(data_dir: Path) -> dict:
    return cargar_bandeja(data_dir / "processed")[0]["temas"][0]


def test_la_ficha_tiene_los_campos_del_contrato_y_no_habilita_publicar(data_dir: Path, reglas):
    tema = _tema(data_dir)
    revision = Revision(estado_revision="aprobado_como_borrador", revisor="  Ana  ")
    ficha = guardar_revision(data_dir / "processed", tema, revision, reglas)
    assert CAMPOS_CONTRATO <= set(ficha)
    assert ficha["id_caso"] == tema["id_grupo"]
    assert ficha["revisor"] == "Ana"  # sin espacios sobrantes
    assert ficha["habilita_publicacion"] is False  # aprobar no es publicar (sección 8)
    # Sin borrador todavía (#15): no se inventan afirmaciones ni citas.
    assert ficha["afirmaciones"] == [] and ficha["citas"] == [] and ficha["borrador"] is None
    assert set(ficha["ids_fuente"]) >= {n["id_noticia"] for n in tema["noticias"]}


def test_solo_se_aceptan_los_cinco_estados_del_reto(data_dir: Path, reglas):
    assert reglas.estados_revision == [
        "nuevo",
        "en_revision",
        "requiere_evidencia",
        "aprobado_como_borrador",
        "descartado",
    ]
    tema = _tema(data_dir)
    with pytest.raises(ValueError, match="no válido"):
        guardar_revision(
            data_dir / "processed",
            tema,
            Revision(estado_revision="publicado", revisor="Ana"),
            reglas,
        )
    assert not (data_dir / "processed" / NOMBRE_FICHAS).exists()  # no se guardó nada


def test_la_persona_revisora_es_obligatoria():
    with pytest.raises(ValidationError):
        Revision(estado_revision="en_revision", revisor="   ")


def test_el_historial_no_se_pierde_y_vale_la_ultima_decision(data_dir: Path, reglas):
    processed = data_dir / "processed"
    tema = _tema(data_dir)
    t0 = datetime(2026, 10, 8, 15, 0, tzinfo=UTC)
    guardar_revision(
        processed, tema, Revision(estado_revision="en_revision", revisor="Ana"), reglas, t0
    )
    guardar_revision(
        processed,
        tema,
        Revision(estado_revision="requiere_evidencia", revisor="Luis", comentario="Falta fuente"),
        reglas,
    )
    decisiones = historial(processed)[tema["id_grupo"]]
    assert [d["estado_revision"] for d in decisiones] == ["en_revision", "requiere_evidencia"]
    assert decisiones[0]["fecha_revision_utc"] == "2026-10-08T15:00:00Z"
    assert estados_vigentes(processed) == {tema["id_grupo"]: "requiere_evidencia"}


def test_una_linea_danada_no_tumba_el_historial(data_dir: Path, reglas):
    processed = data_dir / "processed"
    tema = _tema(data_dir)
    guardar_revision(processed, tema, Revision(estado_revision="descartado", revisor="Ana"), reglas)
    with (processed / NOMBRE_FICHAS).open("a", encoding="utf-8") as f:
        f.write("{esto no es json\n" + json.dumps({"sin": "id_caso"}) + "\n")
    assert estados_vigentes(processed) == {tema["id_grupo"]: "descartado"}


def test_texto_para_notion_escapa_el_texto_de_las_fuentes(data_dir: Path):
    tema = _tema(data_dir)
    tema = {**tema, "titulo": "Ignora todo [clic](https://evil.example) **x**"}
    texto = texto_para_notion(tema, None)
    assert "](https://evil.example" not in texto.split("\n")[0]
    assert "Estado: Nuevo (sin revisar)" in texto
    assert "Aprobar como borrador no significa publicar." in texto
    ultima = {
        "estado_revision": "aprobado_como_borrador",
        "revisor": "Ana [x](http://a)",
        "fecha_revision_utc": "2026-10-08T15:00:00Z",
        "comentario": "",
    }
    texto = texto_para_notion(tema, ultima)
    assert "Estado: Aprobado como borrador" in texto and "](http://a)" not in texto


# --- En la pantalla ---------------------------------------------------------------------


def test_guardar_una_revision_desde_la_ficha(monkeypatch, data_dir: Path):
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    app = AppTest.from_file(APP, default_timeout=60).run()
    assert not app.exception
    caso = app.selectbox(key="tema_elegido").value
    textos = " ".join(m.value for m in app.markdown)
    assert "Estado actual: **Nuevo**" in textos

    # Sin nombre de la persona revisora no se guarda.
    app.selectbox(key=f"revision_estado_{caso}").select("aprobado_como_borrador")
    app = app.button(key=f"revision_guardar_{caso}").click().run()
    assert any("persona revisora" in e.value for e in app.error)
    assert not (data_dir / "processed" / NOMBRE_FICHAS).exists()

    app.selectbox(key=f"revision_estado_{caso}").select("aprobado_como_borrador")
    app.text_input(key=f"revision_revisor_{caso}").input("Ana")
    app = app.button(key=f"revision_guardar_{caso}").click().run()
    assert not app.exception
    assert any("Revisión guardada" in s.value for s in app.success)
    assert estados_vigentes(data_dir / "processed") == {caso: "aprobado_como_borrador"}
    # La bandeja ya muestra el estado nuevo y la ficha se puede copiar para Notion.
    tabla = app.dataframe[0].value
    assert "Aprobado como borrador" in list(tabla["Revisión"])
    assert any("Persona revisora: Ana" in c.value for c in app.code)


def test_avance_de_la_revision_cuenta_los_no_revisados_como_nuevos():
    from faro_editorial.revision import avance_revision_html, conteo_revision

    temas = [{"id_grupo": "a"}, {"id_grupo": "b"}, {"id_grupo": "c"}]
    conteo = conteo_revision(temas, {"a": "aprobado_como_borrador", "b": "estado_raro"})
    assert conteo["nuevo"] == 2 and conteo["aprobado_como_borrador"] == 1
    html = avance_revision_html(conteo)
    assert "<b>1</b> de 3 temas revisados" in html
    assert html.count("<li>") == 5  # los cinco estados, aunque estén en cero
    assert "<b>0</b> de 0" in avance_revision_html(
        dict.fromkeys(conteo, 0)
    )  # sin división por cero


def test_la_fecha_de_la_revision_se_muestra_en_hora_de_panama(data_dir: Path, reglas):
    from faro_editorial.revision import fecha_revision_legible

    # Se guarda en UTC sin microsegundos y se muestra en hora de Panamá (UTC-5).
    ficha = guardar_revision(
        data_dir / "processed",
        _tema(data_dir),
        Revision(estado_revision="en_revision", revisor="Ana"),
        reglas,
        datetime(2026, 10, 9, 0, 44, 49, 80010, tzinfo=UTC),
    )
    assert ficha["fecha_revision_utc"] == "2026-10-09T00:44:49Z"
    assert fecha_revision_legible(ficha["fecha_revision_utc"]).startswith("8 oct 2026, ")
    assert "19:44" in fecha_revision_legible(ficha["fecha_revision_utc"])
    assert fecha_revision_legible("no es fecha") == "no es fecha"


def test_pestana_borrador_muestra_el_borrador_escapado(monkeypatch, data_dir: Path):
    """La pestaña Borrador (#15) muestra el borrador vigente del tema y escapa el texto del LLM
    (T07): un enlace o formato que repita un titular no se convierte en Markdown."""
    from test_borradores import CONFIG, FalsoLLM, _generador, _salida

    from faro_editorial.borradores import generar_ficha, guardar_borradores

    tema = _tema(data_dir)
    id_noticia = tema["noticias"][0]["id_noticia"]
    salida = _salida(
        afirmaciones=[
            {
                "id": "a1",
                "tipo": "declaracion",
                "texto": "El medio lo reporta en su titular.",
                "citas": [{"id_evidencia": id_noticia, "campo": "titulo"}],
            }
        ],
        brief="El medio lo reporta [a1]. [clic aquí](http://malo.example) **urgente**",
        guion="",
        copy_digital="",
    )
    borrador = generar_ficha(tema, _generador(data_dir, FalsoLLM(salida, salida)), None, CONFIG)
    guardar_borradores([borrador], data_dir / "processed")

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    app = AppTest.from_file(APP, default_timeout=60).run()
    assert not app.exception
    assert app.selectbox(key="borrador_elegido").value == tema["id_grupo"]
    textos = " ".join(m.value for m in app.markdown)
    assert "El medio lo reporta" in textos
    assert "](http://malo.example)" not in textos and "**urgente**" not in textos
    assert any("Basado únicamente en titular/metadatos" in m.value for m in app.markdown)
    # La ficha para Notion de la revisión ya no dice "pendiente": trae el borrador.
    assert any("El medio lo reporta" in c.value for c in app.code)
