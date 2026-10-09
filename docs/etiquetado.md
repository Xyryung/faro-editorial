# Método de etiquetado humano

Etiquetas humanas para evaluar la clasificación temática (issue #6, sección 9.1 del reto):
contra ellas se mide el macro-F1 de Jev y de la línea base por palabras clave
(`uv run python -m faro_editorial.clasificacion evaluar`).

## 1. Muestra

- **Tamaño:** 60 titulares.
- **Cómo se eligieron:** `uv run python -m faro_editorial.clasificacion muestra`. Es una muestra
  estratificada por el tema que propone la línea base por palabras clave, en rondas de un
  titular por tema, para que no salgan casi todos de "otro". Usa la semilla 7 (reproducible) y
  descarta los titulares repetidos.
- **Sin sesgo de origen:** el CSV entregado para etiquetar **no incluye el tema que propone el
  sistema**: solo `id_noticia`, `titulo` y `medio`.
- **Fuente:** el snapshot de la demo (noticias de TVN y otros medios; ver
  [`data/CONTRATO.md`](../data/CONTRATO.md)).

## 2. Categorías

Un solo tema por titular, de los siete de [`config/rules_v1.yaml`](../config/rules_v1.yaml),
escrito exactamente así:

| Tema | Cuándo |
|---|---|
| `economia` | precios, impuestos, DGI, empleo, finanzas públicas, comercio |
| `logistica_canal` | Canal de Panamá, puertos, tránsito de buques |
| `turismo` | visitantes, destinos, hoteles, ferias turísticas |
| `servicios_publicos` | salud, CSS, Minsa, educación, agua, luz, transporte |
| `eventos_naturales` | lluvias, clima, El Niño, sismos, deslizamientos |
| `regulacion` | leyes, vetos, Asamblea, reglamentos, decisiones de gobierno |
| `otro` | lo que no encaja en ninguno |

## 3. Criterio

- **Tema principal:** se etiqueta *qué fue lo que pasó*, no quién reacciona. Ejemplo: "Sinaproc
  mantiene vigilancia por fuertes lluvias" es `eventos_naturales`, no `servicios_publicos`.
- **Independiente del país:** el tema es de qué trata la noticia, aunque sea de otro país
  (lluvias en Caracas → `eventos_naturales`). La relación con Panamá la mide aparte el componente
  de relevancia del puntaje, y mezclarla con el tema contaminaría la métrica.
- **Un solo tema:** si un titular toca dos, se elige el principal y el otro se anota en
  `comentario`.

## 4. Quién etiquetó y cómo

| Integrante | Titulares |
|---|---|
| Rafael Aboulafia | 20 |
| David | 20 |
| Kenneth | 20 |

- **A ciegas:** cada integrante etiquetó su parte **sin consultar al modelo ni a ninguna IA**,
  como pide la herramienta de muestra. El CSV no traía el tema propuesto por el sistema.
- Cada fila lleva el nombre de quien la etiquetó en `etiquetador`.

## 5. Dudas y desacuerdos

- Los casos dudosos se anotaron en `comentario`, con la alternativa considerada.
- **Desacuerdos:** cuando dos integrantes no coincidían en un titular, cada uno justificó su
  respuesta y **el tercer integrante tomó la decisión final**. Así ningún titular quedó con la
  decisión de una sola persona cuando hubo duda.

## 6. Dónde están y separación del corpus

- Archivo: `data/evaluacion/etiquetas_tema.csv` (columnas `id_noticia`, `titulo`, `medio`,
  `tema_humano`, `etiquetador`, `comentario`).
- `data/` no se versiona en el repositorio público. Las etiquetas **no se mezclan con el corpus
  del agente**: la carga y la bandeja no leen esa carpeta; solo la usa `clasificacion evaluar`.

## 7. Limitaciones

- 60 titulares es una muestra chica: el macro-F1 es orientativo y se reporta con su tamaño.
- Muestra estratificada por la línea base: favorece que estén todos los temas, pero no refleja
  la proporción real de temas en el snapshot.
- Solo titular (sin el texto completo de la nota).
- Sin una medida formal de acuerdo entre personas (por ejemplo, kappa de Cohen): cada titular
  tuvo un etiquetador principal y solo los dudosos pasaron por la discusión de la sección 5.

## 8. Pares afirmación–evidencia (pendiente)

El issue #6 pide también 30 pares afirmación–evidencia etiquetados como respaldado / no
respaldado. Dependen de los borradores con citas (#15), que todavía no existen.
