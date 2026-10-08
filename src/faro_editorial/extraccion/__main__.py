"""Permite ejecutar:  uv run python -m faro_editorial.extraccion  [opciones]"""

import sys

from faro_editorial.extraccion.snapshot import main

sys.exit(main())
