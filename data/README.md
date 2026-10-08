# Datos

| Carpeta | Contenido | ¿Se sube al repo? |
|---|---|---|
| `raw/` | Snapshot congelado que construye el equipo ("Panamá · Señales y Evidencias v1"): `noticias.csv`, `fuentes.json`, `indicadores.csv`, `eventos.geojson`, `manifest.json`. Formato en [`CONTRATO.md`](CONTRATO.md). Nunca se modifica. | No, hasta confirmar condiciones de redistribución |
| `processed/` | Datos validados y normalizados, base DuckDB, reporte de calidad y archivo de rechazos. Se regenera con el pipeline. | No (regenerable) |
| `cache/` | Respuestas guardadas de Jev, del LLM y embeddings, indexadas por hash de (modelo + versión de prompt + entrada). Permite la demo sin internet (T10). | Por decidir con la organización |
| `entrenamiento/` | Snapshot del perfil de entrenamiento del extractor (`raw/` y `processed/`), con fechas libres. | No |

## Por qué no hay datos en el repo todavía

El repositorio es público. El reto indica que el patrocinio no concede derechos de
republicación y que, si una fuente restringe la redistribución, se entregan metadatos
y receta, no el contenido protegido. Hasta que la organización confirme qué se puede
publicar, todo el contenido de `data/` queda fuera de Git (ver `.gitignore`).
Cuando se confirme, se registra la decisión en un issue con la etiqueta `decision`.

## Cómo generar el snapshot (extractor)

La organización confirmó que las fuentes del reto son ejemplos y que se espera una extracción
general. El extractor escribe los cinco archivos de [`CONTRATO.md`](CONTRATO.md) y al final los
valida con la carga, usando la misma ventana:

```powershell
# Demo: ventana VENTANA_DESDE/VENTANA_HASTA de .env -> data/raw/
uv run python -m faro_editorial.extraccion

# Entrenamiento: fechas libres (por defecto, últimos 90 días) -> data/entrenamiento/raw/
uv run python -m faro_editorial.extraccion --perfil entrenamiento --desde 2026-07-01

# Refrescar solo las noticias y conservar indicadores y sismos
uv run python -m faro_editorial.extraccion --solo noticias --sobrescribir

# Buscar feeds y sitemaps de un medio nuevo para agregarlo a config/extraccion_v1.yaml
uv run python -m faro_editorial.extraccion.descubrir https://www.ejemplo.com.pa
```

- Fuentes y parámetros: `config/extraccion_v1.yaml` (feeds RSS, sitemaps de noticias, GDELT
  DOC 2.0, Banco Mundial y USGS). Cada origen está documentado en `config/fuentes_catalogo.yaml`.
- Titular, URL, fechas, idioma y la descripción del RSS (solo análisis interno, decisión #35).
  Nunca se descarga el artículo ni sus imágenes. Se respeta robots.txt (incluido Crawl-delay).
- Cada respuesta se guarda cruda en `<salida>/_respuestas/` y queda registrada en
  `manifest.json` con su URL, hora, tamaño y SHA-256.
- GDELT DOC 2.0 solo busca en los últimos 3 meses: la parte anterior de la ventana se cubre con
  los feeds y sitemaps, y el manifest lo informa como aviso.
- TVN dentro de la ventana: el RSS y el sitemap de noticias solo traen los últimos días. Las notas
  de TVN de los últimos 3 meses de la ventana salen de sus sitemaps mensuales, sin secciones de
  deportes ni espectáculos; su fecha es el `lastmod` del sitemap (igual o posterior a la
  publicación) y su título, el de la imagen (`alcance_texto = titulo_imagen_sitemap`).
- La consola muestra el avance (fuente, consulta y tramo de GDELT). La ejecución completa de la
  demo tarda del orden de media hora, casi todo por la pausa obligatoria entre llamadas a GDELT.
- Un snapshot existente nunca se pisa sin `--sobrescribir`; con esa opción los archivos
  anteriores se mueven a `<salida>/_anteriores/`.

## Cómo preparar los datos localmente

1. Copia los archivos del snapshot en `data/raw/` (formato en [`CONTRATO.md`](CONTRATO.md)),
   o genéralos con el extractor.
2. Ejecuta la carga, que también verifica el SHA-256 de cada archivo contra `manifest.json`:

   ```powershell
   uv run python -m faro_editorial.carga
   ```

3. Revisa las salidas en `processed/`:
   - `faro.duckdb`: tablas `noticias`, `indicadores` y `eventos` (fechas en UTC, nulos como NULL).
   - `rechazos.jsonl`: cada fila inválida con su archivo, número de fila, ID, motivos y datos crudos.
   - `reporte_calidad.json`: integridad, conteos por archivo, motivos de rechazo y nulos por campo.
   - `catalogo_datos.csv` y `.md`: una fila por fuente para la página "Catálogo de datos" de
     Notion (en Notion: Importar → CSV). Los datos fijos (URL, licencia) están en
     `config/fuentes_catalogo.yaml`; lo demás se calcula en la carga.

Una fila inválida nunca detiene la carga (prueba T01): se separa y el resto se carga.

### Ventana de fechas

| Fuente | Período | Origen de la decisión |
|---|---|---|
| Noticias | 2025-10-01 a 2026-09-30, hora de Panamá | Organización (fechas); equipo (hora de Panamá) |
| Sismos USGS | Mismo período que las noticias | Equipo: deben coincidir con las noticias para respaldarlas |
| Banco Mundial | Años 2010–2024 | Organización (sección 6 del reto) |

La ventana se configura con `VENTANA_DESDE` y `VENTANA_HASTA` (ver `.env.example`). Para el
conjunto de desarrollo, que puede usar otras fechas, se dejan vacías y no se filtra nada.

## Reglas de integridad (sección 7 del reto)

- UTF-8, IDs estables, fechas ISO 8601 en UTC; la interfaz muestra hora de Panamá.
- La fecha de publicación es distinta de `seendate` de GDELT (detección).
- Los nulos se conservan: nunca se rellenan con cero.
- Cada afirmación generada referencia el ID de evidencia y el campo que la respalda.