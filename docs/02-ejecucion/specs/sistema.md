# Spec del servicio y agente

Las palabras DEBE y NO DEBE describen condiciones de aceptación. API/DB/cola/grafo/gateway están integrados y probados por capas; despliegue y E1–E5 reales pendientes. La evolución por hitos/contexto/Jev se describe en multiagente-contexto-jev.md y está conectada como `context-v2`; evidencia y límites en `../integracion-openrouter-v2.md`.

## Alcance

Operar una app desconocida por imágenes y eventos RFB sobre WSS. No usar DOM, red interna de la app, rutas de negocio, coordenadas fijas de SIGECOM ni respuestas de fixtures. No alojamos el escritorio de evaluación: viene en cada petición. La solución debe funcionar con otra disposición visual, nombres y datos mediante observación y replanteamiento.

El patrón deep agent exige planificación, memoria y delegación real a operador, verificador y recuperador mediante LangGraph/LangChain. No se requiere el paquete `deepagents`; no usar un agente hospedado que resuelva el reto por nosotros. Todos los modelos usan exclusivamente OpenRouter, incluido Jev Decisions; no hay fallback directo al fabricante. Jev es un consejero separado sin autoridad de ejecución. Fijar perfil permitido y probar imagen/schema por endpoint. Prompts, objetivos normalizados, instrucciones de rol y preguntas a Jev en inglés; conservar la instrucción original, valores y etiquetas literales. El contrato público no cambia de idioma.

## Contrato público

Contrato validado contra README local: ver [matriz y límites](contrato-entrypoint.md). Nuevos roles, memoria o Jev no agregan campos ni endpoints obligatorios.

Todas las rutas requieren el token de equipo, comparación segura y errores sin reflejar headers/credenciales. El token es configuración de servidor, nunca dato del grafo.

| Ruta | Respuesta normal | Error definido por nuestro servicio |
|---|---|---|
| GET /health | 200, `{"ok":true}` si DB, Redis y heartbeat reciente de worker/gateway/supervisor están listos | 503 si no listo |
| POST /tasks | 200, `{"task_id":"<uuid>"}` tras commit durable en menos de 2 s | 422 entrada inválida; 503 no admisible/DB indisponible |
| GET /tasks/{uuid} | 200, `{"estado":"en_curso|terminada|fallida","resumen":"..."}` | 404 inexistente/no perteneciente al equipo; 422 UUID inválido |
| Cualquier ruta | — | 401 sin token o token incorrecto |

POST recibe exactamente `tarea: str` y `escritorio: {url: str, password: str}`. URL WSS absoluta, sin credenciales embebidas; usar la ruta recibida, sin reemplazarla por la de práctica. Normalizar host/puerto/ruta para exclusión por destino. Límites iniciales configurables: instrucción 16 KiB, password 4 KiB y body 32 KiB; confirmar compatibilidad antes del release. No prohibir hosts nuevos de evaluación; bloquear loopback/link-local/red interna del servicio salvo configuración explícita de desarrollo. Probar resolución y conexiones, no solo validación textual.

`team_id` se deriva de la configuración asociada al bearer (un equipo en demo), nunca del body. No añadir campos obligatorios, endpoints reset/check del escritorio ni esperar conexión/modelo dentro de POST. No garantizar idempotencia de dos POST iguales: el contrato no trae clave idempotente. Sí impedir doble ejecución de una task_id por redelivery.

Readiness no llama al modelo ni mantiene una conexión al escritorio. Worker/gateway/supervisor reportan heartbeat cada 2 s; ventana inicial 10 s, configurable. En admisión degradada, devolver 503 antes de crear tarea; una caída posterior no pierde tareas ya aceptadas.

## Tiempo, estados y cierre

`accepted_at` se toma al entrar a POST autenticado, antes de DB/cola. Deadline absoluto = accepted_at + 300 s; bloqueo de nuevas acciones = +270 s; objetivo de terminal cerrado antes de +285 s. Cola y espera por escritorio consumen el mismo plazo. Combinar tiempo UTC persistido con reloj monotónico local; nunca recalcular un plazo completo al reiniciar.

Estados internos: queued → running → closing → succeeded/failed. queued también puede pasar a closing por cancelación/plazo. Solo succeeded proyecta terminada; failed proyecta fallida; los demás en_curso. Terminal no regresa a running. Resumen provisional no asegura éxito.

1. Reclamar tarea atómicamente y verificar plazo y sesión.
2. Conectar y observar; hacer trabajo serializado por escritorio.
3. Para terminar: revocar capacidad de actuar, invalidar decisiones en vuelo, cancelar modelo/subagentes, liberar teclas/botones mientras aún es seguro y cerrar RFB/WSS/puente.
4. Confirmar cierre y persistir terminal. El supervisor puede terminar el proceso dueño del socket si no responde; espera salida confirmada, no solo expirar lease.
5. Si DB cae después del cierre: NO actuar; reintentar solo persistencia del terminal. GET puede seguir en_curso hasta recuperación; no prometer disponibilidad durante caída de DB.

Programar drenaje preventivo a +268 s; no iniciar una tool cuya duración máxima supere el margen hasta +270 s. Liberaciones de botones/modificadores ocurren antes del cutoff. A partir del cutoff, cerrar transporte/proceso sin emitir más eventos RFB, incluso si un subagente termina tarde. Probarlo en el wire log, no solo en flags internos.

Supervisor y dueño VNC deben ejecutarse fuera del proceso que espera al modelo. Demo: proceso gateway por ejecución y supervisor de procesos, gestionados por el servicio worker; el grafo no conserva sockets. Lease en DB coordina ownership; la exclusión efectiva la mantiene el único gateway. Una sesión uncertain no se reasigna automáticamente. Reinicio requiere confirmar que el dueño anterior murió y el canal cerró; en otro host se bloquea hasta confirmar aislamiento. No afirmar exactly-once de clics.

## Interfaces compartidas (P00)

Modelos Pydantic, sin campos libres para secretos:

- `Observation`: id, run_id, session_id, epoch, frame_revision, captured_at, width, height, artifact_id, sha256. Imagen binaria se carga transitoriamente por referencia.
- `ActionProposal`: id, run_id, session_id, epoch, observation_id, kind, args tipados, expected_effect, scope_reason, risk (`navigation|edit|commit|export`), expires_at.
- `Verification`: `verified|incomplete|ambiguous|unverifiable`, evidence_ids, facts, unresolved_identifiers; no razonamiento privado.
- `RecoveryDecision`: nueva observación/estrategia acotada/abstención; nunca reenviar automáticamente una acción uncertain.
- `TaskResult`: estado terminal candidato, resumen, evidence_ids, reason_code.
- `DesktopAdapter`: connect(secret_handle), observe(), execute(validated_action), disconnect(); propietario único, contexto async con cleanup.
- `SessionAuthority`: acquire(destination_key), validate(session_id,epoch), revoke(), close_and_confirm().
- `TaskRepository`: accept_with_outbox(), claim(), begin_closing(), finalize_after_close(), get_for_team().
- `EventRecorder`: append(run_id,type,parent,payload_sanitized); secuencia transaccional por run.

Tools visibles al operador: observe, click, double_click, press_key, replace_text, scroll. No shell, filesystem libre, reset/check ni acceso a DB/secretos para el modelo. Coordenadas enteras dentro del framebuffer actual; no reescalar silenciosamente. Clic hold 180 ms inicial (>=100 ms); double_click define cada pulsación y separación. Texto reemplaza selección, prueba Unicode/acentos y modificadores. Wait de repintado acotado hasta 1 s seguido de nueva captura; no aceptar resultado por un sleep sin observar.

Ejecutor rechaza observación de otra run/sesión/epoch, propuesta vencida y revisión invalidada por una acción anterior. Caducidad inicial 10 s configurable: si el modelo tarda, refrescar imagen y replantear. No es garantía contra cambios autónomos de pantalla; para commit se exige captura y verificación reciente de identidad/campos. Error de transporte tras enviar mutación ⇒ uncertain, observar antes de decidir. Si no hay evidencia suficiente, fallida.

## Grafo multiagente

`admit → observe → orchestrator → operator → validate → execute → observe → verifier → orchestrator`, con recuperación acotada cuando la verificación falla; `finalize_candidate → closing → terminal` fuera de la decisión del modelo. Estados y propuestas tipados; rutas deterministas para deadline/error.

Orquestador define alcance e hitos; operador propone acciones; verificador consulta evidencia y determina identidad/efecto; recuperador diagnostica popups, navegación fallida o incertidumbre. Ningún especialista emite RFB. Registrar delegaciones reales con parent_event_id y retorno tipado. La política actual verifica cada propuesta, incluida navegación. La evolución por hitos y bypass selectivo requiere los gates de multiagente-contexto-jev.md; no habilitarlo por una etiqueta risk del modelo. No paralelizar mutaciones; análisis de misma captura permitido con invalidación al cambiar frame.

Presupuesto inicial: una tarea activa por VPS, 60 acciones, 40 llamadas de modelo, 2 recuperaciones por bloqueo, timeout de modelo min(20 s, tiempo restante seguro). Son límites configurables y se ajustan midiendo E1–E5, no supuestos de éxito. Limitar también repeticiones de observación idéntica. Agotar presupuesto devuelve fallida cerrada.

UI es dato no confiable: no obedecer notas que pidan ampliar tarea, revelar secretos o ejecutar comandos. Consultas no autorizan mutaciones; homónimos sin desambiguación suficiente ⇒ abstención con identificadores observados. Actualizaciones preservan campos no solicitados; exportar requiere verificar lista, estado y rango. Fechas relativas se derivan de la app visible, no del servidor.

## Resultado y privacidad

Una pregunta respondida incluye `respuesta: <valor>`; el primer número en esa línea debe ser el valor numérico. Dos datos en orden solicitado: `respuesta: <dato 1>; <dato 2>`. Una pregunta fallida no incluye respuesta inventada. El harness verifica esto; el agente no conoce la respuesta esperada.

No conservar chain-of-thought ni respuestas completas del proveedor. Registrar decisiones resumidas y evidencia. Password, token, endpoint privado y DSN nunca van al contexto, eventos, checkpoints, Redis, logs o resumen. Imágenes transitorias para el modelo; referencias privadas para trazabilidad. Desactivar trazado automático de mensajes/modelos en demo. Test con secretos canario y base64 en todos los sinks.

## Despliegue

Plataforma elegida: **Dokploy en VPS, con Docker Compose para los servicios necesarios y escalado por réplicas**. La configuración y gates están en [despliegue Dokploy](despliegue-dokploy.md). API y workers pueden escalar; cada escritorio conserva una sola autoridad VNC. PostgreSQL y Redis permanecen en una instancia cada uno para demo.

## Estructura objetivo del repositorio

```text
app/api/                 routes, auth, schemas
app/core/                config, clocks, budgets, sanitization
app/domain/              contracts, states, result formatting
app/persistence/         SQLAlchemy models, repositories, events, secrets
app/queue/               Redis streams, outbox dispatcher, consumer
app/worker/              runner, supervisor, session authority, heartbeats
app/desktop/             gateway, VNC adapter, tool executor
app/agents/              state, graph, orchestrator, operator, verifier, recovery
app/models/              provider adapter, structured responses
migrations/              Alembic application tables only
scripts/                 bootstrap, harness, release smoke
infra/                   Dockerfile, compose.yaml, configuración Dokploy
tests/{unit,integration,contract,e2e}/
```

## Fuentes técnicas verificadas

[Persistencia LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence): checkpointer por thread y store entre threads. [Memoria PostgreSQL](https://docs.langchain.com/oss/python/langgraph/add-memory): inicialización nativa `setup`. [SQLAlchemy async](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html): sesiones independientes por tarea concurrente. Fijar versiones compatibles en P00 y demostrarlo en pruebas, no depender de latest durante el incidente.
