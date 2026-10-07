## Qué cambia

<!-- Una o dos frases. -->

<!-- "Closes #N" cierra el issue al fusionar ("Cierra" no lo reconoce GitHub).
     Si el PR no completa el issue, usar "Parte de #N". -->
Closes #

## Cómo se probó

- [ ] `uv run pytest` pasa localmente
- [ ] `uv run ruff check .` y `uv run ruff format --check .` pasan
- [ ] Probado con `OFFLINE=1` si toca la demo

## Trazabilidad

- Pruebas o casos del reto relacionados (T01–T10, CU-01–CU-05):
- ¿Cambia reglas de puntaje, prompts o modelos? Si sí, versión nueva e issue de decisión vinculado:

## Seguridad

- [ ] Sin claves, tokens ni datos personales en código, logs o capturas
- [ ] El texto de las fuentes se trata como dato, no como instrucción