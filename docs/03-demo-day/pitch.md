# Pitch para exponer · tres minutos

Versión 0.4 · 10 de octubre de 2026. Datos tomados del último corte documentado de navegación visual; no se ejecutaron nuevas pruebas al preparar estas slides.

[Presentación lista para exponer](slides/pitch-exponer-listo.pptx) · [Contenido y notas](slides/pitch-exponer.json) · [Fuentes externas](fuentes-pitch.md).

**Presentar sólo slides 1–7.** Slides 8–9 contienen los diagramas originales completos para preguntas. Los esquemas principales son resúmenes editables; producción se presenta como arquitectura objetivo, no despliegue confirmado.

## Guion oral

| Tiempo | Slide | Qué decir |
|---|---|---|
| 0:00–0:20 | Problema | «Somos Frontier Agent. Consultar, editar o exportar en una app sin API exige recorrer pantallas y verificar el resultado. Queremos delegar ese recorrido, incluso cuando la interfaz sea nueva». |
| 0:20–0:35 | Impacto | «Salesforce reporta un 60% del tiempo de representantes de ventas en actividades distintas de vender y un 42% abrumado por demasiadas herramientas. Es contexto del segmento; no ahorro medido de nuestro agente». |
| 0:35–0:55 | Solución | «Tú defines la tarea y el agente decide el recorrido. Observa, decide, actúa y verifica. Si aparece un bloqueo, reobserva y ajusta el plan; mantiene el alcance autorizado». |
| 0:55–1:20 | Multiagente | «Coordinator divide en hitos; Operator propone acciones; Verifier comprueba evidencia; Recovery trata bloqueos. Son roles dentro del grafo. Sólo un gateway toca el escritorio; runner y watchdog controlan plazo y cierre». |
| 1:20–1:45 | Producción | «La API admite la tarea; PostgreSQL conserva autoridad y outbox; Redis entrega referencias; el worker ejecuta el grafo. Gateway y supervisor controlan el escritorio. Capturas privadas y telemetría segura. Este es el diseño de producción; el despliegue público y las réplicas siguen pendientes». |
| 1:45–2:25 | Resultados | «Después de intentos fallidos, una ejecución real local E3 pasó. La admisión tardó 0,03 segundos y detectamos terminal a los 196,3 segundos. Hubo diez acciones verificadas y cierre confirmado. La suite técnica documentada pasó 340 pruebas. Eso demuestra este recorrido y controles probados; no estabilidad ni todos los casos del reto». |
| 2:25–3:00 | Aprendizajes | «Aprendimos que una acción puede estar verificada y la tarea seguir incompleta. Separamos efecto y completitud, conservamos rutas confirmadas, refrescamos pantalla tras demoras y ligamos el efecto a evidencia posterior. El siguiente gate es E1–E5 con repeticiones y cambios de interfaz. La meta: expresar la tarea y dejar que el agente encuentre el recorrido». |

Total: **20 + 15 + 20 + 25 + 25 + 40 + 35 = 180 segundos**. Incluye cambios de slide. No hay bloque de demo en vivo: presentamos evidencia ya documentada para no depender de una ejecución que por sí sola duró más de tres minutos. Si se muestra una grabación, ajustar el guion dentro del mismo presupuesto e identificarla como tal.

## Evidencia y alcance

Fuente principal: [mejora de navegación visual](../02-ejecucion/mejora-navegacion-visual.md), confirmada también en [planning](../02-ejecucion/specs/planning.md#iteración-visual-y-cierre-e3--2026-10-10).

- **E3 posterior con prompts v2.7: 1/1 PASS.** Es la última ejecución, no la tasa histórica de todos los intentos. Hubo intentos anteriores fallidos.
- **Admisión: 0,03 s. Terminal detectado: 196,3 s.** Polling mide detección; no el instante exacto del cierre físico.
- **Diez acciones verificadas** y sesión cerrada, con transporte/dueño confirmados.
- **340 pruebas técnicas aprobadas** con PostgreSQL/Redis desechables. No equivalen a 340 tareas de negocio ni a pruebas repetidas hoy.
- **Pendientes:** E1–E5 completos, repeticiones, app desconocida, gates de release y despliegue público. Jev está deshabilitado; su aporte no se ha demostrado.

No publicar respuesta de negocio, instrucciones particulares, pantallas privadas, credenciales ni trazas crudas. Los datos anteriores son síntesis sanitizadas de documentos públicos del proyecto.

## Qué falló y qué mejoramos

La [auditoría inicial](../02-ejecucion/auditoria-arquitectura-e3.md) identificó `invalid_reconciliation`: el verificador confirmaba un efecto aplicado pero marcaba la tarea global incompleta; el grafo descartaba los hechos por el estado global y no podía reconciliar.

La mejora separó `effect_status/effect_facts` de la completitud, añadió criterios parciales y verificación final, y persistió efectos con observación posterior de la misma sesión. Además: rutas confirmadas por run, diagnóstico de rechazos al siguiente rol, captura fresca tras inferencia lenta y canonicalización de acciones. Recuperación sin repetir a ciegas lo ya enviado.

Los diagramas originales permanecen intactos. El de producción v0.3 menciona LangSmith; la implementación actual utiliza Langfuse con metadatos seguros y LangSmith apagado. Esa diferencia se señala en la slide de respaldo.
