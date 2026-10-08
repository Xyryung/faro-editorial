# Riesgos y ética

Página "Riesgos y ética" del espacio de Notion (sección 5 del reto; issue
[#20](https://github.com/Xyryung/faro-editorial/issues/20)). Cada control enlaza al código que
lo implementa y a la prueba automática que lo verifica. Lo que todavía no está implementado se
marca como **pendiente**, con su issue.

Principio general: Faro Editorial **ordena y documenta evidencia para que una persona decida**.
No publica, no declara verdadera o falsa una noticia y, si no hay evidencia, se abstiene.

## 1. Controles implementados

| Riesgo | Control | Código | Prueba |
|---|---|---|---|
| Una fuente intenta dar órdenes al modelo (inyección, T07) | El texto de las fuentes viaja separado de las instrucciones: en Jev, en el campo `state`; en el LLM, dentro de un bloque `<datos>` que el prompt declara como dato y no como instrucción | [`proveedores.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/proveedores.py) | `test_llm_separa_instrucciones_de_datos`, `test_jev_envia_el_formato_de_la_decisions_api` ([pruebas](https://github.com/Xyryung/faro-editorial/blob/main/tests/test_proveedores.py)) |
| Una fuente "cierra" el bloque de datos con `</DATOS>` o variantes | Escape de cualquier variante del cierre, sin distinguir mayúsculas ni espacios | [`proveedores.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/proveedores.py) | `test_ninguna_variante_del_cierre_escapa_del_bloque_de_datos`. Falló y se corrigió: [#43](https://github.com/Xyryung/faro-editorial/issues/43) |
| El modelo responde fuera de lo pedido | Salida JSON con esquema estricto (solo las opciones permitidas); una respuesta inválida se convierte en abstención y no se guarda | [`proveedores.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/proveedores.py), [`decisiones.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/decisiones.py) | `test_llm_esquema_estricto_con_las_opciones_permitidas`, `test_respuesta_invalida_es_abstencion_y_no_se_guarda` |
| Inventar una respuesta cuando falla la IA o no hay internet | Si el proveedor falla, falta la clave o no hay respuesta en caché sin internet, la decisión es una **abstención explícita con motivo**, nunca un error ni una respuesta inventada | [`decisiones.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/decisiones.py) | `test_error_del_proveedor_es_abstencion_y_no_se_guarda`, `test_t10_offline_sin_cache_se_abstiene_sin_llamar`, `test_jev_sin_clave_se_abstiene_sin_llamar`, `test_estado_con_fechas_no_lanza_excepcion_y_se_guarda` (falló y se corrigió: [#44](https://github.com/Xyryung/faro-editorial/issues/44)) |
| Inventar cifras oficiales (T06) | Si una noticia menciona un dato que no está en el snapshot, no se vincula: queda como **pendiente de verificar** | [`contexto.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/contexto.py) | `test_indicador_sin_datos_queda_pendiente_sin_inventar`, `test_sin_relacion_sustentada_no_se_vincula` |
| Presentar un dato anual histórico como si fuera de hoy (T04) | La cita del Banco Mundial sale de una plantilla con país, año, unidad e ID y siempre dice "dato anual, no una medición actual" | [`contexto.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/contexto.py) | `test_t04_nunca_se_presenta_como_dato_de_hoy` |
| Rellenar datos faltantes | Los nulos se conservan; un año sin dato se declara como limitación, nunca como cero | [`contrato.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/contrato.py), [`contexto.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/contexto.py) | `test_t01_nulos_se_conservan_y_no_se_rellenan_con_cero`, `test_t04_anio_sin_dato_se_declara_y_no_se_rellena` |
| Usar USGS como evidencia de algo que no mide | USGS solo respalda noticias sobre sismos, con la advertencia de que la caja regional no equivale al territorio de Panamá; nunca inundaciones, daños ni pérdidas | [`contexto.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/contexto.py), [`contexto_v1.yaml`](https://github.com/Xyryung/faro-editorial/blob/main/config/contexto_v1.yaml) | `test_usgs_vincula_sismo_cercano_con_limitaciones`, `test_sin_relacion_sustentada_no_se_vincula` |
| Que la prioridad se tome como permiso para publicar (T08) | `habilita_publicacion` está fijo en `false` y el modelo no admite otro valor; cada resultado lleva el aviso "no habilita publicación" | [`puntaje.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/puntaje.py) | `test_t08_prioridad_alta_expone_componentes_y_regla` |
| Confundir relevancia con evidencia suficiente | El estado de evidencia (insuficiente / parcial / suficiente para borrador) se calcula aparte del puntaje: una prioridad alta con una sola fuente queda "insuficiente" | [`puntaje.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/puntaje.py) | `test_prioridad_alta_con_evidencia_insuficiente` |
| Confundir repetición con corroboración (T02) | La evidencia cuenta medios distintos, no notas: tres notas del mismo medio son una procedencia y no suben el puntaje | [`puntaje.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/puntaje.py) | `test_duplicar_noticias_no_infla_el_puntaje` |
| Presentar una noticia vieja como nueva (T03) | La novedad usa también la fecha de detección; la bandeja muestra siempre la fecha original | [`puntaje.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/puntaje.py), [`bandeja.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/bandeja.py) | `test_una_sola_nota_vieja_detectada_hoy_no_es_novedad` (falló y se corrigió: [#42](https://github.com/Xyryung/faro-editorial/issues/42)) |
| Datos alterados o con errores | Verificación SHA-256 contra el manifest; las filas inválidas se separan con su motivo sin detener la carga | [`carga.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/carga.py) | `test_integridad_detecta_archivo_alterado`, `test_t01_rechazos_con_motivo` |
| Filtrar claves de API | Claves solo en `.env` (ignorado por Git) y como `SecretStr`, que no aparece en logs ni en `repr`; los errores registrados no incluyen la clave | [`settings.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/settings.py), [`.gitignore`](https://github.com/Xyryung/faro-editorial/blob/main/.gitignore) | `test_claves_no_aparecen_en_repr`, `test_env_example_sin_claves_reales`, `test_jev_error_http_se_vuelve_abstencion_sin_filtrar_la_clave` |
| Llamadas a la IA sin querer | Por defecto la aplicación funciona sin internet (`OFFLINE=1`) y solo lee la caché | [`settings.py`](https://github.com/Xyryung/faro-editorial/blob/main/src/faro_editorial/settings.py) | `test_por_defecto_funciona_offline` |

Las pruebas T01–T10 y su estado están en la
[matriz de pruebas](https://github.com/Xyryung/faro-editorial/blob/main/evaluacion/matriz_pruebas.md).

## 2. Privacidad

- **Qué se guarda:** titulares, enlaces, medio, fechas y metadatos de noticias públicas;
  indicadores agregados del Banco Mundial; sismos de USGS. No hay datos de clientes, usuarios ni
  personas privadas, y el sistema no crea perfiles de personas.
- **Qué sale del equipo:** con `OFFLINE=0`, el texto de noticias públicas se envía a OpenRouter
  (Jev y LLM) para clasificar y redactar. Es texto de noticias ya publicadas; no se envían
  datos de usuarios ni claves en el contenido.
- **Caché de IA:** cada respuesta guardada conserva el texto que se le envió al modelo, para
  trazabilidad. Si ese texto incluye la descripción del RSS de TVN, la caché no puede
  redistribuirse tal cual (ver derechos). **Pendiente:** definir cómo se entrega la caché para la
  demo sin internet (T10).
- **Reputación:** las acusaciones que aparezcan en una noticia deben atribuirse como
  declaraciones, no como hechos. **Pendiente:** se aplica en los borradores
  ([#15](https://github.com/Xyryung/faro-editorial/issues/15)).

## 3. Derechos por fuente

| Fuente | Condiciones | Cómo las respetamos |
|---|---|---|
| TVN (RSS) | Sin licencia abierta sobre artículos, videos ni imágenes; los extractos solo se reutilizan con autorización del patrocinador (sección 6 del reto) | Se usan titulares y metadatos. La descripción del RSS se usa **solo para análisis interno** ([decisión #35](https://github.com/Xyryung/faro-editorial/issues/35)): no sale en la bandeja ni en el paquete de entrega (`test_no_incluye_la_descripcion_del_rss`, `test_la_descripcion_del_rss_no_se_redistribuye`). Pendiente de confirmar con la organización; si no lo autoriza, se deja de usar sin cambiar el resto del sistema |
| GDELT | La API no transfiere derechos sobre los medios enlazados | Solo titulares, enlaces y fecha de detección |
| Banco Mundial | CC BY 4.0, salvo excepciones por indicador | Cada cita nombra la fuente y el indicador |
| USGS | Datos públicos del gobierno de EE. UU.; elementos de terceros por confirmar | Cada cita lleva el ID del evento, la magnitud y la hora en UTC y en hora de Panamá |

- El **repositorio es público**: ningún dato del snapshot se versiona hasta confirmar las
  condiciones de redistribución ([decisión #28](https://github.com/Xyryung/faro-editorial/issues/28)).
  Los datos se entregan en un paquete aparte, que excluye lo que no se puede redistribuir.
- Las licencias y condiciones de cada fuente están en
  [`config/fuentes_catalogo.yaml`](https://github.com/Xyryung/faro-editorial/blob/main/config/fuentes_catalogo.yaml)
  y en el catálogo de datos.

## 4. Sesgos y límites conocidos

- **Más texto para TVN que para GDELT.** TVN aporta titular y descripción; GDELT solo titular.
  La relevancia (mención de Panamá) y los vínculos con datos oficiales usan ese texto, así que
  pueden favorecer a TVN. Mitigación parcial: el componente de evidencia cuenta procedencias
  independientes, no cantidad de texto.
- **Impacto por tema fijo.** El componente de impacto usa un valor por tema
  ([`criterios_v1.yaml`](https://github.com/Xyryung/faro-editorial/blob/main/config/criterios_v1.yaml)),
  no el contenido de cada noticia. Es una línea base explícita y versionada; la rúbrica de Jev
  puede reemplazarla.
- **Palabras clave.** El vínculo con datos oficiales exige que el titular mencione lo que mide el
  indicador. Se excluyeron palabras ambiguas ("internet", "habitantes") para no forzar
  relaciones; a cambio, puede perder noticias que hablan del tema con otras palabras.
- **Cobertura de medios.** GDELT indexa más algunos medios que otros; el corpus no representa a
  todos los medios panameños por igual.
- **Agencias replicadas.** Hoy se cuentan medios distintos; una misma nota de agencia publicada
  por cinco medios todavía contaría como cinco procedencias. **Pendiente:** la agrupación
  ([#9](https://github.com/Xyryung/faro-editorial/issues/9)) debe tratarlas como una sola.
- **Caja regional de USGS.** Incluye zonas fuera de Panamá; por eso cada vínculo lo advierte.

## 5. Control humano

- Estados de revisión definidos en
  [`rules_v1.yaml`](https://github.com/Xyryung/faro-editorial/blob/main/config/rules_v1.yaml):
  nuevo, en revisión, requiere evidencia, aprobado como borrador y descartado. **Aprobar un
  borrador no significa publicarlo.**
- **Pendiente:** el flujo de revisión en la interfaz
  ([#17](https://github.com/Xyryung/faro-editorial/issues/17)).

## 6. Escenarios fuera de alcance

Por diseño, el sistema **no**:

- Determina si una noticia es verdadera o falsa, ni la culpabilidad de nadie.
- Publica nada ni envía alertas al público.
- Mide rating, audiencia ni conversión publicitaria (el dataset no tiene esos datos).
- Crea perfiles de personas ni listas de supuestos responsables.
- Accede a contenido detrás de paywalls ni reproduce artículos, imágenes o videos completos.
- Produce piezas audiovisuales, clona voces ni se integra con la emisión.
