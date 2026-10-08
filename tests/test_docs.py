"""La página de riesgos y ética (#20) enlaza cada control a su código y su prueba: esos
enlaces no deben quedar rotos si alguien renombra una prueba o un archivo."""

import re

from faro_editorial.settings import ROOT_DIR

DOC = ROOT_DIR / "docs" / "riesgos_y_etica.md"


def test_las_pruebas_citadas_existen():
    texto = DOC.read_text(encoding="utf-8")
    citadas = set(re.findall(r"`(test_[a-z0-9_]+)`", texto))
    fuente = "".join(p.read_text(encoding="utf-8") for p in (ROOT_DIR / "tests").glob("test_*.py"))
    assert citadas, "la página debe citar pruebas"
    faltan = sorted(t for t in citadas if f"def {t}" not in fuente)
    assert not faltan, f"pruebas citadas que no existen: {faltan}"


def test_los_archivos_enlazados_existen():
    texto = DOC.read_text(encoding="utf-8")
    rutas = set(re.findall(r"github\.com/Xyryung/faro-editorial/blob/main/([^)\s#]+)", texto))
    assert rutas, "la página debe enlazar al código"
    faltan = sorted(r for r in rutas if not (ROOT_DIR / r).exists())
    assert not faltan, f"archivos enlazados que no existen: {faltan}"
