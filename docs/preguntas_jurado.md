# Preguntas dinámicas del jurado

Respuestas preparadas para las cuatro pruebas dinámicas de la sección 11 del reto (issue
[#22](https://github.com/Xyryung/faro-editorial/issues/22)). Para cada una: qué responder, qué
mostrar en pantalla y qué prueba automática lo respalda. Todo funciona sin internet.

## Antes de la demo

1. Cargar el snapshot y generar la bandeja (deben terminar sin errores):

   ```powershell
   uv run python -m faro_editorial.carga
   uv run python -m faro_editorial.bandeja --top 5
   ```

2. Elegir en `data/processed/bandeja.json` un tema con `vinculos_oficiales` (para la pregunta 1)
   y anotar su `id_evidencia`, por ejemplo `BM:PAN:NY.GDP.MKTP.KD.ZG:2023`.
3. Tener abiertas en el navegador: la [matriz de pruebas](https://github.com/Xyryung/faro-editorial/blob/main/evaluacion/matriz_pruebas.md),
   las [decisiones](https://github.com/Xyryung/faro-editorial/issues?q=label%3Adecision), las
   [pruebas fallidas](https://github.com/Xyryung/faro-editorial/issues?q=label%3Aprueba-fallida)
   y la página de [riesgos y ética](https://github.com/Xyryung/faro-editorial/blob/main/docs/riesgos_y_etica.md).

## 1. "Muéstrame de dónde proviene esta cifra y de qué año es"

**Respuesta:** cada cifra oficial lleva un ID de evidencia que apunta al registro exacto del
snapshot. La cita dice país, año, unidad e indicador, y aclara que es un dato anual, no una
medición de hoy.

**Mostrar:**

```powershell
uv run python -m faro_editorial.evidencia BM:PAN:NY.GDP.MKTP.KD.ZG:2023
```

```
Evidencia BM:PAN:NY.GDP.MKTP.KD.ZG:2023
  Fuente              Banco Mundial
  País                Panamá (PAN)
  Indicador           NY.GDP.MKTP.KD.ZG
  Año del dato        2023
  Valor               7.3
  Unidad              % anual
  URL de la consulta  https://api.worldbank.org/v2/country/PAN/indicator/NY.GDP.MKTP.KD.ZG
  Extraído            2025-10-01 00:00 UTC (2025-09-30 19:00 hora de Panamá)
  Licencia            CC BY 4.0

Dato anual de 2023: describe ese año, no la situación de hoy.
```

(Salida con los datos de prueba; con el snapshot real cambian los valores.) Funciona igual con
un sismo (`USGS:<id>`, con hora de Panamá y URL del evento) o con una noticia (`<id_noticia>`,
con fecha de publicación y de detección por separado).

**Respaldo:** T04 en la matriz; pruebas `test_t04_cifra_anual_con_pais_anio_unidad_e_id`,
`test_t04_nunca_se_presenta_como_dato_de_hoy` y `test_indicador_muestra_fuente_anio_unidad_y_url`.

## 2. "Si cinco medios replican la misma agencia, ¿cuántas fuentes independientes cuentas?"

**Respuesta:** **una.** Varias notas del mismo medio, o titulares casi idénticos de medios
distintos, son una sola procedencia independiente. La repetición no suma corroboración: cinco
medios con la misma nota de EFE tienen la misma evidencia que una sola nota y quedan con
evidencia "insuficiente".

**Mostrar:**

```powershell
uv run pytest tests/test_puntaje.py -k "cinco_medios or titulares_propios" -v
```

En `bandeja.json`, cada tema trae `procedencias` (los medios) y `procedencias_independientes`
(los medios agrupados por procedencia). El criterio del componente de evidencia lo explica: *"5
medios, 1 procedencia(s) independiente(s): titulares casi idénticos de medios distintos (posible
agencia replicada) cuentan como una"*.

**Cómo se decide:** similitud de titulares ≥ 0.9, sin tildes, mayúsculas, signos ni la firma de
agencia entre paréntesis ([decisión #46](https://github.com/Xyryung/faro-editorial/issues/46)).

**Límite (decirlo si preguntan):** una nota de agencia reescrita con otras palabras no se detecta
como réplica. Y dos notas propias casi iguales cuentan como una: el error va en la dirección
segura, porque cuenta menos fuentes, nunca más.

**Respaldo:** T02 en la matriz; pruebas
`test_cinco_medios_que_replican_la_misma_agencia_son_una_procedencia` y
`test_duplicar_noticias_no_infla_el_puntaje`.

## 3. "¿Qué ocurre si el sistema no tiene evidencia o una fuente intenta cambiar sus instrucciones?"

**Respuesta, sin evidencia:** se abstiene y dice qué falta. No inventa cifras ni citas.

- Si una noticia menciona un dato oficial que no está en el snapshot, no se vincula: queda como
  **pendiente de verificar**.
- Si se pide un ID que no existe, lo dice: *"No existe evidencia con el ID … en el snapshot
  cargado."*
- Si la IA falla o no hay internet y no hay respuesta guardada, la decisión es una
  **abstención explícita con motivo**, nunca un error ni una respuesta inventada.
- Una prioridad alta con una sola fuente queda con evidencia **"insuficiente"**: requiere
  investigar y no habilita publicación.

**Mostrar:**

```powershell
uv run python -m faro_editorial.evidencia BM:PAN:FP.CPI.TOTL.ZG:2024
```

```
No existe evidencia con el ID BM:PAN:FP.CPI.TOTL.ZG:2024 en el snapshot cargado.
```

**Respuesta, fuente que intenta cambiar instrucciones:** el texto de las fuentes es dato, nunca
instrucción. Viaja separado de las instrucciones (en Jev, en el campo de estado; en el LLM,
dentro de un bloque de datos que el prompt declara como dato), la salida está forzada a un
esquema JSON estricto con las opciones permitidas y las claves nunca están en el contenido.

**Mostrar:**

```powershell
uv run pytest tests/test_proveedores.py -k "separa or variante or esquema" -v
```

Y la [prueba fallida #43](https://github.com/Xyryung/faro-editorial/issues/43): al revisar el
código encontramos que `</DATOS>` en mayúsculas podía cerrar el bloque de datos; se corrigió en
el PR #40.

**Respaldo:** T06 y T07 en la matriz; pruebas
`test_indicador_sin_datos_queda_pendiente_sin_inventar`,
`test_t10_offline_sin_cache_se_abstiene_sin_llamar`, `test_llm_separa_instrucciones_de_datos` y
`test_ninguna_variante_del_cierre_escapa_del_bloque_de_datos`.

## 4. "Muéstrame en Notion una decisión, una prueba fallida y su corrección"

**Respuesta:** las decisiones y las pruebas fallidas se registran como issues en GitHub, que se
sincronizan con la base de datos de Notion.

**Mostrar (un ejemplo encadenado):**

1. **Decisión:** [#46](https://github.com/Xyryung/faro-editorial/issues/46), una agencia
   replicada cuenta como una sola procedencia (contexto, opciones con pros y contras, decisión,
   evidencia y riesgos). Otras: [#33](https://github.com/Xyryung/faro-editorial/issues/33) hora
   de Panamá, [#34](https://github.com/Xyryung/faro-editorial/issues/34) USGS en el período de
   las noticias, [#35](https://github.com/Xyryung/faro-editorial/issues/35) descripción del RSS
   solo para uso interno.
2. **Prueba fallida:** [#42](https://github.com/Xyryung/faro-editorial/issues/42) (T03): una
   nota vieja detectada hoy por GDELT contaba como evento nuevo.
3. **Corrección:** PR #37; la novedad usa también la fecha de detección. La prueba
   `test_una_sola_nota_vieja_detectada_hoy_no_es_novedad` falla con el código anterior y pasa con
   la corrección.
4. **En la matriz:** la fila T03 muestra la corrección con su issue y su PR.

Otras pruebas fallidas con su corrección: [#43](https://github.com/Xyryung/faro-editorial/issues/43)
(T07) y [#44](https://github.com/Xyryung/faro-editorial/issues/44) (T10), ambas corregidas en el
PR #40.

## Qué no afirmar

- Que el sistema detecta noticias falsas: ordena y documenta evidencia; la decisión es humana.
- Que el puntaje es una probabilidad de verdad: es una herramienta de ordenamiento.
- Métricas que no se midieron: macro-F1, Precision@5 y abstención se reportan solo con el
  snapshot real y etiquetas humanas (#19).
