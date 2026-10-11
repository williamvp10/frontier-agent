# Observabilidad Langfuse — implementación y tablero

Langfuse es el destino de observabilidad del runtime. PostgreSQL conserva la autoridad
de tareas/eventos y `LANGSMITH_TRACING=false` evita exportación automática de contextos.
Esta decisión de ejecución actualiza la observabilidad prevista en el diseño v0.7;
no modifica sus artefactos aprobados.

## Configuración

Completar localmente `.env` con `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` y
`LANGFUSE_BASE_URL=https://us.cloud.langfuse.com`. No versionar las claves.
`LANGFUSE_ENABLED=true` habilita la integración **cuando ambas claves están presentes**;
sin claves, o con `false`, funciona sin exportación. `LANGFUSE_TRACING_ENVIRONMENT`
acepta `development`, `test`, `staging` y `production`.

Settings no carga `.env` automáticamente. Para ejecutar el worker local con el resto
de dependencias configuradas:

```sh
uv sync --frozen --extra dev
uv run --env-file .env python -m app.worker.main
```

En Dokploy, configurar las mismas variables: Compose las entrega únicamente al
worker y usa `production` por defecto. Requiere salida HTTPS a Langfuse. API y
dispatcher no reciben las claves Langfuse. El proceso hijo VNC no inicializa el SDK
ni exporta spans; la instrumentación ocurre en el worker padre.

## Trazas y spans estables

Una traza por tarea reclamada por el worker: ID determinista a partir del UUID
`task_id`. Cada ejecución tiene raíz `execute-task`, con UUID `task_id`/`run_id` y
versión del grafo como metadatos. No usar nombres de clientes, instrucciones ni
destinos de escritorio para nombres o IDs de trazas.

```text
execute-task
├── delegate-agent [agent; role=coordinator/orchestrator/operator/verifier/recovery/analyst]
│   └── generate-decision [generation; modelo, tokens y coste si disponibles]
├── verify-progress [span; grafo context-v2]
│   └── delegate-agent → generate-decision
├── consult-jev [span]
│   └── generate-jev-advice [generation; typesafe/jev-1.13]
├── connect-vnc [tool]
├── observe-vnc [tool]
├── execute-vnc [tool]
└── close-vnc [tool; transport_closed/owner_stopped]
```

Jev sólo produce spans cuando se consulta; habilitar trazabilidad no activa Jev.
Todos los modelos continúan accediéndose exclusivamente vía OpenRouter.
Los spans VNC miden la operación desde el worker, incluyendo validaciones/IPC y,
según la operación, persistencia de evidencia; no representan sólo latencia de red.

## Métricas y significado

Scores al concluir el runner, asociados a la raíz y su traza:

| Score | Unidad / semántica |
|---|---|
| `task_duration_seconds` | Desde admisión hasta salida del runner; incluye cola y cierre/persistencia |
| `queue_wait_seconds` | Desde admisión hasta entrada al runner; no incluye espera posterior por destino |
| `run_duration_seconds` | Tiempo monotónico del runner |
| `agent_calls` | Intentos de delegación; rol disponible en spans |
| `model_calls`, `jev_calls` | Invocaciones intentadas a adaptadores visual/Jev; no se inventa uso de tokens si fallan |
| `vnc_calls` | Intentos de conectar, observar, enviar acción tras intención durable y cerrar |
| `action_attempts` | Entradas al ejecutor, incluidas propuestas rechazadas antes del envío |
| `no_progress_events`, `no_progress_max_streak` | Ciclos de verificación sin avance confirmado y mayor racha consecutiva |
| `recovery_escalations`, `jev_escalations` | Entradas a recuperación/consulta Jev, incluso si un presupuesto las rechaza |
| `repeated_frames` | Capturas consecutivas idénticas; nunca exporta hashes ni imágenes |
| `task_success` | Booleano 0/1; 1 sólo tras retorno con éxito del runner, después de cierre y terminal durable |
| `terminal_state` | `terminada`, `fallida` o `unconfirmed` si el runner no confirmó cierre/persistencia |

En `context-v2`, progreso significa un aumento de criterios satisfechos o transiciones
útiles durante verificación de efecto/completitud/reconciliación. En v1, significa
verificación final con hechos verificados. Prevalidar un clic no cuenta como progreso.
El contador reportado por el contexto se exporta sólo como número; la señal observada
se calcula aparte. No cambia la política de acciones, los presupuestos ni el deadline.

Cada generación conserva `usage_details` de entrada/salida y coste explícito si lo
devuelve OpenRouter. Uso ausente permanece desconocido. Errores exportan estado
controlado, nunca mensajes de excepción. El motivo terminal se limita a códigos
conocidos; cualquier otro motivo se representa como `other`.

## Tablero reproducible

`scripts/setup_langfuse.py` usa **Langfuse CLI 1.2.4**, con credenciales sólo en entorno
del subproceso. Las respuestas se procesan sin imprimir datos del proyecto.

```sh
uv run python scripts/setup_langfuse.py --env-file .env             # plan sin llamadas remotas
uv run python scripts/setup_langfuse.py --env-file .env --smoke     # traza sintética, entorno test
uv run python scripts/setup_langfuse.py --env-file .env --apply     # crear/reusar tablero
```

Abrir **Dashboards → Frontier - observabilidad v1**. Hay 13 widgets: tiempo total P95,
espera de cola, llamadas visual/Jev/VNC por tarea, falta de progreso, racha P95,
escalamientos a recuperación/Jev, cantidad de tareas, tasa de éxito cerrado, latencia
P95 por operación y coste por modelo. Seleccionar entorno y rango temporal antes de
interpretarlos. El smoke está en `test`; las tareas reales usan el entorno del worker.
La tasa de éxito se presenta en escala 0–1, no como porcentaje 0–100.

El aprovisionamiento reutiliza los nombres versionados y placements deterministas;
una segunda ejecución no duplica tiles. No sobreescribe widgets existentes editados
por el usuario. Reintenta de forma acotada HTTP 429. Las APIs de dashboards son
`unstable`: al cambiar su contrato, actualizar el script tras consultar su esquema.

## Privacidad y fallos de exportación

`app/core/observability.py` aplica lista permitida: UUIDs internos, números finitos,
booleanos y estados/roles/códigos enumerados. Modelos se limitan a identificadores
públicos. Se excluyen instrucciones, prompts, SharedTaskContext, hechos de negocio,
resúmenes, decisiones completas, textos/teclas/coordenadas de acción, endpoints,
credenciales, rutas de artefactos, screenshots/base64 y chain-of-thought.

Observaciones manuales y contexto local por tarea mantienen la jerarquía sin activar
callbacks LangChain/LangGraph ni captura de argumentos, retornos o excepciones OTel.
El SDK usa un TracerProvider separado y exporta sólo su scope explícito. Los contadores
de tareas concurrentes están aislados y una respuesta tardía no cambia los totales finales.

La exportación es best effort: un fallo de Langfuse no altera acciones, cierre ni
resultado. El shutdown/flush se ejecuta en un thread **después** de cerrar gateways;
no se espera una llamada de red Langfuse antes de publicar el terminal. Una muerte
abrupta puede perder spans/scores bufferizados. Las tareas que expiran en cola antes
de ser reclamadas no tienen traza de ejecución: consultar PostgreSQL para ese caso.

## Evidencia real de esta integración

- SDK Python **4.17.0** fijado en `uv.lock`.
- `--smoke`: ingestión remota confirmada de **4 observaciones sintéticas**; sin inferencia
  facturada ni conexión a un escritorio real.
- `--apply`: tablero remoto confirmado con **13 widgets**, repetición idempotente verificada.
- `uv run --extra dev pytest tests/unit/test_observability.py -q`: **7 passed**.
  Prueba SDK real con exporter en memoria, jerarquía, tokens, canarios, excepción,
  cancelación, aislamiento, respuesta tardía, fallo de exportador y cierre no confirmado.
- Suite `uv run --extra dev pytest -m 'not e2e and not release' -q`: **229 passed,
  27 skipped** en esta revisión; servicios de pruebas PostgreSQL/Redis no configurados.
  Ruff y scan público pasan (131 archivos, sin coincidencias privadas detectadas).
- Esto no constituye evidencia de E1–E5 ni de despliegue/observabilidad de tareas reales.

Referencias: [SDK](https://langfuse.com/docs/observability/sdk/instrumentation),
[scores](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk),
[dashboards](https://langfuse.com/docs/metrics/features/custom-dashboards).
