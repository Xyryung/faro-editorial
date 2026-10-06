# Faro Editorial

Copiloto de inteligencia informativa para **TVN Media**, construido para el reto
*"De la señal a la decisión"* del hackIAthon (Viamatica · ADEN).

Convierte noticias públicas e indicadores oficiales en una **bandeja de temas priorizados**,
**fichas de evidencia** y **borradores editoriales** listos para revisión humana. Cada afirmación
se vincula con su fuente, fecha y alcance; cuando no hay evidencia suficiente, el sistema se abstiene.

> Estado: esqueleto inicial. Las funcionalidades se construyen durante el evento.

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

## Datos

Ver [`data/README.md`](data/README.md). El snapshot oficial no se versiona en este repo público
hasta confirmar las condiciones de redistribución.

## Flujo de trabajo del equipo

1. Cada trabajo nace como **issue** (plantillas: Tarea, Decisión, Prueba fallida).
2. Rama desde `main`: `feat/12-agrupacion`, `fix/18-fechas-nulas`, `docs/...`, `test/...`, `chore/...`.
3. Commits con [Conventional Commits](https://www.conventionalcommits.org/es/): `feat(puntaje): ...`.
4. **Pull request** con `Cierra #<issue>`; el CI debe pasar para poder fusionar.
5. Fusión con *squash*; el título del PR queda como mensaje del commit.

Los issues y PRs se sincronizan con Notion (base de datos sincronizada de GitHub), que es el
registro oficial del reto.

## Estructura

```
app/                 Interfaz Streamlit
config/              Reglas versionadas del puntaje
data/                raw/ (snapshot), processed/ (regenerable), cache/ (respuestas IA)
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
- _Integrante 2_
- _Integrante 3_

## Licencia

Pendiente de confirmar con la organización. Hasta entonces, todos los derechos reservados.