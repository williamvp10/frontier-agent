# Debrief técnico base

Versión 0.2 · 10 de octubre de 2026 · Corte E3 incorporado; evaluación completa e incidente pendientes.

Profundidad técnica: 20:00–21:00, según la agenda compartida. El repositorio prevé un debrief individual de diez minutos; confirmar el slot asignado. Cada integrante prepara su explicación y debe poder recorrer las partes clave del sistema.


## Corte disponible para la exposición

La [mejora de navegación visual](../02-ejecucion/mejora-navegacion-visual.md) documenta **340 pruebas técnicas aprobadas** y **E3 posterior 1/1 PASS** con prompts v2.7: admisión 0,03 s, terminal detectado a 196,3 s, diez acciones verificadas y transporte/dueño cerrados. Es una ejecución posterior a intentos fallidos, no estabilidad ni cobertura E1–E5; no se repitió en esta preparación.

Aprendizaje confirmado: `invalid_reconciliation` mezclaba efecto de acción y completitud global. Se separaron `effect_status/effect_facts`, criterios parciales y verificación de cierre. Efectos ligados a observación posterior del mismo run; rutas confirmadas y captura fresca tras planificación lenta. La [auditoría](../02-ejecucion/auditoria-arquitectura-e3.md) conserva el fallo inicial; el documento de mejora conserva el cambio posterior. No atribuirlo al incidente del organizador.

## Recorrido de diez minutos

| Tiempo | Contenido | Evidencia a tener preparada |
|---|---|---|
| 0:00–1:00 | Objetivo, restricciones y versión evaluada | Contrato público; tarea y escritorio recibidos |
| 1:00–3:00 | Recorrido de una tarea y responsabilidades | API → DB/outbox → Redis → worker/grafo → gateway → observación → verificación → cierre |
| 3:00–5:00 | Decisiones y alternativas | Explicar dos decisiones con código, beneficio y coste |
| 5:00–7:30 | Lectura del set de evaluaciones | Tabla final y un fallo explicado desde su evidencia |
| 7:30–9:00 | Incidente y límites | Antes/después, causa, cambio y regresión; pendiente si no ocurrió |
| 9:00–10:00 | Qué haría después | Prioridad sustentada en el fallo; conclusión pendiente |

## Decisiones: cómo funciona y por qué

Esta base se apoya en la [integración v2 documentada](../02-ejecucion/integracion-openrouter-v2.md). Revalidar implementación y configuración al cierre: el diseño aprobado y las notas históricas pueden diferir del código final.

| Decisión | Mecanismo y ubicación | Motivo | Coste / límite y alternativa |
|---|---|---|---|
| VNC directo | `app/desktop/transport.py`, `gateway.py`, `executor.py`: captura y eventos de teclado/ratón | Cumplir pantalla e interacción sobre apps sin API | Depende de percepción, latencia y repintado. DOM/API no son alternativas permitidas en este reto |
| Roles en LangGraph | `app/agents/graph_v2.py`, `runtime.py`, `context_service.py`: hitos, propuestas, verificación y recuperación | Separar propuesta, evidencia y autoridad de ejecución | Más inferencias y coordinación. Comparar con un único agente bajo las mismas condiciones queda pendiente |
| Un único ejecutor | Gateway valida sesión, epoch, observación, alcance y presupuesto | Evitar eventos concurrentes y propuestas viejas | Ownership incierto bloquea progreso. Réplicas de worker requieren coordinación probada |
| DB durable + outbox + Redis | `app/persistence/`, `app/queue/`, `app/worker/runner.py` | Confirmar admisión durable y tolerar redelivery sin doble claim | Redis no es fuente de verdad; dos POST iguales no son automáticamente idempotentes |
| Contexto por rol | `context_service.py`, `app/core/graph_memory.py`: slices, patches y procedencia | Acotar permisos y conservar referencias a evidencia | Checkpoint no vuelve idempotente un clic; resume automático no está resuelto |
| Modelos por rol mediante OpenRouter | `app/models/profiles.py`, `factory.py`, `openrouter_visual.py` | Fijar modelo y contrato estructurado por responsabilidad | Calidad, schema, latencia y coste deben medirse. Smoke sintético no demuestra capacidad de negocio |
| Plazo y cierre independiente | `app/worker/runner.py`, `app/desktop/gateway.py` | Un modelo bloqueado no debe seguir controlando el escritorio | 300 s totales desde recepción; explicar drenaje +268 s, corte +270 s y objetivo terminal antes de +285 s. Son reglas/objetivos; exigir medición para afirmar cumplimiento |

Jev es consejo opcional y no tiene autoridad para ejecutar; su habilitación y contribución al resultado final quedan por confirmar. Langfuse registra metadatos seguros; no sustituye los eventos ni la autoridad de PostgreSQL. No afirmar que una funcionalidad está activa sólo por tener código o spec.

## Lectura de evaluaciones

Completar desde el reporte final del [harness](../02-ejecucion/evaluaciones.md), conservando un resumen sanitizado. Expectativas y chequeos permanecen fuera del contexto del agente.

| Caso | Capacidad | Repeticiones ejecutadas / planificadas | PASS | FAIL | ERROR | SKIPPED | Evidencia y causa |
|---|---|---|---|---|---|---|---|
| E1 | Crear registro | Pendiente | — | — | — | — | Pendiente |
| E2 | Editar sólo el campo solicitado | Pendiente | — | — | — | — | Pendiente |
| E3 | Consultar sin modificar | 1/1, último intento | 1 | 0 | 0 | 0 | 196,3 s hasta detección terminal; diez acciones verificadas; cierre confirmado |
| E4 | Filtrar y exportar | Pendiente | — | — | — | — | Pendiente |
| E5 | Resolver ambigüedad o abstenerse | Pendiente | — | — | — | — | Pendiente |
| Variación / incidente | Adaptación de interfaz | Pendiente | — | — | — | — | Pendiente |

La meta de tres repeticiones por caso es un plan, no un resultado. «—» significa sin medición; no significa cero.

- **PASS**: todos los oráculos pasan. **FAIL**: evaluación realizada con algún oráculo fallido. **ERROR**: infraestructura/protocolo impidió evaluar. **SKIPPED**: planificado, no iniciado.
- El estado público `terminada/fallida` es distinto del resultado del evaluador: una abstención segura por homónimos puede ser `fallida` y PASS. Un falso éxito puede ser `terminada` y FAIL.
- Publicar cuentas completas y denominador. Si se usa PASS/(PASS+FAIL), etiquetarlo como tasa entre casos evaluables y mostrar ERROR/SKIPPED aparte; no ocultarlos ni sustituirlos por éxitos.
- Distinguir latencia de admisión POST, tiempo hasta detectar terminal por polling y duración real del run. Polling cada cinco segundos no prueba el instante exacto del cierre.
- `closure_basis=api_terminal_contract` no es observación independiente del wire RFB. Sin evidencia adicional, cierre confirmado y acciones posteriores quedan desconocidos.
- Coste y tokens: declarar muestra, roles y fuente. No extrapolar coste de un smoke sintético a tareas de negocio. No presentar p95 con una muestra insuficiente sin advertir su tamaño.

## Ficha de diagnóstico por fallo

Copiar una ficha por fallo observado; no rellenar causas por intuición.

| Campo | Registro al cierre |
|---|---|
| Caso, repetición, versión | Pendiente |
| Instrucción y alcance | Síntesis sanitizada pendiente |
| Síntoma observado / oráculo fallido | Pendiente |
| Última observación y acción propuesta | Referencias locales privadas; no pegar pantalla o traza cruda |
| Capa implicada | Percepción / contexto / planificación / ejecución / verificación / transporte / harness |
| Causa | Pendiente; marcar confirmada o hipótesis |
| Evidencia que sostiene la causa | Pendiente |
| Cómo descartar otra causa | Pendiente |
| Cambio realizado | Pendiente |
| Regresión y resultado posterior | Pendiente |
| Límite que permanece | Pendiente |

Ejemplo de explicación, **hipotético**: una propuesta caduca durante la inferencia; el gateway la rechaza. Confirmarlo exige comparar captura, expiración y rechazo. Reobservar puede ser la corrección; ampliar el TTL sin medir puede admitir acciones sobre una pantalla vieja. No registrar este ejemplo como fallo ocurrido.

Hay un antecedente real documentado: un proveedor rechazó el schema amplio del verificador; se separó `VerifierPatch` sin relajar strict. Usarlo sólo con su contexto y evidencia en integración v2; no atribuirlo al incidente del organizador ni a la evaluación final.

## Incidente y próximos pasos

- Cambio recibido del organizador: **pendiente**.
- Reproducción, observación y efecto: **pendientes**.
- Causa confirmada y corrección general: **pendientes**.
- Regresión anterior/posterior y pruebas de cierre: **pendientes**.
- Limitación restante y prioridad siguiente: **pendientes**.
- Conclusión final: **pendiente de terminar el reto**.

Límites ya documentados, por revisar al cierre: E1–E5 completos y app desconocida sin aceptación demostrada en esta base; recuperación tras crash y reconciliación externa pendientes; réplicas de worker y GC/retención sin gate final; despliegue público sin confirmación en el corte consultado. Superar cinco casos de práctica no demuestra generalidad universal.

## Preparación individual

Cada integrante completa: **nombre**, **parte implementada**, **decisión que puede defender**, **fallo que puede explicar** y **mejora prioritaria**. Los nombres y reparto quedan pendientes.

Ensayar respuestas con referencia al código: ¿quién puede emitir un clic?, ¿qué ocurre si DB falla antes de actuar?, ¿qué hacemos si el guardado pudo ocurrir pero perdimos la respuesta?, ¿qué invalida un cambio de pantalla?, ¿por qué checkpoint no autoriza replay?, ¿cómo distinguimos resultado del modelo, estado de la app y oráculo?, ¿qué explica cada FAIL/ERROR?, ¿qué no podemos concluir todavía?

Al terminar el reto, actualizar primero las fichas y la matriz, después el pitch. Cada afirmación final debe vincular una decisión con una evidencia o un límite concreto.
