"""Contexto oficial (issue #12). T04 · Cifra anual del Banco Mundial: mantener país, año y
unidad; citar el dato y no describirlo como cifra de hoy. Usa el snapshot sintético T01."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from faro_editorial.carga import cargar_snapshot
from faro_editorial.contexto import ContextoOficial, buscar_palabra, load_reglas_contexto

HOY = datetime(2025, 9, 15, 14, 0, tzinfo=UTC)


@pytest.fixture
def carga(raw: Path, tmp_path: Path):
    return cargar_snapshot(raw, tmp_path / "processed")


@pytest.fixture
def contexto(carga) -> ContextoOficial:
    return ContextoOficial(carga.indicadores.validos, carga.eventos.validos)


def test_t04_cifra_anual_con_pais_anio_unidad_e_id(contexto):
    resultado = contexto.vincular("El PIB de Panamá creció en el último año", HOY, "n100")
    assert resultado.pendientes == []
    (vinculo,) = resultado.vinculos
    assert vinculo.tipo == "indicador"
    assert vinculo.id_evidencia == "BM:PAN:NY.GDP.MKTP.KD.ZG:2023"
    assert vinculo.campo == "valor"
    assert vinculo.cita.startswith("Crecimiento del PIB, Panamá, 2023: 7.3 (% anual).")
    assert "Dato anual de 2023, no una medición actual." in vinculo.cita
    assert "indicador NY.GDP.MKTP.KD.ZG" in vinculo.cita
    assert "extraído el 2025-10-01" in vinculo.cita
    assert "pib" in vinculo.regla


def test_t04_nunca_se_presenta_como_dato_de_hoy(contexto):
    (vinculo,) = contexto.vincular("PIB de Panamá", HOY).vinculos
    texto = vinculo.cita.lower()
    for prohibida in ("hoy", "actualmente", "este año", "en la actualidad"):
        assert prohibida not in texto


def test_t04_anio_sin_dato_se_declara_y_no_se_rellena(contexto):
    (vinculo,) = contexto.vincular("PIB de Panamá", HOY).vinculos
    # 2024 existe en el snapshot pero sin valor: no se usa ni se convierte en cero.
    assert "Sin dato publicado para 2024; el último disponible es 2023." in vinculo.limitaciones
    assert vinculo.serie == [(2023, 7.3), (2024, None)]


def test_cero_real_se_cita_como_cero(contexto):
    (vinculo,) = contexto.vincular("Sube el desempleo juvenil", HOY).vinculos
    assert "2024: 0 (% de la fuerza laboral)" in vinculo.cita


def test_indicador_sin_datos_queda_pendiente_sin_inventar(contexto):
    # La inflación de Panamá no está en el snapshot (la fila tenía el ISO mal y se rechazó).
    resultado = contexto.vincular("La inflación golpea el costo de los alimentos", HOY)
    assert resultado.vinculos == []
    (pendiente,) = resultado.pendientes
    assert "no tiene dato de Inflación" in pendiente


@pytest.mark.parametrize(
    "titular",
    [
        "Tránsito por el Canal se mantiene estable",
        "Lluvias, crecidas y alertas en Chiriquí",  # USGS no respalda inundaciones
        "Población afectada por las inundaciones en Darién",
        "Detienen a red de estafas por internet",
        "Habitantes de Colón protestan por el agua",
    ],
)
def test_sin_relacion_sustentada_no_se_vincula(contexto, titular):
    resultado = contexto.vincular(titular, HOY)
    assert resultado.vinculos == []
    assert resultado.pendientes == []


def test_usgs_vincula_sismo_cercano_con_limitaciones(contexto):
    fecha = datetime(2024, 1, 1, 5, 0, tzinfo=UTC)
    (vinculo,) = contexto.vincular("Fuerte sismo se sintió en Chiriquí", fecha).vinculos
    assert vinculo.tipo == "evento_sismico"
    assert vinculo.id_evidencia == "USGS:us7000aaaa"
    assert "magnitud 4.6" in vinculo.cita
    assert "2024-01-01 00:00 UTC (2023-12-31 19:00 hora de Panamá)" in vinculo.cita
    assert "45 km S of Punta de Burica, Panama" in vinculo.cita
    assert any("no equivale al territorio de Panamá" in x for x in vinculo.limitaciones)
    # Evento revisado: no se agrega la advertencia de estado automático.
    assert not any("revisado por un sismólogo" in x for x in vinculo.limitaciones)


def test_usgs_evento_automatico_lo_advierte(contexto):
    fecha = datetime(2024, 2, 1, 10, 0, tzinfo=UTC)
    (vinculo,) = contexto.vincular("TEMBLOR en la capital", fecha).vinculos
    assert vinculo.id_evidencia == "USGS:us7000dddd"
    assert "ubicación no indicada" in vinculo.cita
    assert "profundidad sin dato" in vinculo.cita
    assert any("no ha sido revisado por un sismólogo" in x for x in vinculo.limitaciones)


def test_usgs_sin_evento_en_la_ventana_queda_pendiente(contexto):
    resultado = contexto.vincular("Reportan sismo en Bocas del Toro", HOY)
    assert resultado.vinculos == []
    assert "USGS no registra sismos" in resultado.pendientes[0]


def test_usgs_noticia_sin_fecha_queda_pendiente(contexto):
    resultado = contexto.vincular("Reportan sismo en Bocas del Toro", None)
    assert resultado.vinculos == []
    assert "no tiene fecha" in resultado.pendientes[0]


def test_para_noticia_usa_deteccion_si_falta_publicacion(contexto, carga):
    noticia = next(n for n in carga.noticias.validos if n.id_noticia == "n002")
    resultado = contexto.para_noticia(noticia)
    assert resultado.id_noticia == "n002"
    assert resultado.version_reglas == "contexto-v1.0"


def test_desde_duckdb_equivale_a_los_modelos(carga):
    desde_db = ContextoOficial.desde_duckdb(carga.rutas["db"])
    (vinculo,) = desde_db.vincular("PIB de Panamá", HOY).vinculos
    assert vinculo.id_evidencia == "BM:PAN:NY.GDP.MKTP.KD.ZG:2023"
    fecha = datetime(2024, 1, 1, 5, 0, tzinfo=UTC)
    assert desde_db.vincular("sismo", fecha).vinculos[0].id_evidencia == "USGS:us7000aaaa"


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Crece el PIB", "pib"),
        ("Índice de Precios al Consumidor", "indice de precios"),
        ("el pibe del barrio", None),  # palabra completa, no subcadena
        ("TERREMOTO en la costa", "terremoto"),
    ],
)
def test_palabras_clave_sin_tildes_ni_mayusculas(texto, esperado):
    reglas = load_reglas_contexto()
    palabras = [p for r in reglas.indicadores.values() for p in r.palabras_clave]
    palabras += reglas.usgs.palabras_clave
    assert buscar_palabra(texto, palabras) == esperado
