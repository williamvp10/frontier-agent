# Plan: evaluador de práctica por CLI, progreso y métricas

Fecha: 10 de octubre de 2026. Estado: implementación CLI/harness entregada con revisión estática;
validación dinámica a cargo de otro agente. Por instrucción del usuario no se ejecutaron tests,
CLI, reset/check, tareas ni inferencias para esta entrega. Ver [uso y límites implementados](../evaluaciones.md).

## 1. Objetivo y estado actual

Evolucionar `scripts/evaluate.py` a un evaluador local que reproduzca el protocolo del organizador sobre el escritorio de práctica: seleccionar uno o varios casos, resetear, enviar tarea, consultar estado, verificar y mostrar el resultado. Usa servicios reales en ejecución; no simula el agente ni modifica la app. Mock HTTP queda reservado a las pruebas unitarias del evaluador.

Punto de partida del plan:

- Harness con `__reset`, `POST /tasks`, polling cada cinco segundos y `__check`.
- Carga de fixtures desde `../frontier-reto-1/pruebas`, envío al agente solo de instrucción/escritorio, tres repeticiones por defecto.
- Reporte con `case_id`, `passed`, checks, `acceptance_seconds`, `duration_seconds` y error_code.
- Detención de suite ante aceptación incierta o ausencia de terminal; reportes privados.
- Pruebas unitarias del harness: 15 pasaron en la revisión anterior. Esto no prueba capacidad visual ni tiempos reales E1–E5.
- Observabilidad con task_id/run_id, tiempos, llamadas, tokens y coste cuando viene del proveedor; el código actual genera trace_id con seed task_id.

Se añadieron selección por ID, eventos en vivo, correlación task→traza offline, métricas
diferenciadas, reportes incrementales y journal persistente con lock local/preflight.
Readiness visual y evidencia independiente de cierre siguen limitadas por el contrato público;
el score remoto `evaluation_pass` se propone al propietario de Langfuse, no se exporta aquí.
No hay resultados E1–E5 acreditados por esta entrega ni nuevos resultados de tests.

## 2. Alcance y propiedad de archivos

Responsable nuevo: evaluador/harness, tests del harness y documentación de uso. Archivos principales: `scripts/evaluate.py`, `tests/unit/test_harness.py`; puede añadir un módulo auxiliar y tests si evita sobrecargar el script.

No tocar grafo, modelos, configuración de modelos, worker, API, DB, lockfile ni módulo Langfuse sin coordinar con sus propietarios. No crear tareas/chats o enviar mensajes a otros agentes desde este encargo. Este documento es el handoff copiable.

Mantener el contrato público existente. No agregar campos obligatorios de evaluación a POST /tasks ni expectativas al contexto del agente. No ejecutar pruebas destructivas mientras el escritorio tenga otro operador activo.

## 3. CLI implementada

Ejemplos de interfaz con endpoint ficticio público. No ejecutados en esta entrega.
`--exclusive-desktop` declara una reserva coordinada externamente; no la adquiere por sí solo.

```sh
# Listar casos; no red ni lectura de credenciales.
.venv/bin/python scripts/evaluate.py --list

# Plan de selección; sin operar el escritorio.
.venv/bin/python scripts/evaluate.py --cases E1,E3 --repetitions 1 --dry-run

# Un caso real; requiere escritorio exclusivo y servicio listo.
.venv/bin/python scripts/evaluate.py --entrypoint https://agent.example --cases E3 --repetitions 1 --execute --exclusive-desktop

# Varios casos, en serie sobre el mismo escritorio.
.venv/bin/python scripts/evaluate.py --entrypoint https://agent.example --cases E1,E2,E5 --repetitions 1 --execute --exclusive-desktop

# Suite completa: cinco casos por tres repeticiones.
.venv/bin/python scripts/evaluate.py --entrypoint https://agent.example --all --repetitions 3 --execute --exclusive-desktop
```

Requisitos: `--list` y `--dry-run` no requieren entrypoint/env; `--cases` valida IDs antes del reset; `--all` y `--cases` son excluyentes. Rechazar selección vacía, duplicados y repeticiones inválidas. No lanzar toda la suite por un error de selección. Para ejecución real exigir selección explícita y `--execute`; documentar el cambio respecto al comportamiento actual.

Mantener `--fixtures`, `--env-file` y reporte dentro de `.private/`. La ejecución normal muestra instrucciones/resúmenes solo con una opción privada explícita; por defecto imprimir identificadores, estado y métricas seguros. Evitar pasar secretos como argumentos shell.

## 4. Ciclo por caso

1. Validar configuración y TODA la selección antes de realizar llamadas destructivas.
2. Confirmar servicio listo, ownership exclusivo del escritorio y ausencia de tarea previa pendiente. El estado inicial `safe_to_reset=True` no demuestra exclusividad.
3. Obtener lock local por escritorio sin almacenar endpoint en nombre/output; avisar que ese lock no impide clientes externos. Coordinar reserva con integrador antes de ejecuciones reales. No usar reset como prueba de exclusividad.
4. Emitir reset_started; llamar `__reset`, comprobar respuesta y disponibilidad posterior con deadline propio acotado. `/health` comprueba servicio remoto, no necesariamente UI lista: esperar readiness visual mediante el runtime o un mecanismo confirmado; si no hay señal, documentar espera acotada y limitación.
5. Enviar instrucción literal + escritorio a POST /tasks, medir aceptación y guardar task_id inmediatamente. Una pérdida de respuesta puede significar aceptación; detener y conservar estado incierto.
6. Poll cada cinco segundos sin exceder deadline original. Mostrar estado y tiempo aunque continúe en_curso. No inventar progreso de hitos que GET no expone.
7. Al terminal, registrar resultado declarado, validar estado/resumen y cierre mediante contrato/evidencia disponible.
8. Llamar `__check` con fixture completo; conservar nombres/checks seguros y compararlos con los oráculos locales.
9. Emitir resultado de prueba y correlación Langfuse; guardar reporte antes de iniciar otro caso.
10. Continuar solo si el cierre está suficientemente confirmado. Fallo funcional con cierre confirmado permite siguiente caso; timeout, cierre incierto o POST incierto bloquean la suite.

Ctrl-C/SIGTERM: detener nuevas admisiones y resets, escribir reporte parcial. Cerrar el cliente HTTP no cancela la tarea remota. No inventar endpoint de cancelación; marcar tarea pendiente y dejar referencia para reconciliar. Una ejecución posterior debe detectar bloqueo persistido por escritorio y no resetear a ciegas.

## 5. Progreso en terminal

Ejemplo sintético:

```text
[E3 1/1] Preparando escritorio
[E3 1/1] Aceptada en 0.32 s | task_id=<uuid>
[E3 1/1] en_curso | 5.3 s
[E3 1/1] en_curso | 10.3 s
[E3 1/1] terminada | detectada a los 15.3 s
[E3 1/1] PASS | estado OK | respuesta OK | app OK | aceptación OK
[E3 1/1] Langfuse: <enlace de traza cuando esté disponible>
Suite: 1/1 PASS | 0 FAIL | 0 ERROR | 0 SKIPPED
```

E5 puede mostrar `fallida` y PASS: abstenerse ante homónimos es el resultado esperado. Mantener cuatro resultados del evaluador: `PASS` (todos los oráculos), `FAIL` (incumplimiento), `ERROR` (no se pudo evaluar por infraestructura/protocolo) y `SKIPPED` (no ejecutado por bloqueo anterior). Ninguno equivale automáticamente al estado público de la tarea.

Logs de progreso seguros en stdout; eventos estructurados opcionales como JSONL privado. Nunca mostrar tokens, endpoints privados, errores HTTP crudos ni valores de negocio por defecto. stdout no es un canal seguro solo porque el reporte esté ignorado.

## 6. Métricas y semántica

| Métrica | Fuente | Significado |
|---|---|---|
| reset_duration_seconds | CLI, monotonic | Tiempo de petición reset |
| desktop_ready_wait_seconds | CLI | Espera posterior a reset |
| acceptance_seconds | CLI | Duración POST /tasks; requisito <2 s |
| terminal_detected_seconds | CLI | Desde envío POST hasta recibir terminal; incluye polling/red |
| check_duration_seconds | CLI | Duración __check |
| case_wall_seconds | CLI | Ciclo completo, incluido reset y check |
| suite_wall_seconds | CLI | Duración total de la suite |
| queue_wait_seconds | Runner/Langfuse | Espera durable antes de ejecutar |
| task_duration_seconds / run_duration_seconds | Runner/Langfuse | Duraciones del backend según su reloj |
| closure_confirmed / actions_after_close | Runner/gateway | Evidencia de cierre y actividad tardía |
| llamadas/tokens/coste | Langfuse/OpenRouter | Consumo del run, incluidos Jev y reintentos registrados |

La duración del CLI es una detección, no el instante exacto de cierre. Con polling de cinco segundos habrá retraso de muestreo/red. No usar terminal_detected_seconds como prueba exacta del objetivo +285 s ni como duración de inferencia. Preservar `duration_seconds` anterior como alias documentado si hace falta compatibilidad.

Registrar por caso: suite_id, case_id, repetition, task_id nullable, run_id si se obtiene, trace_id/url nullable, timestamps UTC, resultado público, resultado evaluador, métricas, checks desglosados y errores tipados. No guardar instrucción, fixture completo o resumen crudo en reporte normal; evidencia sensible adicional solo privada y explícita.

Agregados: cuentas PASS/FAIL/ERROR/SKIPPED, total planificado/ejecutado, duración por caso y repetición, mediana/máximo; p95 solo con número de muestras y método declarado, sin venderlo como estable con tres muestras. Los agregados deben incluir fallos/timeouts y distinguir métricas faltantes; no ocultarlos para mejorar promedio.

## 7. Langfuse: correlación y USD

Reutilizar la integración del otro agente. No crear una segunda traza de tarea desde el harness ni sumar su coste al backend.

- task_id enlaza cada caso con la traza real. Actualmente Observability usa `create_trace_id(seed=str(task_id))`; coordinar un resolver compatible con esa política y entorno/proyecto configurado.
- No hardcodear un hostname privado ni asumir una forma de URL sin validar SDK/documentación vigente. Construir enlace solo con configuración local; si no está disponible, imprimir task_id como referencia.
- Coste oficial visible en Langfuse: generaciones OpenRouter y Jev con usage/cost cuando venga del proveedor. Falta de coste significa desconocido, nunca cero.
- Telemetría puede llegar después del terminal. Mostrar enlace aunque la traza aún no aparezca y distinguir pending/missing; no bloquear siguiente caso o cierre por ingestión.
- No duplicar coste registrándolo en root y volviéndolo a sumar junto con generaciones. Definir agregación que cuente cada llamada facturada una sola vez.
- La puntuación existente `task_success = estado == terminada` no mide aprobación del evaluador. E5 correcto tiene estado fallida. Coordinar con propietario Langfuse un score separado `evaluation_pass`, y opcionalmente checks/IDs seguros, sin reescribir la instrumentación desde este paquete.
- No es requisito de primera entrega descargar gasto a terminal: el usuario lo consulta en Langfuse. Un enriquecimiento posterior puede obtenerlo asíncronamente con acceso autorizado y estado `reported|estimated|pending|unavailable`.

## 8. Oráculos y límites

`__check` verifica estado de aplicación; no comprueba por sí solo resumen, plazo o cierre de socket. Validar estos aspectos separadamente. Mantener las expectativas exclusivamente en harness, nunca enviarlas al agente.

Comprobar estado esperado, claves de razón, formato/orden/valor de respuesta según reglas existentes y fixture. Incluir caso de fallo con respuesta inventada: no debe pasar porque respuesta_esperada sea null. Preservar diferencias entre regla del organizador explícita y convención de nuestra spec; no atribuir al README detalles que no contiene.

Comprobar estructura de __check, `pass` y checks individuales; respuestas vacías/inconsistentes son error o fallo documentado, no éxito. Los nombres retornados son datos no confiables: acotar/sanitizar antes de output.

## 9. Pruebas de aceptación del desarrollo

HTTP simulado y reloj fake; no necesitan credenciales ni escritorio real:

- Selección de uno/varios/todos, orden estable, IDs inválidos y dry-run sin red.
- Progreso por polling y terminal; E5 fallida→PASS, terminada incorrecta→FAIL.
- Aislamiento de tokens y payload: expectativas llegan solo a __check.
- Medición independiente de aceptación, detección, reset/check y total; latency de polling no prueba cierre.
- JSONL/report incremental, fallo antes/después de aceptación, Ctrl-C y recuperación de bloqueo entre invocaciones.
- Suite abortada sin reset adicional tras incertidumbre; casos restantes SKIPPED.
- Correlación task_id/trace y telemetría retrasada sin bloquear flujo.
- Coste ausente no cero; no duplicados; score de evaluación distinto de terminal_state.
- Canarios ausentes de stdout/report/errores; redirects no reenvían credenciales.

Después de tests, smoke real con E3 y una repetición, coordinando exclusividad. Revisar resultado y cierre antes de E1–E5 x3. No correr sesiones concurrentes en el mismo escritorio. El desarrollo termina con comandos reproducibles, reporte sanitizado de evidencia y límites explícitos, no solo mocks pasando.

## 10. Encargo listo para copiar

El encargo siguiente se conserva como referencia del plan original. El código ya fue
desarrollado en este corte: el responsable de pruebas debe validar/adaptar sus mocks y
relojes contra la interfaz entregada, sin recrear el evaluador. Esta entrega no ejecutó
la secuencia de tests/smoke descrita abajo.

> Implementa el plan de docs/02-ejecucion/specs/evaluador-practica-cli.md sobre scripts/evaluate.py y tests/unit/test_harness.py existentes. Lee AGENTS.md, el README original y fixtures solo como harness, specs/pruebas.md y observabilidad-langfuse.md. Añade selección por IDs, all/list/dry-run, progreso en vivo, métricas con semántica precisa, task_id y correlación Langfuse, reportes incrementales privados y detención persistente ante operaciones inciertas. Conserva API pública y aislamiento de expectativas; Jev y modelos siguen por OpenRouter. No modifiques Langfuse, grafo, API, worker, config o lockfile sin coordinar. Propón al responsable de Langfuse el resolver de enlace y evaluation_pass separado, evitando doble conteo de USD. Primero ejecuta tests HTTP simulados/reloj fake, Ruff y scan público. No leas/imprimas secretos ni operes el escritorio hasta confirmar reserva exclusiva y servicio listo dentro del contexto autorizado. Documenta comandos reales, resultados y límites; no declares E1–E5 aprobados sin ejecución real. No publiques ni despliegues.

Orden sugerido: selección/progreso → reporte/métricas → incertidumbre entre invocaciones/readiness → correlación coordinada Langfuse → smoke real → suite completa.
