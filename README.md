# Faro Editorial

Copiloto de inteligencia informativa para **TVN Media**, construido para el reto
*"De la señal a la decisión"* del hackIAthon (Viamatica · ADEN).

Convierte noticias públicas e indicadores oficiales en una **bandeja de temas priorizados**,
**fichas de evidencia** y **borradores editoriales** listos para revisión humana. Cada afirmación
se vincula con su fuente, fecha y alcance; cuando no hay evidencia suficiente, el sistema se abstiene.

## Estado

| Etapa | Módulo | Estado |
|---|---|---|
| 1 · Cargar | `carga.py`, `contrato.py`, `catalogo.py` | Implementada (#5, #4) |
| 2 · Organizar | `agrupacion.py`, `clasificacion.py` | Agrupación (#9) y tema por grupo con Jev y línea base por palabras clave (#10) |
| 3 · Contextualizar | `contexto.py` | Implementada (#12) |
| 4 · Priorizar | `puntaje.py`, `rules.py`, `bandeja.py` | Implementada con reglas; R, I y U admiten Jev (#11, #8) |
| 5 · Explicar | `app/main.py`, `interfaz.py`, `busqueda.py` | Bandeja y ficha de evidencia en Streamlit (#16); consulta en español con búsqueda híbrida (BM25 + semántica) y compuerta de abstención (#13); contradicciones en desarrollo (#14) |
| 6 · Producir | `borradores.py`, `app/main.py` | Brief, guion y copy con una cita por afirmación, verificación con Jev y pestaña Borrador; el borrador vigente vive en `borradores.jsonl` y la revisión lo copia en la ficha (#15) |
| 7 · Revisar | `revision.py`, `app/main.py` | Revisión humana en la ficha: estado, persona revisora y comentario en `fichas.jsonl` con historial, y ficha lista para Notion (#17) |

## Modalidad y usuario

- **Modalidad:** TVN · principal (editorial).
- **Usuario:** editor/a y periodista de TVN.
- **Salida:** agenda priorizada, ficha de investigación, preguntas pendientes y borradores
  (brief, guion de 45–60 s y copy digital), siempre sujetos a revisión humana.
  Nada se publica automáticamente.

## Flujo

1. **Cargar:** leer el snapshot congelado, validar y emitir un reporte de calidad.
2. **Organizar:** clasificar temas y agrupar noticias del mismo evento.
3. **Contextualizar:** relacionar noticias con indicadores y eventos oficiales.
4. **Priorizar:** puntaje de atención explicable (reglas versionadas en `config/`).
5. **Explicar:** ficha con qué se reporta, quién lo reporta, qué está respaldado y qué falta.
6. **Producir:** borrador con citas por afirmación.
7. **Revisar:** aceptación, corrección o descarte por una persona responsable.

## Arquitectura

```
snapshot (data/raw) -> validación y normalización -> DuckDB
  -> embeddings locales + agrupación + clasificación (Jev)
  -> vínculo con indicadores (Banco Mundial) y eventos (USGS)
  -> puntaje de atención (reglas v1) -> generación con citas (LLM)
  -> verificación de cada afirmación (Jev) -> caché -> interfaz Streamlit -> revisión -> Notion
```

| Capa | Tecnología |
|---|---|
| Lenguaje y dependencias | Python 3.12, uv (`uv.lock`) |
| Validación | pydantic |
| Almacenamiento | DuckDB, JSONL |
| Embeddings | sentence-transformers (modelo multilingüe local, CPU) |
| Línea base | BM25 y reglas por palabras clave |
| Decisiones (clasificación, puntajes, verificación) | Jev (TypeSafe) vía OpenRouter, detrás de una interfaz intercambiable |
| Generación | LLM vía OpenRouter, salida JSON con citas |
| Interfaz | Streamlit |

## Requisitos

- [uv](https://docs.astral.sh/uv/) (instala Python 3.12 automáticamente si no lo tienes).
- Git.

## Instalación

```powershell
git clone https://github.com/Xyryung/faro-editorial.git
cd faro-editorial
uv sync
Copy-Item .env.example .env   # en macOS/Linux: cp .env.example .env
```

Completa `.env` solo si vas a hacer llamadas en vivo. Sin `.env`, la app arranca en modo offline.

## Ejecución

1. Copia el snapshot en `data/raw/` y cárgalo. El snapshot no está en este repositorio público:
   si recibiste el paquete de datos (`faro-editorial-datos-<versión>.zip`), copia el contenido de
   su carpeta `raw/` en `data/raw/`. El formato está en [`data/CONTRATO.md`](data/CONTRATO.md).
   La carga valida cada fila, verifica los SHA-256 contra el manifest y genera la base DuckDB, el
   reporte de calidad y el catálogo de datos en `data/processed/`. Debe decir
   `Integridad del snapshot: OK`:

   ```powershell
   uv run python -m faro_editorial.carga
   ```

   Por defecto se cargan noticias y sismos del 2025-10-01 al 2026-09-30 en hora de Panamá
   (período de la demo). Para datos de otras fechas, deja `VENTANA_DESDE` y `VENTANA_HASTA`
   vacías en `.env`.

2. Agrupa las noticias del mismo evento (`data/processed/grupos.jsonl` y un resumen en
   `agrupacion.json`). Usa el modelo de embeddings local si está disponible y, si no, TF-IDF:

   ```powershell
   uv run python -m faro_editorial.agrupacion
   ```

   Asigna un tema a cada grupo con Jev (con caché) y, si Jev se abstiene, con la línea base
   por palabras clave. La primera vez necesita internet y la clave de OpenRouter (`OFFLINE=0`);
   después funciona sin internet desde la caché. Vuelve a ejecutarlo si rehaces la agrupación:

   ```powershell
   uv run python -m faro_editorial.clasificacion
   ```

   Genera la bandeja priorizada (`data/processed/bandeja.json`) y muestra los cinco temas que
   merecen revisión (CU-01). Si existe `data/processed/grupos.jsonl`, lo usa; si no, cada
   noticia es un grupo:

   ```powershell
   uv run python -m faro_editorial.bandeja --top 5
   ```

   Redacta el borrador de los temas principales: brief (≤ 250 palabras), guion de 45–60 s y
   copy (≤ 80 palabras), con una cita `{id_evidencia, campo}` por afirmación, afirmaciones
   tipadas (hecho, declaración, inferencia, hipótesis) y verificación de cada una con Jev.
   Escribe `data/processed/borradores.jsonl` (se ve en la pestaña Borrador) y un Markdown por
   tema en `data/processed/borradores/`. Al guardar una revisión, la ficha copia el borrador
   vigente. La primera vez necesita `OFFLINE=0`, la clave de OpenRouter y `LLM_MODEL`; después
   funciona sin internet desde la caché:

   ```powershell
   uv run python -m faro_editorial.borradores --top 5
   ```

3. Abre la interfaz:

   ```powershell
   uv run streamlit run app/main.py
   ```

   - **Bandeja y ficha:** temas ordenados por puntaje; cada ficha muestra qué se reporta, quién
     lo reporta, qué está respaldado, qué falta comprobar, la acción recomendada y el puntaje
     desglosado.
   - **Revisión humana:** al final de cada ficha, la persona revisora elige el estado (nuevo, en
     revisión, requiere evidencia, aprobado como borrador o descartado), escribe su nombre y un
     comentario. Cada decisión se agrega a `data/processed/fichas.jsonl` con su historial.
     Aprobar no publica nada. "Copiar ficha para Notion" la deja lista para "Casos y evidencias".
   - **Consulta:** pregunta en español; responde con las noticias que la sustentan o se abstiene
     y explica qué falta. También desde la consola:

     ```powershell
     uv run python -m faro_editorial.busqueda "¿qué lluvias hubo en Chiriquí?"
     ```

### Modo offline

Con `OFFLINE=1` (valor por defecto) la aplicación usa solo datos y respuestas en caché locales:
no necesita internet ni claves. Así se ejecuta la demo ante el jurado. El flujo completo (carga,
agrupación, clasificación, bandeja, interfaz y revisión) se ensayó sin internet sobre un clon
limpio del repositorio (#61); la letra de la interfaz (IBM Plex Sans) viene incluida en
`app/static/fonts`.

- **Jev:** responde desde la caché (`data/cache/`). Sin respuesta guardada, la clasificación usa
  la línea base por palabras clave y la consulta se abstiene; ambas lo declaran.
- **Modelo de embeddings** (`intfloat/multilingual-e5-small`): sin internet se usa solo si ya
  está descargado; si no, la agrupación y la consulta usan TF-IDF al instante y lo avisan. Para
  la demo, descárgalo antes con internet (una sola vez):

  ```powershell
  uv run python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"
  ```

## Pruebas y calidad

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Las pruebas marcadas `online` (requieren claves) y `model` (requieren el modelo de embeddings
descargado) se omiten por defecto. Para ejecutarlas: `uv run pytest -m online`.

### Pruebas de aceptación del reto (T01–T10)

La matriz con caso, entrada, resultado esperado, resultado observado, evidencia de ejecución y
corrección está en [`evaluacion/matriz_pruebas.md`](evaluacion/matriz_pruebas.md) (y en CSV para
importar en Notion). Se regenera ejecutando las pruebas:

```powershell
uv run python -m faro_editorial.matriz
```

Qué pruebas cubren cada caso se define en `config/matriz_pruebas.yaml`; una prueba que falla
queda en la matriz junto a su issue de "Prueba fallida" y el PR que la corrigió.

Las métricas de la ejecución final (cobertura de citas, abstención, macro-F1, Precision@5 y
tiempos) se registran en #19. La clasificación se evalúa contra etiquetas humanas:

```powershell
uv run python -m faro_editorial.clasificacion muestra   # titulares para etiquetar a mano
uv run python -m faro_editorial.clasificacion evaluar   # macro-F1 de Jev y de la línea base
```

El reporte de las métricas de la sección 9.1 (numerador, denominador y fallos de cada una) se
arma con `metricas`. Las entradas con titulares quedan en `data/evaluacion/` (no se versiona);
el reporte, en [`evaluacion/metricas.md`](evaluacion/metricas.md), solo lleva conteos e IDs. Una
métrica sin su entrada queda "pendiente" con el comando que la produce, nunca con un número:

```powershell
uv run python -m faro_editorial.metricas pares       # afirmación-evidencia para revisión humana
uv run python -m faro_editorial.metricas seleccion   # 15 temas a ciegas para Precision@5
uv run python -m faro_editorial.metricas consultas   # abstención con config/consultas_v1.yaml
uv run python -m faro_editorial.metricas reporte     # escribe evaluacion/metricas.md
```

`consultas` mide la compuerta completa (umbral + Jev Noul) solo con `OFFLINE=0`; con
`OFFLINE=1` decide solo el umbral, igual que la interfaz sin internet.

## Datos

Ver [`data/README.md`](data/README.md) y el diccionario de datos en
[`data/CONTRATO.md`](data/CONTRATO.md). El snapshot no se versiona en este repo público hasta
confirmar las condiciones de redistribución; se entrega como paquete aparte:

```powershell
uv run python -m faro_editorial.paquete
```

Genera en `dist/` un `.zip` con el snapshot, el manifest, el diccionario, el catálogo con las
licencias y condiciones de cada fuente y el reporte de calidad.

Para la demo se usa un recorte de **300 noticias de los 30 días previos al corte**, como pide el
reto (meta: 200 registros únicos; mínimo: 100, con al menos 20 de TVN). Se reparten por día y,
dentro de cada día, alternando medios; el manifest del recorte registra el criterio y la
cobertura efectiva (días, medios y cuántas son de TVN), y la carga verifica su integridad igual
que con el snapshot completo:

```powershell
uv run python -m faro_editorial.recorte --n 300 --dias 30 --destino data/demo300/raw
$env:DATA_DIR = "data/demo300"   # luego carga, agrupacion, clasificacion y bandeja
```

## Demo y preguntas del jurado

Respuestas preparadas para las cuatro pruebas dinámicas del jurado, con qué mostrar en pantalla:
[`docs/preguntas_jurado.md`](docs/preguntas_jurado.md). Para mostrar el registro original detrás
de cualquier cita (país, año, unidad, URL y licencia):

```powershell
uv run python -m faro_editorial.evidencia BM:PAN:NY.GDP.MKTP.KD.ZG:2023
```

## Decisiones

Las decisiones técnicas y de producto se registran como issues con la etiqueta
[`decision`](https://github.com/Xyryung/faro-editorial/issues?q=label%3Adecision).

## Flujo de trabajo del equipo

1. Cada trabajo nace como **issue** (plantillas: Tarea, Decisión, Prueba fallida).
2. Rama desde `main`: `feat/12-agrupacion`, `fix/18-fechas-nulas`, `docs/...`, `test/...`, `chore/...`.
3. Commits con [Conventional Commits](https://www.conventionalcommits.org/es/): `feat(puntaje): ...`.
4. **Pull request** con `Closes #<issue>` (GitHub solo cierra el issue al fusionar con las
   palabras en inglés; si el PR no completa el issue, `Parte de #<issue>`). El CI debe pasar
   para poder fusionar.
5. Fusión con *squash*; el título del PR queda como mensaje del commit.

Los issues y PRs se sincronizan con Notion (base de datos sincronizada de GitHub), que es el
registro oficial del reto.

## Estructura

```
app/                 Interfaz Streamlit (estilo en estilo.css, letra en static/fonts)
config/              Reglas versionadas: puntaje y criterios, contexto oficial, agrupación,
                     clasificación, búsqueda, catálogo y matriz de pruebas
data/                raw/ (snapshot), processed/ (regenerable, incluye fichas.jsonl),
                     cache/ (respuestas IA) y CONTRATO.md (diccionario de datos)
docs/                Riesgos y ética, respuestas a las preguntas del jurado
evaluacion/          Matriz T01–T10 generada (Markdown, CSV para Notion y JSON)
src/faro_editorial/  Paquete principal
tests/               Pruebas, incluidas las de aceptación T01–T10 del reto
.github/             CI, plantillas de issues y de PR
```

## Seguridad

Riesgos, derechos por fuente, sesgos y cada control con su código y su prueba:
[`docs/riesgos_y_etica.md`](docs/riesgos_y_etica.md).

- Las claves viven solo en `.env` (ignorado por Git). Nunca en código, Notion, capturas ni logs.
- El texto de las fuentes se trata como dato, nunca como instrucción.
- No se almacenan datos personales innecesarios ni se etiquetan noticias como verdaderas o falsas.

## Equipo

- Kenneth ([@Xyryung](https://github.com/Xyryung))
- Rafael Aboulafia ([@rafael23231](https://github.com/rafael23231))
- David ([@Cod7777](https://github.com/Cod7777))

## Licencia

Pendiente de confirmar con la organización. Hasta entonces, todos los derechos reservados.