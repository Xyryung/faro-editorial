# Contrato de datos del snapshot (diccionario)

Formato que deben tener los archivos de `data/raw/` para que la carga
(`uv run python -m faro_editorial.carga`) los acepte. Lo valida `src/faro_editorial/contrato.py`;
una fila que no cumple se separa en `data/processed/rechazos.jsonl` con su motivo, sin detener
la carga.

## Reglas generales

- Archivos en **UTF-8**. CSV separados por coma, con encabezado; un valor con comas va entre
  comillas (`"Lluvias, crecidas y alertas"`).
- **Fechas** con zona horaria: `2026-03-15T14:30:00Z`, `2026-03-15T09:30:00-05:00`, el formato
  de GDELT `20260315T143000Z` o el del RSS `Sun, 15 Mar 2026 09:30:00 -0500`. Sin zona se asume
  UTC.
- **Vacío = sin dato.** Nunca rellenar con `0`, `-` ni `N/A` inventado. Se aceptan como nulo:
  vacío, `NA`, `N/A`, `NaN`, `null`, `None`.
- **Ventana de la demo:** noticias y sismos del **2025-10-01 al 2026-09-30, hora de Panamá**.
  Lo que caiga fuera se rechaza al cargar con el motivo "fuera de la ventana de fechas".

## `noticias.csv`

Una fila por noticia. **Deduplicar por URL** antes de escribir el archivo.

| Columna | ¿Obligatoria? | Formato y notas | Ejemplo |
|---|---|---|---|
| `id_noticia` | Sí | Único y estable entre descargas. Recomendado: origen + hash de la URL | `tvn-3f2a9c1b7e04` |
| `titulo` | Sí | Titular tal como lo publica el medio | `Tránsito por el Canal se mantiene estable` |
| `url` | Sí | `http://` o `https://`, sin modificar | `https://www.tvn-2.com/nacionales/...` |
| `medio` | Sí | Nombre del medio | `TVN` |
| `idioma` | No | Código ISO 639-1 | `es` |
| `fecha_publicacion` | No* | Cuándo publicó el medio. **En GDELT va vacía** | `2026-03-15T09:30:00-05:00` |
| `fecha_deteccion` | No* | `seendate` de GDELT (cuándo lo detectó GDELT). En TVN va vacía | `20260315T143000Z` |
| `fecha_extraccion` | Sí | Cuándo se descargó. No puede ser anterior a las otras dos fechas | `2026-10-02T12:00:00Z` |
| `tema` | No | Vacío: lo asigna la clasificación | |
| `origen` | Sí | `tvn_rss`, `gdelt` u otro. Un origen nuevo se documenta en `config/fuentes_catalogo.yaml` | `tvn_rss` |
| `alcance_texto` | No | `titular_metadatos` o `titular_descripcion`: qué texto hay disponible | `titular_descripcion` |
| `descripcion` | No | Descripción del RSS. Solo para análisis interno: no se copia en borradores ni se sube al repo | |

\* Cada noticia debería tener al menos una de las dos fechas; si no tiene ninguna, no se puede
ubicar en la ventana ni vincular con sismos.

## `indicadores.csv`

Banco Mundial, **años 2010–2024**, países `PAN, CRI, COL, DOM, MEX, GTM` e indicadores
`NY.GDP.MKTP.KD.ZG, FP.CPI.TOTL.ZG, SL.UEM.TOTL.ZS, SP.POP.TOTL, IT.NET.USER.ZS, NE.EXP.GNFS.ZS`.
Una fila por cada combinación país × indicador × año (6 × 6 × 15 = **540 filas**), **incluidas
las que no tienen valor** (con `valor` vacío). No usar la ventana de noticias aquí.

| Columna | ¿Obligatoria? | Formato y notas | Ejemplo |
|---|---|---|---|
| `pais_iso3` | Sí | ISO 3166 alfa-3 en mayúsculas | `PAN` |
| `indicador_id` | Sí | Código del Banco Mundial | `NY.GDP.MKTP.KD.ZG` |
| `anio` | Sí | Entero | `2023` |
| `valor` | No | Número con punto decimal. **Vacío si no hay dato, nunca 0** | `7.3` |
| `unidad` | No | Unidad del indicador | `% anual` |
| `fuente_url` | Sí | URL de la consulta a la API | `https://api.worldbank.org/v2/country/PAN/indicator/NY.GDP.MKTP.KD.ZG` |
| `fecha_extraccion` | Sí | Cuándo se descargó | `2026-10-02T12:00:00Z` |
| `licencia` | Sí | | `CC BY 4.0` |

Consulta sugerida (una por indicador):
`https://api.worldbank.org/v2/country/PAN;CRI;COL;DOM;MEX;GTM/indicator/NY.GDP.MKTP.KD.ZG?date=2010:2024&format=json&per_page=1000`

## `eventos.geojson`

La respuesta del servicio de USGS **sin modificar**. La carga toma `id`, `properties.mag`,
`time`, `updated`, `place`, `status`, `url` y `geometry.coordinates` [longitud, latitud,
profundidad].

Consulta (horas en UTC; medianoche de Panamá = 05:00 UTC):
`https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime=2025-10-01T05:00:00&endtime=2026-10-01T05:00:00&minlatitude=5&maxlatitude=12&minlongitude=-86&maxlongitude=-76&minmagnitude=3`

## `fuentes.json`

Lista de las fuentes usadas, para el catálogo. Formato libre; sugerido:

```json
[
  {"origen": "tvn_rss", "medio": "TVN", "url": "https://www.tvn-2.com/...", "consulta": "feed RSS"},
  {"origen": "gdelt", "url": "https://api.gdeltproject.org/api/v2/doc/doc", "consulta": "Panama economia ..."}
]
```

## `manifest.json`

Se genera **al final**, cuando los demás archivos ya no van a cambiar. Después no abrir ni
guardar los CSV en Excel: cambia los bytes y el hash deja de coincidir.

```json
{
  "version": "panama-senales-evidencias-v1",
  "fecha_corte_utc": "2026-10-01T05:00:00Z",
  "consultas": ["...una entrada por consulta hecha a cada API..."],
  "transformaciones": ["deduplicación por URL", "seendate de GDELT en fecha_deteccion"],
  "archivos": {
    "noticias.csv": {"sha256": "<64 caracteres hex>", "cantidad": 0, "licencia": "metadatos; ver catálogo"},
    "fuentes.json": {"sha256": "..."},
    "indicadores.csv": {"sha256": "...", "cantidad": 540, "licencia": "CC BY 4.0"},
    "eventos.geojson": {"sha256": "...", "licencia": "dominio público (USGS)"}
  }
}
```

Hash de un archivo: `Get-FileHash data\raw\noticias.csv -Algorithm SHA256` (PowerShell) o
`sha256sum data/raw/noticias.csv`.

## Cómo comprobar que el snapshot está bien

```powershell
uv run python -m faro_editorial.carga
```

Debe decir `Integridad del snapshot: OK`. Revisar `data/processed/reporte_calidad.json` y los
motivos en `rechazos.jsonl`: unos pocos rechazos son normales; muchos indican un problema de
formato.
