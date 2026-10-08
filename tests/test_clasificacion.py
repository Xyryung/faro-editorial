"""Clasificación temática (issue #10) y evaluación con etiquetas humanas (#6, #7). Titulares
sintéticos; Jev se simula con ProveedorSimulado (sin red ni claves)."""

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from faro_editorial.bandeja import generar_bandeja
from faro_editorial.carga import cargar_snapshot
from faro_editorial.clasificacion import (
    ClasificadorTema,
    clasificar_grupos,
    clasificar_por_palabras,
    escribir_evaluacion,
    estado_jev,
    evaluar,
    leer_etiquetas,
    load_config,
    main,
    muestra_para_etiquetar,
)
from faro_editorial.decisiones import ClienteDecisiones, RespuestaChoice
from faro_editorial.proveedores import ProveedorSimulado

CONFIG = load_config()
BASE = datetime(2026, 9, 25, 12, tzinfo=UTC)

TITULARES = [
    (
        "n1",
        "TVN",
        "Canal de Panamá aumenta los tránsitos diarios y el calado máximo",
        "logistica_canal",
    ),
    ("n2", "La Prensa", "Fuertes lluvias provocan inundaciones en Chiriquí", "eventos_naturales"),
    ("n3", "Telemetro", "MEF reporta caída de las exportaciones y del empleo", "economia"),
    (
        "n4",
        "Crítica",
        "Asamblea aprueba en tercer debate la reforma a la ley de compras",
        "regulacion",
    ),
    ("n5", "TVN", "Idaan anuncia cortes de agua potable en San Miguelito", "servicios_publicos"),
    ("n6", "TVN", "Llegan más cruceros y turistas a Colón esta temporada", "turismo"),
    ("n7", "TVN", "Selección de fútbol gana amistoso en Japón", "otro"),
]


def jev_que_lee_el_titular(estado, preguntas):
    """Jev simulado: 'acierta' usando la etiqueta escondida en la tabla de arriba."""
    for _, _, titulo, tema in TITULARES:
        if titulo in estado:
            return {"tema": RespuestaChoice(opcion=tema, probabilidades={tema: 0.9}, confianza=0.8)}
    return {"tema": RespuestaChoice(opcion="otro", confianza=0.5)}


def cliente(tmp_path: Path, respuestas=jev_que_lee_el_titular, offline=False, error=None):
    proveedor = ProveedorSimulado(respuestas, error=error)
    return ClienteDecisiones(proveedor, tmp_path / "cache", offline=offline), proveedor


# --- Línea base -----------------------------------------------------------------------


@pytest.mark.parametrize(("_", "medio", "titulo", "esperado"), TITULARES)
def test_linea_base_por_palabras(_, medio, titulo, esperado):
    tema, palabras = clasificar_por_palabras([titulo], CONFIG)
    assert tema == esperado
    assert (palabras == {}) == (esperado == "otro")


def test_linea_base_sin_tildes_ni_mayusculas_y_canal_de_tv():
    assert clasificar_por_palabras(["SEQUÍA golpea el país"], CONFIG)[0] == "eventos_naturales"
    # "canal" suelto no es el Canal de Panamá: TVN es un canal de televisión.
    assert clasificar_por_palabras(["El canal estrena programa"], CONFIG)[0] == "otro"


def test_linea_base_gana_el_tema_con_mas_palabras():
    tema, palabras = clasificar_por_palabras(
        ["Lluvias e inundaciones obligan a cerrar el puerto"], CONFIG
    )
    assert tema == "eventos_naturales" and len(palabras["eventos_naturales"]) == 2
    assert palabras["logistica_canal"] == ["puerto"]


def test_config_coherente():
    assert CONFIG.temas == [
        "economia",
        "logistica_canal",
        "turismo",
        "servicios_publicos",
        "eventos_naturales",
        "regulacion",
        "otro",
    ]
    from faro_editorial.rules import load_rules
    from faro_editorial.settings import get_settings

    assert set(CONFIG.temas) == set(load_rules(get_settings().rules_path).temas)


def test_config_con_temas_distintos_falla(tmp_path: Path):
    datos = CONFIG.model_dump()
    datos["pregunta"]["en"]["opciones"].pop("turismo")
    ruta = tmp_path / "c.yaml"
    import yaml

    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="mismos temas"):
        load_config(ruta)


# --- Jev con respaldo -----------------------------------------------------------------


def test_jev_decide_y_usa_descripciones(tmp_path: Path):
    c, proveedor = cliente(tmp_path)
    r = ClasificadorTema(CONFIG, c).clasificar([TITULARES[0][2]])
    assert (r.tema, r.metodo, r.confianza) == ("logistica_canal", "jev", 0.8)
    (pregunta,) = ClasificadorTema(CONFIG, c).preguntas.values()
    assert "Canal de Panamá, puertos" in pregunta.opciones["logistica_canal"]


def test_jev_caido_usa_la_linea_base_y_lo_dice(tmp_path: Path):
    c, _ = cliente(tmp_path, error=TimeoutError("sin respuesta"))
    r = ClasificadorTema(CONFIG, c).clasificar([TITULARES[1][2]])
    assert (r.tema, r.metodo) == ("eventos_naturales", "palabras")
    assert "TimeoutError" in r.motivo_respaldo


def test_offline_sin_cache_usa_la_linea_base_y_con_cache_usa_jev(tmp_path: Path):
    titulo = [TITULARES[2][2]]
    offline, _ = cliente(tmp_path, offline=True)
    assert ClasificadorTema(CONFIG, offline).clasificar(titulo).metodo == "palabras"
    en_linea, _ = cliente(tmp_path)
    ClasificadorTema(CONFIG, en_linea).clasificar(titulo)  # llena la caché
    r = ClasificadorTema(CONFIG, offline).clasificar(titulo)
    assert (r.metodo, r.desde_cache, r.tema) == ("jev", True, "economia")


def test_estado_separa_los_titulares():
    assert estado_jev(["a", "b"]) == "Titulares:\n- a\n- b"


def test_en_paralelo_el_registro_queda_bien_formado(tmp_path: Path):
    c, proveedor = cliente(tmp_path)
    config = CONFIG.model_copy(update={"hilos": 8})
    lotes = [[f"Titular {i} sobre lluvias"] for i in range(40)]
    resultados = ClasificadorTema(config, c).clasificar_varios(lotes)
    assert len(resultados) == 40 and proveedor.llamadas == 40
    lineas = (tmp_path / "cache" / "registro_llamadas.jsonl").read_text(encoding="utf-8")
    assert [json.loads(x)["ok"] for x in lineas.splitlines()] == [True] * 40


# --- De la base a la bandeja ----------------------------------------------------------


@pytest.fixture
def processed(tmp_path: Path, escribir_manifest) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    columnas = [
        "id_noticia",
        "titulo",
        "url",
        "medio",
        "idioma",
        "fecha_publicacion",
        "fecha_deteccion",
        "fecha_extraccion",
        "tema",
        "origen",
        "alcance_texto",
    ]
    with (raw / "noticias.csv").open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=columnas)
        escritor.writeheader()
        for k, (i, medio, titulo, _) in enumerate(TITULARES):
            escritor.writerow(
                {
                    "id_noticia": i,
                    "titulo": titulo,
                    "url": f"https://m{k}.example/{i}",
                    "medio": medio,
                    "idioma": "es",
                    "fecha_publicacion": (BASE + timedelta(hours=k)).isoformat(),
                    "fecha_deteccion": "",
                    "fecha_extraccion": "2026-10-07T00:00:00Z",
                    "tema": "",
                    "origen": "prueba",
                    "alcance_texto": "titular_metadatos",
                }
            )
    escribir_manifest(raw)
    destino = tmp_path / "processed"
    cargar_snapshot(raw, destino)
    return destino


def test_clasificar_grupos_escribe_un_tema_por_grupo_y_la_bandeja_lo_usa(processed, tmp_path):
    (processed / "grupos.jsonl").write_text(
        '{"id_grupo": "g-1", "ids_noticias": ["n1", "n4"]}\n', encoding="utf-8"
    )
    c, proveedor = cliente(tmp_path)
    resumen = clasificar_grupos(processed, CONFIG, c)

    filas = [json.loads(x) for x in (processed / "grupos.jsonl").read_text("utf-8").splitlines()]
    # El grupo de la agrupación se conserva y las 5 noticias sueltas pasan a ser grupos con tema.
    assert [f["id_grupo"] for f in filas] == ["g-1", "n2", "n3", "n5", "n6", "n7"]
    assert sorted(i for f in filas for i in f["ids_noticias"]) == [t[0] for t in TITULARES]
    assert all(f["tema"] in CONFIG.temas and f["tema_metodo"] == "jev" for f in filas)
    assert filas[0]["tema"] == "logistica_canal"  # Jev ve los dos titulares; gana el primero
    assert resumen["por_metodo"] == {"jev": 6} and resumen["jev_en_vivo"] == 6

    bandeja = generar_bandeja(processed)
    temas = {t["id_grupo"]: t for t in bandeja["temas"]}
    assert temas["g-1"]["tema"] == "logistica_canal"
    assert temas["n7"]["tema"] == "otro"
    impacto = {g: t["componentes"]["I"]["valor"] for g, t in temas.items()}
    assert impacto["g-1"] > impacto["n7"]  # el tema cambia el impacto
    assert bandeja["advertencias"] == []  # ninguna noticia queda sin grupo


def test_clasificar_sin_ia(processed):
    resumen = clasificar_grupos(processed, CONFIG, None)
    assert resumen["por_metodo"] == {"palabras": 7}
    assert resumen["coincidencia_jev_vs_palabras"] is None


def test_segunda_corrida_sale_de_la_cache(processed, tmp_path):
    c, proveedor = cliente(tmp_path)
    clasificar_grupos(processed, CONFIG, c)
    resumen = clasificar_grupos(processed, CONFIG, c)
    assert proveedor.llamadas == 7 and resumen["jev_desde_cache"] == 7


# --- Etiquetado y evaluación ----------------------------------------------------------


def test_muestra_estratificada_sin_tema_propuesto(processed, tmp_path):
    ruta = muestra_para_etiquetar(processed, tmp_path / "eval" / "etiquetas.csv", n=5)
    with ruta.open(encoding="utf-8-sig") as f:
        filas = list(csv.DictReader(f))
    assert len(filas) == 5
    assert all(f["tema_humano"] == "" for f in filas)  # no se sugiere el tema
    temas = {clasificar_por_palabras([f["titulo"]], CONFIG)[0] for f in filas}
    assert len(temas) == 5  # una por tema en cada ronda, no cinco del mismo
    with pytest.raises(FileExistsError):
        muestra_para_etiquetar(processed, ruta, n=5)  # no pisa etiquetas humanas


def _etiquetas(ruta: Path, filas, separador=","):
    with ruta.open("w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.writer(f, delimiter=separador)
        escritor.writerow(
            ["id_noticia", "titulo", "medio", "tema_humano", "etiquetador", "comentario"]
        )
        escritor.writerows(filas)
    return ruta


def test_leer_etiquetas_tolera_excel_en_espanol(tmp_path: Path):
    ruta = _etiquetas(
        tmp_path / "e.csv",
        [
            ["n1", TITULARES[0][2], "TVN", "Logistica_Canal", "Ana", ""],
            ["n2", TITULARES[1][2], "La Prensa", "", "Ana", ""],
            ["n3", TITULARES[2][2], "Telemetro", "economía", "Ana", ""],
        ],
        separador=";",
    )
    filas, avisos = leer_etiquetas(ruta, CONFIG)
    assert [f["tema_humano"] for f in filas] == ["logistica_canal"]
    assert "sin tema_humano" in avisos[0] and "'economía' desconocido" in avisos[1]


def test_evaluar_compara_variantes_con_las_mismas_etiquetas(tmp_path: Path):
    ruta = _etiquetas(
        tmp_path / "e.csv", [[i, t, m, tema, "Ana", ""] for i, m, t, tema in TITULARES]
    )
    c, _ = cliente(tmp_path)
    caido, _ = cliente(tmp_path / "otro", error=TimeoutError("x"))
    resultado = evaluar(
        ruta,
        {
            "palabras": ClasificadorTema(CONFIG, None),
            "jev_es": ClasificadorTema(CONFIG, c),
            "jev_caido": ClasificadorTema(CONFIG, caido),
        },
        CONFIG,
    )

    v = resultado["variantes"]
    assert v["jev_es"]["macro_f1"] == 1.0 and v["jev_es"]["aciertos"] == 7
    assert v["palabras"]["macro_f1"] == 1.0  # los titulares sintéticos traen palabras clave
    # Para medir a Jev, una abstención es un error (no se rellena con la línea base).
    assert v["jev_caido"]["abstenciones"] == 7 and v["jev_caido"]["macro_f1"] == 0.0
    assert resultado["n_etiquetas"] == 7
    rutas = escribir_evaluacion(resultado, tmp_path / "salida")
    assert "| jev_es | 1.000 |" in rutas["md"].read_text(encoding="utf-8")


def test_macro_f1_con_un_error_conocido(tmp_path: Path):
    filas = [[i, t, m, tema, "Ana", ""] for i, m, t, tema in TITULARES]
    filas[6][3] = "turismo"  # el humano dice turismo; palabras dirá "otro"
    ruta = _etiquetas(tmp_path / "e.csv", filas)
    v = evaluar(ruta, {"palabras": ClasificadorTema(CONFIG, None)}, CONFIG)["variantes"]["palabras"]
    assert v["aciertos"] == 6 and v["temas_evaluados"] == [
        "economia",
        "logistica_canal",
        "turismo",
        "servicios_publicos",
        "eventos_naturales",
        "regulacion",
    ]
    # turismo: precisión 1, recall 1/2 -> F1 2/3; los otros 5 temas F1 1 -> macro (5 + 2/3) / 6.
    assert v["macro_f1"] == pytest.approx((5 + 2 / 3) / 6, abs=1e-4)
    assert v["errores"][0]["humano"] == "turismo" and v["errores"][0]["modelo"] == "otro"


def test_comandos(processed, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(processed.parent))
    from faro_editorial.settings import get_settings

    get_settings.cache_clear()
    try:
        main(["--metodo", "palabras"])
        assert "7 grupos clasificados" in capsys.readouterr().out
        main(["muestra", "--n", "4"])
        salida = capsys.readouterr().out
        assert "etiquetas_tema.csv" in salida and "logistica_canal" in salida
        ruta = processed.parent / "evaluacion" / "etiquetas_tema.csv"
        with ruta.open(encoding="utf-8-sig") as f:
            filas = list(csv.DictReader(f))
        tema_de = {t[2]: t[3] for t in TITULARES}
        _etiquetas(
            ruta,
            [
                [f["id_noticia"], f["titulo"], f["medio"], tema_de[f["titulo"]], "Ana", ""]
                for f in filas
            ],
        )
        main(["evaluar", "--variantes", "palabras"])
        assert "| palabras | 1.000 |" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()
