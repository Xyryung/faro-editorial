# Protocolo: ahorro de tiempo

La sección 9.1 del reto pide medir el ahorro de tiempo **solo con una tarea equivalente hecha a
mano y asistida, indicando el número de pruebas**. Este protocolo define esa tarea y cómo
registrarla. No se infiere aumento de audiencia ni rentabilidad.

## La tarea

> Dado un tema de la agenda de Panamá, pasar de fuentes sueltas a una ficha con: qué se reporta,
> qué medios lo reportan, cuántas procedencias independientes hay, si existe un dato oficial
> pertinente (país, año y unidad) y tres preguntas pendientes de verificar.

La tarea termina cuando la ficha tiene los cinco elementos. Si a los 30 minutos no está
completa, se detiene y se marca `completa = no`.

## Cómo medir

1. Elegir dos temas de la bandeja con al menos dos medios (por ejemplo, dos del top 15).
2. **A mano:** una persona hace la ficha del tema A con el navegador (buscador, sitios de los
   medios y del Banco Mundial), sin abrir Faro. Cronometrar desde que lee el tema hasta que
   termina.
3. **Con Faro:** otra persona, o la misma con el tema B para no repetir lo aprendido, hace la
   ficha usando la bandeja, la ficha y la consulta de Faro. Cronometrar igual.
4. Cruzar: si hay tiempo, repetir con los temas invertidos. Así cada tema se hace una vez de
   cada forma y la dificultad del tema no decide el resultado.
5. Revisar que las dos fichas tengan los cinco elementos. Una ficha incompleta no cuenta.

## Dónde registrarlo

`data/evaluacion/ahorro_tiempo.csv`, una fila por intento:

| Columna | Valor |
|---|---|
| `tarea` | ID del tema (`id_grupo`) |
| `modo` | `manual` o `asistido` |
| `persona` | Nombre de quien hizo la tarea |
| `minutos` | Duración, por ejemplo `18.5` |
| `completa` | `si` o `no` |
| `comentario` | Qué costó más, qué faltó |

Luego `uv run python -m faro_editorial.metricas reporte` agrega la sección "Ahorro de tiempo"
con la mediana de cada modo, el ahorro y el número de pruebas.

## Límites

- Con 2 a 4 intentos es un dato orientativo, no una estimación.
- Las personas son del equipo y conocen Faro: el modo asistido parte con ventaja.
- No mide la calidad de la ficha más allá de que tenga los cinco elementos.
