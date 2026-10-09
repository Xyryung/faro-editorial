# Métricas de la ejecución final

Generado el 2026-10-09T08:00:23+00:00 con `uv run python -m faro_editorial.metricas reporte` · snapshot con corte 2026-10-01 00:00 (hora de Panamá).

Metas orientativas de la sección 9.1 del reto, no resultados. Cada métrica muestra numerador, denominador y fallos. Una métrica pendiente no tiene número: dice qué falta.

| Métrica | Resultado | IC 95 % (Wilson) | Meta | Cumple |
|---|---|---|---|---|
| Cobertura de citas | 32/32 (100.0 %) | 89.3–100.0 % | 100 % | sí |
| Validez de sustento (revisión humana) | 30/32 (93.8 %) | 79.8–98.3 % | ≥ 90 % con ≥ 30 pares | sí |
| Abstención en consultas sin respuesta | 8/10 (80.0 %) | 49.0–94.3 % | ≥ 80 % | sí |
| Precision@5 (exploratoria) | 1/5 (20.0 %) | 3.6–62.5 % | — | — |
| Macro-F1 clasificación · palabras | 0.764 (45/60 aciertos) | 62.8–84.2 % (aciertos) | — | — |
| Macro-F1 clasificación · jev_es | 0.741 (44/60 aciertos) | 61.0–82.9 % (aciertos) | — | — |
| Macro-F1 clasificación · jev_en | 0.805 (48/60 aciertos) | 68.2–88.2 % (aciertos) | — | — |
| Agrupación · e5 | F1 0.714 · precisión 15/18 (83.3 %) · recall 15/24 (62.5 %) | P 60.8–94.2 % · R 42.7–78.8 % | — | — |
| Agrupación · tfidf | F1 0.343 · precisión 6/11 (54.5 %) · recall 6/24 (25.0 %) | P 28.0–78.7 % · R 12.0–44.9 % | — | — |
| Mediana por consulta (p95) | 0.032 s (0.035 s) | — | ≤ 15 s | sí |

## Cobertura de citas

Método: Afirmaciones factuales emitidas con al menos una cita a un ID de la evidencia recuperada del tema / afirmaciones factuales emitidas (incluye las rechazadas). Las hipótesis no cuentan: se investigan, no se afirman.

- 9 borradores (0 abstenciones), 0 afirmaciones rechazadas.

## Validez de sustento

Método: Afirmaciones que la persona marca 'respaldada' por la evidencia citada / pares etiquetados. Etiquetado a ciegas: el CSV no muestra el veredicto de Jev.

- Etiquetado por: Kenneth · filas sin etiquetar: 0.
- Pares suficientes (≥ 30): sí.
- Acuerdo de Jev con la persona: 28/32 (87.5 %). Jev 'respaldada' frente a la persona; 'dudosa' y 'no_respaldada' cuentan como no respaldada.
  - Desacuerdo: g-7df90d19a433:a6 (persona: respaldada, Jev: dudosa)
  - Desacuerdo: tvn2-a549c78c052b:a1 (persona: respaldada, Jev: dudosa)
  - Desacuerdo: tvn2-a549c78c052b:a3 (persona: respaldada, Jev: dudosa)
  - Desacuerdo: critica-d08d53cd9537:a2 (persona: no_respaldada, Jev: respaldada)
- Fallo: g-75a66de0bfb3:a4
- Fallo: critica-d08d53cd9537:a2

## Abstención

Método: No respondibles rechazadas / no respondibles del conjunto. Una respondible solo cuenta como bien respondida si cita al menos una evidencia esperada.

- Conjunto consultas-v1, revisado por: Kenneth · con Jev: sí.
- No respondibles difíciles rechazadas: 3/4.
- No respondibles que se respondieron: N04, N07.
- Abstenciones incorrectas en respondibles: 0/10 (0.0 %): ninguna.
- Respondibles con la evidencia esperada citada: 10/10 (100.0 %); fallos: ninguno.

## Precision@5

Método: Una persona eligió a ciegas 5 de los 15 temas mejor puntuados, presentados en orden aleatorio y sin puntaje; P@5 = coincidencias con el top 5 del sistema / 5. Exploratoria: la eligió un integrante del equipo, no un editor de TVN.

- Evaluador: Kenneth (integrante del equipo; vio la bandeja antes) · candidatos: 15.
- Coinciden: g-7df90d19a433 · solo el sistema: g-75a66de0bfb3, tvn2-123983567584, tvn2-5e7b54b3fa43, tvn2-662b0d8e525c · solo la persona: g-17b3e381eca8, g-acb419946a70, g-6198e64768be, laestrella-39e9bc105470.

## Clasificación

Método: macro-F1 sobre los temas presentes en las etiquetas humanas; una abstención de la IA cuenta como error.

- 60 titulares etiquetados; método en docs/etiquetado.md.

## Agrupación

Método: Pares de titulares etiquetados a ciegas como 'mismo evento' o no; precisión = pares agrupados que son el mismo evento / pares agrupados; recall = pares del mismo evento que el método agrupó / pares del mismo evento de la muestra. Muestra estratificada, no poblacional.

- 40 pares etiquetados por David, Kenneth, Rafael, 24 del mismo evento · estratos: solo_tfidf 9, parecido_no_agrupado 13, agrupado_snapshot 18.
- e5: TP 15 · FP 3 · FN 9 · TN 13; errores: P02, P07, P10, P13, P17, P21, P26, P27, P29, P34, P39, P40.
- tfidf: TP 6 · FP 5 · FN 18 · TN 11; errores: P01, P03, P08, P09, P11, P12, P17, P19, P20, P21, P22, P23, P24, P25, P26, P27, P28, P30, P32, P33, P34, P36, P38.

## Ahorro de tiempo

Pendiente: No hay tareas cronometradas a mano y con Faro. Comando: `completar data/evaluacion/ahorro_tiempo.csv (protocolo en docs/ahorro_tiempo.md)`

## Eficiencia

- Consultas: 20, mediana 0.032 s, p95 0.035 s · llamadas a Jev en vivo: 0 · costo USD 0.0 (0.0 por consulta) · tokens 0 de entrada y 0 de salida. El índice semántico se prepara una vez al iniciar (24.723 s), fuera de la medición por consulta.
- Borradores: 9, mediana 33.445 s, p95 57.578 s, 1.67 intentos en promedio · costo USD 0.019373 (LLM) + 0.000872 (Jev) · tokens registrados 23196 / 41310. Solo el tiempo del LLM (todos los intentos), sin la verificación con Jev. Es preparación por lotes antes de la demo, no la consulta.
