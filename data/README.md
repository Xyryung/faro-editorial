# Datos

| Carpeta | Contenido | ¿Se sube al repo? |
|---|---|---|
| `raw/` | Snapshot congelado entregado por la organización ("Panamá · Señales y Evidencias v1"): `noticias.csv`, `fuentes.json`, `indicadores.csv`, `eventos.geojson`, `manifest.json`. Nunca se modifica. | No, hasta confirmar condiciones de redistribución |
| `processed/` | Datos validados y normalizados, base DuckDB, reporte de calidad y archivo de rechazos. Se regenera con el pipeline. | No (regenerable) |
| `cache/` | Respuestas guardadas de Jev, del LLM y embeddings, indexadas por hash de (modelo + versión de prompt + entrada). Permite la demo sin internet (T10). | Por decidir con la organización |

## Por qué no hay datos en el repo todavía

El repositorio es público. El reto indica que el patrocinio no concede derechos de
republicación y que, si una fuente restringe la redistribución, se entregan metadatos
y receta, no el contenido protegido. Hasta que la organización confirme qué se puede
publicar, todo el contenido de `data/` queda fuera de Git (ver `.gitignore`).
Cuando se confirme, se registra la decisión en un issue con la etiqueta `decision`.

## Cómo preparar los datos localmente

1. Copia los archivos del snapshot oficial en `data/raw/`.
2. Verifica el SHA-256 de cada archivo contra `manifest.json`
   (PowerShell: `Get-FileHash data\raw\noticias.csv -Algorithm SHA256`).
3. Ejecuta el pipeline de carga (se documentará aquí cuando exista).

## Reglas de integridad (sección 7 del reto)

- UTF-8, IDs estables, fechas ISO 8601 en UTC; la interfaz muestra hora de Panamá.
- La fecha de publicación es distinta de `seendate` de GDELT (detección).
- Los nulos se conservan: nunca se rellenan con cero.
- Cada afirmación generada referencia el ID de evidencia y el campo que la respalda.