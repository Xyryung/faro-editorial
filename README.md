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
| 2 · Organizar | | En desarrollo (#9, #10) |
| 3 · Contextualizar | `contexto.py` | Implementada (#12) |
| 4 · Priorizar | `puntaje.py`, `rules.py` | Implementada con reglas; R, I y U admiten Jev (#11, #8) |
| 5 · Explicar | | En desarrollo (#13, #14, #18) |
| 6 · Producir | | En desarrollo (#15) |
| 7 · Revisar | | En desarrollo (#16, #17) |

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

1. Copia el snapshot en `data/raw/` (formato en [`data/CONTRATO.md`](data/CONTRATO.md)) y cárgalo.
   Valida cada fila, verifica los SHA-256 contra el manifest y genera la base DuckDB, el reporte
   de calidad y el catálogo de datos en `data/processed/`:

   ```powershell
   uv run python -m faro_editorial.carga
   ```

2. Abre la interfaz:

   ```powershell
   uv run streamlit run app/main.py
   ```

### Modo offline

Con `OFFLINE=1` (valor por defecto) la aplicación usa solo datos y respuestas en caché locales:
no necesita internet ni claves. Así se ejecuta la demo ante el jurado.

## Pruebas y calidad

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Las pruebas marcadas `online` (requieren claves) y `model` (requieren el modelo de embeddings
descargado) se omiten por defecto. Para ejecutarlas: `uv run pytest -m online`.

### Pruebas de aceptación del reto (T01–T10)

| ID | Prueba | Dónde | Estado |
|---|---|---|---|
| T01 | Fechas inválidas y nulos | `tests/test_carga.py` | Automatizada |
| T02 | Tres registros del mismo evento | | Pendiente (#9) |
| T03 | Noticia antigua recirculada | | Pendiente (#14) |
| T04 | Cifra anual del Banco Mundial | `tests/test_contexto.py` | Automatizada |
| T05 | Dos afirmaciones incompatibles | | Pendiente (#14) |
| T06 | Consulta sin respuesta | | Pendiente (#13) |
| T07 | Fuente que exige ignorar instrucciones | | Pendiente (#15) |
| T08 | Caso de prioridad alta | `tests/test_puntaje.py` | Automatizada |
| T09 | Brief editorial | | Pendiente (#15) |
| T10 | Sin internet durante la demo | | Pendiente (#21) |

Las métricas de la ejecución final (cobertura de citas, abstención, macro-F1, Precision@5 y
tiempos) se registran en #19.

## Datos

Ver [`data/README.md`](data/README.md) y el diccionario de datos en
[`data/CONTRATO.md`](data/CONTRATO.md). El snapshot no se versiona en este repo público hasta
confirmar las condiciones de redistribución; se entrega como paquete aparte:

```powershell
uv run python -m faro_editorial.paquete
```

Genera en `dist/` un `.zip` con el snapshot, el manifest, el diccionario, el catálogo con las
licencias y condiciones de cada fuente y el reporte de calidad.

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
app/                 Interfaz Streamlit
config/              Reglas versionadas: puntaje y sus criterios, contexto oficial y catálogo
data/                raw/ (snapshot), processed/ (regenerable), cache/ (respuestas IA)
                     y CONTRATO.md (diccionario de datos)
src/faro_editorial/  Paquete principal
tests/               Pruebas (incluirán T01–T10 del reto)
.github/             CI, plantillas de issues y de PR
```

## Seguridad

- Las claves viven solo en `.env` (ignorado por Git). Nunca en código, Notion, capturas ni logs.
- El texto de las fuentes se trata como dato, nunca como instrucción.
- No se almacenan datos personales innecesarios ni se etiquetan noticias como verdaderas o falsas.

## Equipo

- Kenneth ([@Xyryung](https://github.com/Xyryung))
- Rafael Aboulafia ([@rafael23231](https://github.com/rafael23231))
- [@Cod7777](https://github.com/Cod7777)

## Licencia

Pendiente de confirmar con la organización. Hasta entonces, todos los derechos reservados.