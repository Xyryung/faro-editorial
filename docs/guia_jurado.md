# Guía para el jurado

Recorrido de Faro Editorial en el orden del reto: para cada etapa, qué pantalla mostrarla, qué
prueba la respalda y qué métrica la mide. Todo funciona sin internet con el snapshot y las
respuestas guardadas (`OFFLINE=1`). Las respuestas a las cuatro preguntas dinámicas están en
[`preguntas_jurado.md`](preguntas_jurado.md).

## Cómo abrirlo

```powershell
uv sync
uv run streamlit run app/main.py      # http://localhost:8501
```

Con el snapshot ya cargado (`data/processed/`). Si falta, la app explica cómo cargarlo (T10).
Recorrido sugerido: **Bandeja → ficha de un tema → Borrador → Revisión → Consulta**.

## Las siete etapas

| Etapa del reto | Dónde verlo | Prueba (matriz) | Métrica o evidencia |
|---|---|---|---|
| 1 · Cargar | Configuración: snapshot, corte y versión de reglas. `uv run python -m faro_editorial.carga` imprime el reporte de calidad | T01 · 26 pruebas | Integridad por SHA-256 del manifest; rechazos con motivo en `reporte_calidad.json` |
| 2 · Organizar | Bandeja: tema de cada fila; ficha: noticias del grupo con su medio | T02 · 6 pruebas | Clasificación: macro-F1 0,805 (Jev) vs 0,764 (palabras clave), 60 titulares. Agrupación: F1 0,714 (e5) vs 0,343 (TF-IDF), 40 pares a ciegas |
| 3 · Contextualizar | Ficha: bloque de respaldo oficial (Banco Mundial o USGS) con año, unidad y limitación | T04 · 9 pruebas, T06 | 68 temas con vínculo oficial; sin relación sustentada, no se vincula |
| 4 · Priorizar | Bandeja ordenada; ficha: desglose R, I, U, N, E con el criterio de cada uno | T08 · 6 pruebas | Precision@5 exploratoria 1/5; puntaje reproducible (`reglas-v1.0`, `criterios-v2.0`) |
| 5 · Explicar | Ficha: qué se reporta, quién, qué está respaldado, qué falta y acción recomendada | T03, T05 | Estado de evidencia aparte del puntaje: 4.264 sin corroborar, 68 parciales, 42 suficientes |
| 6 · Producir | Pestaña Borrador: brief, guion, copy, 3 preguntas, cada afirmación con su cita y el veredicto de Jev | T07, T09 · 29 pruebas | Cobertura de citas 32/32; validez de sustento 30/32 (93,8 %, IC 79,8–98,3 %) |
| 7 · Revisar | Ficha: formulario de revisión (estado, responsable, comentario) e historial; texto para Notion | T08 | Ningún estado habilita publicar; el historial nunca se borra |
| Consulta en español | Pestaña Consulta: respuesta con citas o abstención explicada | T06, T07 · 38 pruebas | Abstención 8/10 (IC 49,0–94,3 %); 0/10 abstenciones incorrectas; mediana 0,032 s |

Las cifras son de la ejecución final ([`evaluacion/metricas.md`](../evaluacion/metricas.md)) y
de la matriz T01–T10 ([`evaluacion/matriz_pruebas.md`](../evaluacion/matriz_pruebas.md)).

## Casos para mostrar en 4 minutos

1. **Consulta útil:** en Consulta, "¿Cómo han cambiado los casos de dengue en Panamá?" → responde
   citando los titulares de TVN.
2. **Ficha con citas:** el primer tema de la bandeja (presupuesto del Canal, 92,5 puntos) →
   desglose del puntaje y estado de evidencia.
3. **Borrador:** pestaña Borrador del mismo tema → cada afirmación con su ID de evidencia.
4. **Abstención:** en Consulta, "¿Cuál es el salario mínimo aprobado en Panamá para 2027?" → se
   abstiene y dice qué falta.

## Ensayo sin internet (T10)

Hacerlo en la máquina de la demo, con la caché de Jev y los borradores ya copiados en `data/`.

1. Apagar el Wi-Fi y desconectar el cable de red.
2. Captura 1: el ícono de red sin conexión y la hora del sistema visibles.
3. `uv run streamlit run app/main.py` y abrir la bandeja. Captura 2.
4. Abrir la ficha del primer tema y la pestaña Borrador. Captura 3.
5. Hacer la consulta útil y la de abstención del punto anterior. Capturas 4 y 5.
6. Guardar las capturas con la fecha y la hora en el nombre y enlazarlas en la fila T10 de
   "Pruebas y métricas" en Notion, con el commit que se ejecutó (`git rev-parse --short HEAD`).

Si algo falla, anotarlo en el registro de pruebas fallidas antes de corregirlo.

## Qué no hace

No publica, no dice si una noticia es verdadera o falsa y no mide audiencia. Trabaja con
titulares y metadatos, no con el texto completo. Límites y riesgos:
[`riesgos_y_etica.md`](riesgos_y_etica.md).
