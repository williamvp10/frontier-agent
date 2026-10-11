# Planning de implementación y coordinación

Estado actualizado: P00–P06 tienen una implementación inicial integrada y pruebas por capa; los paquetes siguen parciales hasta superar sus gates completos. El notebook y el smoke real prueban acceso/captura/cierre, no E1–E5. Ver [avance inicial](../avance-inicial.md), [plataforma](../plataforma-implementada.md) y [despliegue preparado](../despliegue.md). Estimaciones de esfuerzo, no garantías; revisar con medición tras el primer recorrido. Prioridad: tener servicio desplegado temprano y corregir capacidades generales antes del incidente.

## Orden y paquetes

A = agente de plataforma (ideal para el otro agente). B = agente de ejecución visual. I = integrador, responsable de contratos, integración y evidencia. Son roles de trabajo; no implican subagentes dentro del producto. Con dos desarrolladores, B asume I en cortes definidos.

| ID / prioridad | Responsable / archivos | Dependencias | Entrega y aceptación | Pruebas | Esfuerzo orientativo |
|---|---|---|---|---|---|
| P00 · P0 | I; app/domain, app/core, pyproject/lock | — | Modelos compartidos de sistema.md, config sin secretos, reloj/presupuestos, elegir modelo permitido y pin de dependencias; validar visión y salidas tipadas | U01,U02,U07,G01 | 20–30 min |
| P01 · P0 | A; persistence, migrations, bootstrap | P00 | 10 tablas, repositorios y grants, admisión/outbox atomicidad, cifrado, nativos setup separados; upgrade en DB real | D01–D08 | 45–70 min |
| P02 · P0 | A; api, queue | P01 | API auth/contrato y readiness; dispatcher/consumer Streams, duplicate claim; smoke de tareas sintéticas sin VNC | C01–C05,Q01–Q03 | 40–60 min |
| P03 · P0 | B; desktop, worker/supervisor/session | P00; integrar repos P01 | Adaptador desde prototipo, gateway único, observaciones/actions tipadas, revocación y cierre fuera del modelo; no acción post-close | V01–V05,S01–S06 | 50–80 min |
| P04 · P0 | B; agents, models | P00,P03; saver P01 | Orquestador + 3 especialistas, guard de acciones, verificación y recovery acotados; evidencia de delegación real | U03–U06,G01–G05 | 60–90 min |
| P05 · P0 | I; worker/runner y ensamblaje | P02,P03,P04 | POST→DB→cola→grafo→VNC→cierre→GET sobre tarea simple; consumo deadline cola; historial reconstruible | I01–I04 | 30–45 min |
| P06 · P0 | A; infra, CI, release smoke | P02,P03; actualizar con P05 | Dokploy en VPS + Docker Compose, HTTPS, réplicas verificadas, migraciones/bootstrap único y volúmenes privados; smoke externo | R01–R05 | 30–50 min |
| P07 · P0 | I; scripts/harness, tests/e2e | P05; desplegado P06 | E1–E5 con reset/check y validación aparte de estado/resumen; 3 repeticiones, resultados sanitizados, cero expectativas al grafo | E01–E05,SEC01 | 45–75 min más correcciones |
| P08 · P0 | B + I; fixtures sintéticos/faults | P05 | Variaciones de UI/datos y fallos tras guardado; test nueva app sintética, mismo agente; incident-ready | A01–A06,S01–S06 | 45–60 min |
| P09 · P0 | I; docs/02-ejecucion y entrega | P06–P08 | Repo revisado, entrypoint/token por canal privado, video <=2 min, freeze con evidencias; servicio disponible durante demo | SEC01,R01,E01–E05 | 20–30 min |
| P10 · P1 | A/B; evolución posterior | P09 | Multiworker/multihost, cuotas, objetos privados, LangSmith filtrado, carga y retención operativa | L01,SEC02,D08 | Después de demo |

P03 inicia contra repositorio fake con el mismo protocolo mientras P01 avanza. No se acepta P03 hasta integrar la DB real. P04 puede empezar con modelo fake y framebuffer sintético; no se acepta sin modelo visual real y sesiones reales. P06 debe comenzar apenas API/worker smoke estén disponibles, sin esperar las 15 pruebas finales.

Cada fila requiere test relacionado y demostración; no basta scaffold, endpoint que devuelve éxito fijo o subagentes decorativos. Cambiar estado a hecho solo enlazando evidencia y comandos reales.

## Cortes de integración

1. **Corte 0:** congelar `app/domain/contracts.py` y nombres de interfaces. Ambos agentes importan las mismas clases.
2. **Corte 1:** API durable y gateway seguro pasan por separado; unir con runner fake. Probar deadline y proceso muerto antes del primer guardado real.
3. **Corte 2:** recorrido visual completo para consulta no mutante. Desplegar este servicio utilizable mediante Dokploy y Docker Compose en VPS.
4. **Corte 3:** creación/edición/exportación con verificador y riesgo; E5 debe rechazar antes de tocar datos.
5. **Corte 4:** 15 ejecuciones del set, adaptación, fallos e incidente. Congelar antes del límite y producir video.

## Reparto con el otro agente

El encargo antiguo de recrear P01/P02/P06 queda sustituido por [delegación del siguiente corte](delegacion-desarrollo.md). Agente de apoyo: P04-C1/C2, sólo context.py/context_service.py y sus pruebas. Integrador: adapter Jev, luego grafo por hitos y validación real del baseline. Ambos acuerdan contratos antes de conectar consumidores.

Checkout compartido: archivos exclusivos; no stage/commit los cambios del otro ni cambiar lockfile sin coordinar. Mantener un solo dueño del escritorio durante pruebas reales. No empezar analistas paralelos, bypass de verificación ni multiworker sin sus gates.

## Definición de terminado por paquete

- Cambios funcionales y tipados, errores controlados, tiempos acotados y cleanup.
- Pruebas que verifican comportamiento y fallos relevantes, no réplica de implementación.
- Config/env.example actualizados sin valores privados, dependencia fijada y documentación de comandos reales.
- Evidencia redactada con versión de código/modelo/grafo, IDs de tests, entorno, resultado y limitaciones.
- Scan de archivos públicos, diff revisado y ningún output sensible en notebook.
- Sin rutas/IDs/respuestas hardcodeadas de SIGECOM. Regresión sintética si se corrige un problema del set.

## Incidente y adaptación

Antes del incidente: snapshot de versión y métricas, datos de práctica aislados; no modificar el servicio mientras opera una sesión mutante. Al llegar cambio: reproducir → decidir qué contrato/capacidad afecta → agregar caso de regresión sintético → parche mínimo en adaptador/prompts/política → correr paquete afectado + cierre/deadline + smoke → desplegar drenando sesiones.

No cambiar el contrato del entrypoint ni convertir un nuevo issue en una excepción por nombre de pantalla. Si cambia transporte, sustituir adaptador; si cambia UI, reobservar y replantear; si cambia el modelo, usar adapter y validar salida. Una variación arbitraria de app puede superar las capacidades: reportar fallo veraz, no éxito sin evidencia.

## Registro de avance

Ajuste de documentación API: Swagger `/docs` y esquema `/openapi.json` habilitados
para lectura pública; esquema HTTP Bearer declarado, middleware conservado para
operaciones y rutas no documentales. Verificación:
`python3 scripts/test_with_services.py tests/contract/test_api.py tests/integration/test_platform.py tests/integration/test_runner_flow.py -q`:
**46 passed**, PostgreSQL/Redis desechables. Incluye acceso a docs, rechazo de
prefijos/métodos no exentos, contrato, límites, readiness y recorrido sintético;
sin despliegue externo ni VNC real.

| Paquete | Estado | Evidencia / comando ejecutado | Bloqueo |
|---|---|---|---|
| P00 | Parcial | Contratos/config/uv.lock y pruebas unitarias; OpenRouter único; Jev consejero especificado | Smoke visual real del proveedor/modelo y reglas |
| P01 | Parcial | SQLAlchemy, Alembic 001–003, repositorios/grants y tests PG; bootstrap nativo aislado | GC/retención, aislamiento de memoria y pools en release |
| P02 | Parcial | API/auth/readiness/outbox/Streams; pruebas contrato e integración real | Readiness/latencia externas; fallos durante sesiones reales |
| P03 | Parcial | Gateway proceso/watchdog, gate tipado; 4 wire tests TLS/RFB y smoke real 1280×800 con cierre | Crash worker y cierre incierto; validación bajo tareas reales |
| P04 | Parcial | Grafo con orquestador/operador/verificador/recuperador; tests fake y adaptadores visuales | Inferencia real, subagentes en E1–E5 y adaptación real |
| P05 | Parcial | Runner integrado y pruebas API→cola→grafo→cierre con modelo/VNC sintéticos | Recorrido real de negocio, fallos de dependencia y restart seguro |
| P06 | Parcial | Compose con nombres frontier-agent, recursos configurables, bootstrap/grants automático y volúmenes por proyecto; runner desechable: 300 passed sin skips. Build y smoke bootstrap/API/dispatcher + límites Docker comprobados; [evidencia](../despliegue.md#evidencia-del-ajuste-de-infraestructura) | Usuario Dokploy/HTTPS; R01–R05 y workers múltiples no aprobados |
| P07 | Parcial | Harness disponible y pruebas de sus oráculos | E1–E5 reales x3, modelo configurado y entrypoint accesible |
| P08 | Parcial | Casos sintéticos de fallo, deadline, propuestas antiguas y redelivery | App alternativa/UI, fallos tras guardado reales e incidente |
| P09 | Pendiente | Repo local y documentación de avance | Release completo, repo publicado, entrypoint y video |
| P10 | Pendiente | Diseño/spec de evolución | Después de demo; no habilitar multiworker sin R05 |


El comando base es `uv run --extra dev pytest -m 'not e2e and not release' -q`; indicar variables de servicios de pruebas desechables y cualquier skip. Los conteos de ejecuciones anteriores no representan automáticamente la suite actual. Smoke visual: `uv run --extra notebook python scripts/smoke_model.py --env-file .env`; VNC captura/cierre: `uv run --extra notebook python scripts/smoke_vnc.py --env-file .env.vnc`. No operar simultáneamente el escritorio.

Actualizar cada fila al comenzar y terminar el paquete. No ejecutar el planning como automatización ni crear tareas externas sin indicación del usuario.

## Infraestructura elegida

P06 debe seguir [despliegue Dokploy](despliegue-dokploy.md), entregar `infra/compose.yaml` y registrar cómo se aplican las réplicas en la instalación real. El usuario realizará el despliegue en Dokploy; preparar configuración y pasos reproducibles para ello.

## Evolución P04: contexto, hitos y Jev

La [spec normativa](multiagente-contexto-jev.md) prevalece para esta evolución. Prompts y contenido normalizado en inglés; sin alterar contrato público ni ampliar plazo. Política actual verifica todas las acciones hasta probar un bypass seguro.

| Corte | Archivos | Entrega / gate | Estado |
|---|---|---|---|
| P04-C1 | agents/context.py, schemas.py | SharedTaskContext, Fact con procedencia, mapa, pending_operation, budgets y contratos congelados | Reutilizado por grafo v2; contratos originales preservados |
| P04-C2 | agents/context_service.py | Reducer, versiones, permisos y proyecciones por rol e invalidación; MC01–MC03 | Integrado con serialización por run y checkpoints; extensiones runtime limitadas a interpretación vigente y cuotas decrecientes |
| P04-C3 | agents/graph_v2.py, runtime.py, models/turns.py, prompts.py | Hitos activos, progreso y relevo sin redescubrimiento; MC04 | Implementado; consulta sintética con modelos reales y consulta VNC cero acciones verificadas; E1–E5 pendientes |
| P04-C4 | models/jev_advisor.py, config, Compose | AdviceRequest completo, adapter OpenRouter Decisions acotado/optional; MC05–MC07 | Transporte directo retirado; HTTP simulado y smoke textual pagado aprobados; JEV_ENABLED=false |
| P04-C5 | agents/graph_v2.py | Consejo vuelve al solicitante, FinalDecision y stale guards; MC08 | Conectado a roles solicitantes; retornos/fallos probados con fakes; no convierte choice en RFB |
| P04-C6 | tests + harness | Paralelismo sólo analítico, segunda app y benchmark baseline/contexto/Jev; MC09–MC10 | Pendiente |

No usar Jev como fallback de visión. No enviar screenshots a Jev. No migrar runs activos al schema nuevo: graph_version v2, drenar antes de actualizar; runs antiguos no hacen replay. Mantener totales de tiempo/coste por proveedor y rol. El SDK OpenAI puede permanecer como cliente compatible de OpenRouter; esto no habilita OpenAI directo.

Revisión OpenRouter-only/prompts: 73 tests unit/contract/Compose pasan; [evidencia y pendientes API](contrato-entrypoint.md). Sin nueva inferencia real ni despliegue.

Corte consejero: [componentes Jev y evidencia](../jev-implementado.md), 48 tests enfocados pasan (HTTP simulado). Puente de contexto y retorno al solicitante preparados; sin activación en grafo/worker ni inferencias reales.

## Traspaso vigente

### Observabilidad implementada

Langfuse integrado en runner, delegación v1/v2, adaptadores de modelos/Jev y límites
VNC. Trazas por tarea con metadatos permitidos, scores de tiempo/llamadas/progreso/
escalamiento y shutdown posterior al cierre. Tablero remoto **Frontier - observabilidad v1**
confirmado con 13 widgets; smoke remoto de 4 observaciones sintéticas en `test`.
`uv run --extra dev pytest tests/unit/test_observability.py -q`: 7 passed.
Ver [comandos, semántica, privacidad y límites](../observabilidad-langfuse.md).
No equivale a validar E1–E5 ni tareas reales desplegadas.

El usuario delega el desarrollo a otro integrador. Leer [traspaso](../traspaso-desarrollo.md): C1/C2 entregados, routing por rol preparado en entorno pero no integrado; todo modelo incluido Jev exclusivamente OpenRouter según decision-modelos-openrouter.md. Sustituye el reparto anterior y cualquier referencia a TypeSafe directo.

## Corte de integración OpenRouter / contexto v2

El traspaso anterior describe el punto de partida. Routing por rol, perfiles,
Compose y Jev Decisions ahora están conectados. Se preservaron los cambios
concurrentes de observabilidad; las pruebas de visión/VNC de este corte no
activaron exportación externa. Evidencia y límites actuales:
[integración OpenRouter v2](../integracion-openrouter-v2.md).

Verificación intermedia de este corte con PostgreSQL 16.13 y Redis 7.4.8
desechables: 254 passed, 4 warnings del upstream AsyncVNC. Consulta de título de
vista real autorizada con exclusividad: terminada, 22,40 s, cero intentos de
acción y dueño cerrado. Los resultados finales posteriores se registran en la
evidencia enlazada; no presentar esto como E1–E5, benchmark o aceptación release.

## Corte evaluador de práctica CLI

Implementados `scripts/evaluate.py` y auxiliar `scripts/evaluation_support.py`: selección
list/cases/all/dry-run, progreso seguro, métricas independientes, reportes incrementales,
lock local/journal persistente, reconciliación de task_id conocido y correlación Langfuse
sin nueva traza ni conteo de USD. Ver [uso y límites](../evaluaciones.md).
Se propone `evaluation_pass` separado al propietario Langfuse; no se cambió su módulo.
Ruff estático pasa. Por instrucción del usuario, tests y ejecución real quedan al otro
agente: no se modificó ni ejecutó `tests/unit/test_harness.py`, ni se operó el escritorio.
No marcar P07 como aprobado ni reutilizar conteos anteriores para esta nueva interfaz.

## Ajuste P06: primer despliegue Dokploy (2026-10-10)

Implementados logins y DSN automáticos en volúmenes privados por servicio; plantilla
mínima `infra/dokploy.env.example` con cinco secretos requeridos y Langfuse opcional.
Compatibilidad de overrides locales conservada. 34 tests de infra/local/plataforma
pasan con dependencias desechables; parser final 2 passed, build exitoso y Compose
completo con worker listo y `/health` autenticado 200, segundo bootstrap exitoso.
Evidencia: [corrección Dokploy](../despliegue.md#corrección-de-primer-despliegue-dokploy--2026-10-10).
Pendientes P06: desplegar cambios en Dokploy, HTTPS externo y gates R01–R05.
Sin nueva tarea VNC, inferencia ni publicación del repo.

### Runtime Python local para iteración — 2026-10-10

PostgreSQL Homebrew verificado; creada base local desechable `frontier_api_test`,
Alembic 003 y esquemas nativos LangGraph inicializados. Redis dedicado Docker
verificado con PONG y persistencia. API, dispatcher y un worker ejecutados desde
el checkout con TLS local y configuración/logs ignorados. Runtime Docker previo
sin tareas ni sesiones abiertas; detenidos sus procesos de aplicación antes de
iniciar el worker Python. Verificados reinicio controlado, `/health` autenticado
200, sin token 401, Swagger 200 y consultas del visor de eventos. Evaluador
preparado privadamente y E3 planificado offline; no ejecución/reset/VNC real.
Comandos y configuración local en `.private/local-python/README.md`, sin secretos
ni diagnósticos privados en archivos públicos. Esto acredita disponibilidad local,
no éxito E1–E5 ni despliegue externo.

### Evaluación real local E3 — 2026-10-10

Una ejecución E3 autorizada con reserva exclusiva declarada, usando API local
HTTPS y configuración privada. Resultado **0/1 PASS, 1 FAIL, 0 ERROR**. Admisión
0,10 s, estado terminal `fallida` detectado a 50,5 s; razón técnica
`invalid_reconciliation`. Evaluador: `app_state`, plazo, admisión y contrato de
cierre OK; estado esperado y respuesta fallaron. DB confirmó sesión `closed`
con evidencia. API continúa lista después de la ejecución. No acredita E3.
Reporte/eventos/consola en `.private/local-python/evaluation-E3-20261010-run1.*`;
visor local: `.venv/bin/python .private/local-python/runtime.py logs`.
Próximo diagnóstico: reconciliación del verificador en `app/agents/graph_v2.py`.

### Auditoría posterior E3 — 2026-10-10

[Revisión de arquitectura y evidencia](../auditoria-arquitectura-e3.md): distingue
fallo Docker context_rejected/invalid_contract sin acciones de fallo Python
invalid_reconciliation con dos acciones. 5 PNG privados verificados, 58 eventos,
19 checkpoints y 30 observaciones Langfuse remotas. Brechas: reconciliación
incomplete/applied, estado SQL de efecto sin actualizar y motivo Langfuse other.
Jev deshabilitado, sin valor medido. Verificación actual 119 + 10 pruebas unitarias
pasando; no repetición ni reset durante la auditoría.

### Iteración visual y cierre E3 — 2026-10-10

Ensayo manual autorizado vía VNC; evidencia privada e ignorada. Implementados
descubrimiento de rutas relevante a la tarea, comprobación de destino/filtros/
paginación, separación de efecto y completitud, pruebas parciales explícitas,
confirmación SQL de efectos, recuperación con códigos técnicos y canonicalización
de acciones. Entrada de texto configurable, UTF-8 probado en runtime local.
Al satisfacer el último criterio parcial se solicita verificación final y respuesta,
sin inventar otro hito. Ver [cambio y límites](../mejora-navegacion-visual.md).

Suite completa **340 passed** con PostgreSQL/Redis desechables; Ruff y scan público
limpios. Ejecución E3 posterior con v2.7: **1/1 PASS**, admisión 0,03 s, terminal
196,3 s; respuesta, app_state y cierre OK. Diez acciones SQL verificadas, 14 PNG
privados y 267 eventos; transporte y dueño cerrados. Reporte/eventos/consola en
`.private/local-python/evaluation-E3-20261010-run8.*`; auditoría privada vinculada
a la ejecución y a Langfuse. API local continúa saludable. Jev sigue deshabilitado.
No acredita E1–E5 completos, estabilidad por repetición ni aceptación release.

## Plazo actualizado por solicitud del usuario

Nuevas tareas: corte a +290 s (4:50), cierre objetivo +295 s y límite +300 s.
El runner calcula el corte desde accepted_at incluyendo cola, y respeta cutoff
persistido de tareas anteriores. Defaults de Settings/repository y Compose alineados;
archivos privados Dokploy actualizados sin modificar .env. Sustituye los márgenes
anteriores de +268/+270/+285; no garantiza terminal durable con DB caída/cierre incierto.
Verificación con PostgreSQL/Redis desechables:
`python3 scripts/test_with_services.py tests/unit/test_runner.py tests/unit/test_gateway.py tests/integration/test_platform.py tests/integration/test_runner_flow.py tests/contract/test_api.py tests/infra/test_compose.py -q`:
**62 passed**. Incluye límite en cola a +289/+290, no ampliar tareas existentes,
modelo bloqueado y cierre antes de terminal. Compose privado valida con `config --quiet`;
Ruff, diff y scan públicos pasan. Sin despliegue externo ni escritorio real.
