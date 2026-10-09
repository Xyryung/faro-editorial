"""Puntaje de atención (issue #11). T08 · Caso de prioridad alta: exponer componentes y regla;
la prioridad no habilita publicación."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from faro_editorial.carga import cargar_snapshot
from faro_editorial.contexto import ContextoOficial
from faro_editorial.contrato import Noticia
from faro_editorial.puntaje import Componente, GrupoNoticias, MotorPuntaje, Puntuacion

REFERENCIA = datetime(2025, 9, 20, 12, 0, tzinfo=UTC)


def noticia(
    id_: str,
    titulo: str,
    medio: str = "TVN",
    url: str | None = None,
    horas_antes: float | None = 1,
    tema: str | None = None,
) -> Noticia:
    fecha = REFERENCIA - timedelta(hours=horas_antes) if horas_antes is not None else None
    return Noticia(
        id_noticia=id_,
        titulo=titulo,
        url=url or f"https://www.tvn-2.com/nacionales/{id_}",
        medio=medio,
        fecha_publicacion=fecha,
        fecha_extraccion=REFERENCIA,
        tema=tema,
        origen="tvn_rss" if medio == "TVN" else "gdelt",
    )


@pytest.fixture
def contexto_oficial(raw: Path, tmp_path: Path) -> ContextoOficial:
    carga = cargar_snapshot(raw, tmp_path / "processed")
    return ContextoOficial(carga.indicadores.validos, carga.eventos.validos)


@pytest.fixture
def motor(contexto_oficial) -> MotorPuntaje:
    return MotorPuntaje(contexto_oficial=contexto_oficial)


def test_t08_prioridad_alta_expone_componentes_y_regla(motor):
    grupo = GrupoNoticias(
        id_grupo="g-pib",
        noticias=[
            noticia("n1", "El PIB de Panamá creció más de lo esperado", tema="economia"),
            noticia(
                "n2",
                "Panama GDP growth beats forecasts",
                medio="example.org",
                url="https://example.org/pib",
                horas_antes=3,
                tema="economia",
            ),
        ],
    )
    p = motor.puntuar(grupo, REFERENCIA)

    assert p.banda == "alto" and p.puntaje >= 70
    assert list(p.componentes) == ["R", "I", "U", "N", "E"]
    assert {k: c.peso for k, c in p.componentes.items()} == {
        "R": 30,
        "I": 25,
        "U": 20,
        "N": 15,
        "E": 10,
    }
    # El puntaje es exactamente la suma de los aportes visibles.
    assert p.puntaje == round(sum(c.aporte for c in p.componentes.values()), 1)
    assert all(c.criterio for c in p.componentes.values())
    assert "BM:PAN:NY.GDP.MKTP.KD.ZG:2023" in p.componentes["E"].criterio
    assert p.version_reglas == "reglas-v1.0"
    assert p.version_criterios == "criterios-v2.0"
    # La prioridad no habilita publicación, y el modelo no permite marcarlo.
    assert p.habilita_publicacion is False
    assert "no habilita publicación" in p.aviso
    with pytest.raises(ValidationError):
        Puntuacion(**{**p.model_dump(), "habilita_publicacion": True})


def test_prioridad_alta_con_evidencia_insuficiente(motor):
    """Relevancia y suficiencia de evidencia son cosas distintas (sección 4 del reto)."""
    grupo = GrupoNoticias.de_noticia(
        noticia("n1", "Cierre parcial de esclusas en el Canal de Panamá", tema="logistica_canal")
    )
    p = motor.puntuar(grupo, REFERENCIA)
    assert p.banda == "alto"
    assert p.estado_evidencia == "insuficiente"
    assert "requiere investigar" in p.motivo_estado


def test_duplicar_noticias_no_infla_el_puntaje(motor):
    """T02: tres registros del mismo medio no triplican importancia ni corroboración."""
    una = GrupoNoticias.de_noticia(noticia("a1", "Alza del pasaje en Panamá", tema="economia"))
    tres = GrupoNoticias(
        id_grupo="a",
        noticias=[
            noticia("a1", "Alza del pasaje en Panamá", tema="economia"),
            noticia("a2", "Sube el pasaje en Panamá", tema="economia", horas_antes=2),
            noticia("a3", "Panamá: pasaje más caro", tema="economia", horas_antes=3),
        ],
    )
    p1, p3 = motor.puntuar(una, REFERENCIA), motor.puntuar(tres, REFERENCIA)
    assert p3.procedencias == ["tvn"]
    assert p3.componentes["E"].valor == p1.componentes["E"].valor
    assert p3.componentes["N"].valor == p1.componentes["N"].valor
    assert p3.puntaje == p1.puntaje


def test_noticia_recirculada_no_es_novedad(motor):
    """T03: si el evento circula desde hace semanas, no se presenta como nuevo."""
    grupo = GrupoNoticias(
        id_grupo="viejo",
        noticias=[
            noticia("v1", "Panamá: nuevo récord de tránsito", horas_antes=24 * 40),
            noticia("v2", "Vuelve a circular: récord de tránsito en Panamá", horas_antes=1),
        ],
    )
    n = motor.puntuar(grupo, REFERENCIA).componentes["N"]
    assert n.valor == 0.0
    assert "recirculada" in n.criterio


@pytest.mark.parametrize(("horas", "esperado"), [(1, 1.0), (24, 1.0), (96, 0.5), (200, 0.0)])
def test_urgencia_decrece_con_la_antiguedad(motor, horas, esperado):
    grupo = GrupoNoticias.de_noticia(noticia("u", "Panamá", horas_antes=horas))
    assert motor.puntuar(grupo, REFERENCIA).componentes["U"].valor == esperado


def test_sin_fecha_urgencia_y_novedad_en_cero(motor):
    grupo = GrupoNoticias.de_noticia(noticia("s", "Panamá", horas_antes=None))
    p = motor.puntuar(grupo, REFERENCIA)
    assert p.componentes["U"].valor == 0 and p.componentes["N"].valor == 0
    assert "Sin fecha" in p.componentes["U"].criterio


def test_relevancia_explica_panama_y_tema(motor):
    sin_relacion = GrupoNoticias.de_noticia(
        noticia("x", "Elecciones en otro país", url="https://example.org/x", tema="otro")
    )
    r = motor.puntuar(sin_relacion, REFERENCIA).componentes["R"]
    assert r.valor == 0.0
    assert "sin relación explícita con Panamá" in r.criterio
    # El dominio del medio también cuenta como relación con Panamá.
    por_dominio = GrupoNoticias.de_noticia(noticia("y", "Sube el desempleo", tema="economia"))
    r = motor.puntuar(por_dominio, REFERENCIA).componentes["R"]
    assert r.valor == 1.0
    assert "tvn-2.com" in r.criterio


def test_pendientes_dejan_la_evidencia_parcial(motor):
    grupo = GrupoNoticias(
        id_grupo="sismo",
        noticias=[
            noticia("s1", "Fuerte sismo en Chiriquí, Panamá", tema="eventos_naturales"),
            noticia(
                "s2",
                "Sismo sacude Panamá",
                medio="example.org",
                url="https://example.org/s",
                tema="eventos_naturales",
            ),
        ],
    )
    p = motor.puntuar(grupo, REFERENCIA)
    # USGS no tiene sismos en la fecha: queda pendiente y la evidencia no alcanza.
    assert p.estado_evidencia == "parcial"
    assert p.pendientes and "USGS no registra sismos" in p.pendientes[0]


class EvaluadorFalso:
    """Simula Jev Score: entrega R, I y U por grupo (y un E que debe ignorarse)."""

    def __init__(self, valores: dict[str, dict[str, float]]) -> None:
        self.valores = valores

    def evaluar(self, grupo, referencia):
        return {
            k: Componente(valor=v, criterio="rúbrica simulada", fuente="jev")
            for k, v in self.valores[grupo.id_grupo].items()
        }


def test_evaluador_externo_reemplaza_r_i_u_y_desempate(contexto_oficial):
    evaluador = EvaluadorFalso(
        {
            # Mismo puntaje total: B gana 10 puntos en I y A gana 10 en U.
            "B": {"R": 0.5, "I": 0.8, "U": 0.5, "E": 1.0},
            "A": {"R": 0.5, "I": 0.4, "U": 1.0},
            "C": {"R": 0.5, "I": 0.4, "U": 1.0},
        }
    )
    motor = MotorPuntaje(contexto_oficial=contexto_oficial, evaluador=evaluador)
    grupos = [GrupoNoticias.de_noticia(noticia(i, "Nota local")) for i in ("C", "B", "A")]
    for g, i in zip(grupos, ("C", "B", "A"), strict=True):
        g.id_grupo = i
    ranking = motor.ranking(grupos, REFERENCIA)

    assert len({p.puntaje for p in ranking}) == 1
    # Empate: mayor urgencia primero (A y C) y luego por ID.
    assert [p.id_grupo for p in ranking] == ["A", "C", "B"]
    b = next(p for p in ranking if p.id_grupo == "B")
    assert b.componentes["I"].fuente == "jev"
    # E siempre sale de las reglas deterministas, no del evaluador externo.
    assert b.componentes["E"].fuente == "reglas"


def test_una_sola_nota_vieja_detectada_hoy_no_es_novedad(motor):
    """T03 con una sola noticia: GDELT detecta hoy un artículo publicado hace 40 días."""
    vieja = noticia("g1", "Récord de tránsito en Panamá", horas_antes=24 * 40)
    vieja = vieja.model_copy(update={"fecha_deteccion": REFERENCIA - timedelta(hours=1)})
    n = motor.puntuar(GrupoNoticias.de_noticia(vieja), REFERENCIA).componentes["N"]
    assert n.valor == 0.0
    assert "recirculada" in n.criterio


@pytest.mark.parametrize(
    ("url", "panameno"),
    [
        ("https://www.tvn-2.com/x", True),
        ("https://tvn-2.com:443/x", True),
        ("https://www.prensa.com.pa/x", True),
        ("https://faketvn-2.com/x", False),
        ("https://tvn-2.com.evil.org/x", False),
    ],
)
def test_dominio_panameno_sin_falsos_positivos(motor, url, panameno):
    grupo = GrupoNoticias.de_noticia(noticia("d", "Sube el desempleo", url=url, tema="economia"))
    criterio = motor.puntuar(grupo, REFERENCIA).componentes["R"].criterio
    assert ("medio panameño" in criterio) is panameno


def test_sismo_se_busca_desde_la_primera_noticia_del_grupo(motor):
    # Sismo USGS us7000aaaa: 2024-01-01 00:00 UTC. El grupo lo sigue durante 5 días.
    inicio = datetime(2024, 1, 1, 3, 0, tzinfo=UTC)

    def nota(id_: str, dias: int) -> Noticia:
        return Noticia(
            id_noticia=id_,
            titulo="Sismo en Chiriquí, Panamá: balance",
            url=f"https://www.tvn-2.com/{id_}",
            medio="TVN",
            fecha_publicacion=inicio + timedelta(days=dias),
            fecha_extraccion=inicio + timedelta(days=10),
            origen="tvn_rss",
        )

    grupo = GrupoNoticias(id_grupo="sismo", noticias=[nota("s1", 0), nota("s2", 5)])
    p = motor.puntuar(grupo, inicio + timedelta(days=5))
    assert "USGS:us7000aaaa" in p.componentes["E"].criterio
    assert p.pendientes == []


def _replica(i: int, titulo: str, medio: str) -> Noticia:
    return noticia(
        f"r{i}", titulo, medio=medio, url=f"https://{medio}.example/r{i}", tema="economia"
    )


CINCO_MEDIOS_MISMA_NOTA = [
    _replica(1, "Gobierno de Panamá anuncia alza del combustible (EFE)", "medio-a"),
    _replica(2, "Gobierno de Panamá anuncia alza del combustible", "medio-b"),
    _replica(3, "GOBIERNO DE PANAMA ANUNCIA ALZA DEL COMBUSTIBLE.", "medio-c"),
    _replica(4, "Gobierno de Panamá anuncia alza del combustible - EFE", "medio-d"),
    _replica(5, "Gobierno de Panamá anuncia alza del combustible", "medio-e"),
]


def test_cinco_medios_que_replican_la_misma_agencia_son_una_procedencia(motor):
    """Pregunta del jurado (sección 11) y CU-03: cinco medios que replican la misma nota de
    agencia cuentan como una sola procedencia independiente."""
    replicada = motor.puntuar(
        GrupoNoticias(id_grupo="r", noticias=CINCO_MEDIOS_MISMA_NOTA), REFERENCIA
    )
    una_sola = motor.puntuar(GrupoNoticias.de_noticia(CINCO_MEDIOS_MISMA_NOTA[0]), REFERENCIA)

    assert len(replicada.procedencias) == 5
    assert replicada.procedencias_independientes == [
        ["medio-a", "medio-b", "medio-c", "medio-d", "medio-e"]
    ]
    # La repetición no suma corroboración: misma evidencia y mismo estado que una sola nota.
    assert replicada.componentes["E"].valor == una_sola.componentes["E"].valor
    assert replicada.estado_evidencia == "insuficiente"
    assert "5 medios replican la misma nota" in replicada.motivo_estado
    assert "posible agencia replicada" in replicada.componentes["E"].criterio


def test_medios_con_titulares_propios_son_independientes(motor):
    grupo = GrupoNoticias(
        id_grupo="i",
        noticias=[
            *CINCO_MEDIOS_MISMA_NOTA[:3],
            _replica(6, "Transportistas anuncian paro por el precio del diésel en Panamá", "tvn"),
        ],
    )
    p = motor.puntuar(grupo, REFERENCIA)
    assert p.procedencias_independientes == [["medio-a", "medio-b", "medio-c"], ["tvn"]]
    assert p.estado_evidencia == "suficiente_para_borrador"
    assert "4 medios, 2 procedencias independientes" in p.componentes["E"].criterio


def test_criterios_v1_siguen_contando_medios(contexto_oficial):
    """La versión anterior queda reproducible: v1 contaba cada medio como una procedencia."""
    from faro_editorial.puntaje import load_criterios
    from faro_editorial.settings import ROOT_DIR

    v1 = load_criterios(ROOT_DIR / "config" / "criterios_v1.yaml")
    motor_v1 = MotorPuntaje(criterios=v1, contexto_oficial=contexto_oficial)
    p = motor_v1.puntuar(GrupoNoticias(id_grupo="r", noticias=CINCO_MEDIOS_MISMA_NOTA), REFERENCIA)
    assert p.version_criterios == "criterios-v1.0"
    assert len(p.procedencias_independientes) == 5
