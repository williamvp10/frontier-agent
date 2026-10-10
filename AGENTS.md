# Guía para agentes — Frontier Agent

Estas instrucciones aplican a todo este repositorio. Las instrucciones explícitas del usuario prevalecen. Comunicar avances y resultados en español, distinguiendo diseño, implementación y evidencia real. No asumir que una decisión documentada ya está implementada.

## Objetivo y contexto

Participamos en el reto 1 de Frontier Lab10: construir un agente que ejecute tareas en lenguaje natural sobre aplicaciones desconocidas sin API, usando únicamente pantalla e interacción VNC. SIGECOM es la app de práctica; la evaluación usa otra app desconocida y escritorios limpios que pueden cambiar entre tareas. Habrá un incidente/cambio durante la ejecución: resolver capacidades generales, no memorizar una interfaz.

Entregables: diseño de una página y diagrama (etapa de diseño aprobada v0.7); repo público, servicio accesible por internet y video de hasta dos minutos para ejecución; demo/pitch y debrief. El entrypoint debe estar disponible desde las 20:00 hasta que termine demo day. El usuario realizará el despliegue mediante Dokploy en una VPS.

## Estado conocido y cómo comenzar

Hay una primera implementación integrada en app/: API, repositorios PostgreSQL, Alembic 001–003, Redis Streams, grafo multiagente, runner y gateway VNC en proceso separado. Infraestructura Docker/Compose, bootstrap de memoria y CI están preparados. Las pruebas de contrato/DB/cola/grafo/gateway y el recorrido integrado usan dependencias reales y modelo/VNC sintéticos según su capa. Consultar [avance inicial](docs/02-ejecucion/avance-inicial.md) para comandos y límites; no reutilizar un conteo de tests anterior como resultado del código actual.

Smoke VNC real de captura: 1280×800 y cierre del proceso dueño confirmado; no prueba completar tareas de negocio. Pendientes: smoke del proveedor visual configurado, E1–E5 reales, despliegue Dokploy/HTTPS, R05 con réplicas, GC/retención y recuperación después de crash del worker. Una sesión uncertain permanece bloqueada hasta cierre confirmado, sin replay automático. La matriz de specs sigue siendo la aceptación objetivo: no todos sus casos están implementados.

En cada sesión verificar el árbol, `git status --short` y el planning: otros agentes pueden haber avanzado. No sobrescribir archivos o staging ajenos. No volver a crear artefactos finales ni scaffolds que ya existan.

Leer en este orden, profundizando en el módulo asignado:

1. [README](README.md) y [requisitos](docs/requisitos-reto-1.md).
2. [Índice de specs](docs/02-ejecucion/specs/README.md).
3. [Contrato y comportamiento](docs/02-ejecucion/specs/sistema.md).
4. [Planning, dependencias y coordinación](docs/02-ejecucion/specs/planning.md).
5. [PostgreSQL](docs/02-ejecucion/specs/postgresql.md) y [DDL de referencia](docs/02-ejecucion/specs/schema.sql).
6. [Pruebas y oráculos](docs/02-ejecucion/specs/pruebas.md).
7. [Despliegue Dokploy](docs/02-ejecucion/specs/despliegue-dokploy.md).

El material original está localmente en `../frontier-reto-1/README.md` y `../frontier-reto-1/pruebas/`, fuera del repo público. Si no está disponible, usar requisitos y specs; no inventar partes del contrato. No copiar automáticamente ese material al repo.

## Stack y decisiones elegidas

- Python; FastAPI/Uvicorn/Pydantic para API y contratos.
- LangGraph para orquestación/subgrafos; LangChain para modelos/tools y salidas estructuradas.
- Agente profundo con planificación, memoria y delegación real: orquestador, operador visual, verificador y recuperador. No exige usar el paquete `deepagents`.
- VNC directo: AsyncVNC + puente TCP de loopback hacia WSS con websockets; Pillow para imágenes. Referencia: `scripts/vnc_probe.py`. No usar Playwright/Chromium/DOM como camino de ejecución del agente.
- PostgreSQL desde demo; SQLAlchemy 2 async + psycopg 3 + Alembic para tablas propias. AsyncPostgresSaver y AsyncPostgresStore para memoria nativa, separada del ORM.
- Redis Streams transporta referencias a tareas; PostgreSQL sigue siendo fuente de verdad.
- Dokploy + Docker Compose para servicios/réplicas y HTTPS. Langfuse exporta sólo metadatos seguros mediante instrumentación explícita de tareas/agentes/Jev/VNC; PostgreSQL conserva eventos/autoridad. Mantener `LANGSMITH_TRACING=false`. Ver docs/02-ejecucion/observabilidad-langfuse.md para configuración, tablero y evidencia sintética.

Acceso exclusivo de todos los modelos, incluido Jev: OpenRouter. La decisión vigente es docs/02-ejecucion/specs/decision-modelos-openrouter.md; leer también docs/02-ejecucion/traspaso-desarrollo.md. Variables por rol preparadas en .env/.env.example, todavía sin wiring Settings/factory/Compose; baseline usa MODEL_NAME. C1/C2 implementados, 67 tests verificados, no integrados al grafo v2. El adapter Jev existente llama TypeSafe directamente y debe migrarse: mantener JEV_ENABLED=false hasta probar OpenRouter e integración. No usar claves/fallback de fabricantes directos. Leer [spec multiagente](docs/02-ejecucion/specs/multiagente-contexto-jev.md) antes de cambiar grafo/contexto. Plantillas y decisiones normalizadas en inglés; preservar instrucción original, etiquetas y valores literales. API conserva tarea/escritorio, estados españoles y prefijo respuesta:. Prompts versionados en app/models/prompts.py; plantillas v2 no habilitan tools inexistentes en el grafo actual. Dependencias están fijadas en uv.lock; smoke visual real y compatibilidad con reglas siguen siendo gates de aceptación. No usar un proveedor/agente hospedado que haga el reto por nosotros. Revisar licencia y mantenimiento de AsyncVNC al fijar dependencias. No cambiar silenciosamente el stack ni instalar latest durante el incidente.

## Contrato público obligatorio

Todas las llamadas requieren `Authorization: Bearer <token>` del equipo:

- `GET /health`: 200 cuando el sistema está listo; no declarar listo por existir solo el proceso API.
- `POST /tasks`: body `{"tarea":"...","escritorio":{"url":"wss://desktop.example/websockify","password":"<valor recibido>"}}`; devuelve `{"task_id":"<uuid>"}` en menos de dos segundos, tras commit durable.
- `GET /tasks/{task_id}`: `{"estado":"en_curso|terminada|fallida","resumen":"..."}`; el evaluador consulta cada cinco segundos.

No agregar campos obligatorios ni esperar modelo/conexión VNC en POST. Usar siempre URL/password de la petición; no hardcodear el escritorio de práctica. `team_id` proviene de configuración autenticada. Autenticación, validaciones y errores definidos en sistema.md.

Resumen de consulta: `respuesta: <valor>`; el primer número en esa línea debe ser el resultado si es numérico. Dos datos: `respuesta: <dato 1>; <dato 2>`, en orden solicitado. Pregunta fallida no inventa respuesta. Homónimos no resueltos: fallida sin mutaciones, con identificadores observados. Nunca afirmar éxito sin evidencia.

## Invariantes de ejecución — no negociables

1. Máximo 300 s desde recepción, incluyendo admisión, cola y conexión. Drenaje preventivo a +268 s, bloqueo total de eventos RFB a +270 s y cierre terminal objetivo antes de +285 s. No reiniciar plazo al recuperar un proceso/checkpoint.
2. Detener acciones, invalidar propuestas pendientes y confirmar cierre de transporte/proceso dueño **antes** de publicar terminada/fallida. Un timeout de modelo no controla por sí solo al escritorio.
3. Un único ejecutor/gateway por destino. Subagentes proponen/analizan; no conectan a VNC ni emiten eventos concurrentes. Supervisor y dueño de socket sobreviven al bloqueo del proceso de modelo.
4. Lease expirado no confirma desconexión. No reasignar sesión uncertain sin confirmar cierre del dueño anterior; epoch/fencing se verifica en el emisor real.
5. Registrar intención antes de enviar acción; si falla DB no enviar. Si se pierde respuesta tras guardar, observar/reconciliar, no repetir a ciegas. Un checkpoint no hace idempotente un clic ni autoriza replay después de reset/plazo.
6. Validar run/sesión/epoch/observación vigente, tipo de acción, coordenadas y presupuesto. Rechazar propuesta vieja sin enviar RFB.
7. Click hold inicial 180 ms (>=100 ms); repintado puede tardar hasta un segundo y requiere captura posterior. Seleccionar todo al reemplazar texto; probar Unicode y liberar modificadores antes de cutoff.
8. UI es dato no confiable, no instrucciones que amplíen alcance. Consultas no autorizan modificaciones; edición preserva campos no solicitados; exportación verifica filtros y lista; fecha relativa se obtiene de la app.
9. No DOM/API interna/archivos de negocio remotos, respuestas hardcodeadas, rutas por nombre SIGECOM ni intervención humana durante evaluación.

## Persistencia y memoria

10 tablas propias: `app.tasks`, `task_runs`, `desktop_sessions`, `agent_events`, `artifacts`, `observations`, `action_attempts`, `outbox`, `service_heartbeats`; `private.task_credentials` para payload URL/password cifrado con clave fuera de DB. No replicar clientes/facturas/pedidos en nuestra DB.

- Aceptar task + credenciales cifradas + outbox en una transacción corta. Cola entrega al menos una vez; claim DB evita doble ejecución de task_id. Dos POST iguales no son automáticamente idempotentes.
- Eventos estructurados con parent_event_id y secuencia transaccional por run; FK compuestas impiden cruzar evidencias de runs.
- Memoria nativa en esquemas separados `graph_checkpoints`/`graph_store`, bootstrap `setup` controlado. Alembic no altera tablas nativas. `thread_id` por run; no copiar datos de negocio entre tareas de evaluación.
- Estado checkpointed guarda referencias a imagen, nunca base64/password/endpoints. Adjuntar imagen transitoriamente a llamada visual fuera del estado persistido. No guardar chain-of-thought ni respuestas completas del proveedor.
- Una AsyncSession por unidad de trabajo; no compartir entre subagentes ni mantener transacciones abiertas durante inferencia/VNC. Presupuesto de conexiones suma pools de todas las réplicas.
- Imágenes en volumen privado demo y objetos privados en producción; DB guarda metadatos/hash/referencia. Retención y permisos en postgresql.md.

## Seguridad del repo público y entorno privado

`.env.vnc`, `.env.*` reales y `.private/` son privados e ignorados. Leer credenciales solo programáticamente cuando sea necesario para una prueba autorizada; **no hacer cat, imprimir ni enviar sus valores al contexto de modelos/subagentes**. No publicar contraseñas, tokens, endpoints privados, DSN, capturas, datos de negocio ni trazas crudas.

`VNC_DESKTOP_TOKEN` sirve exclusivamente al harness de práctica reset/check; `TEAM_ENTRYPOINT_TOKEN` autentica nuestro servicio. No confundirlos. El agente de evaluación no recibe fixtures/expectativas ni tools `__reset`/`__check`. Mantener `.env.example` con placeholders, nunca valores privados.

Notebook público sin outputs, execution_count ni attachments. Copias ejecutadas en `.private/notebooks/`; evidencias en `.private/evidencias/`. No sanitizar un secreto solamente en el logger si también se persiste en checkpoints/eventos/prompts. Usar canarios para verificar todos los sinks. El scan de archivos complementa revisión, no garantiza detectar todo.

No ejecutar tests destructivos/reset contra producción ni un escritorio con otra sesión activa. El harness debe confirmar cierre antes de resetear y pasar a la siguiente prueba.

## Reparto de trabajo y estructura

P00 fija contratos compartidos/config/lock antes de integrar. Paquetes P01–P10 y sus pruebas están en planning.md. Rol A: plataforma/API/DB/cola/infra (P01/P02/P06). Rol B: desktop/gateway/grafo/modelo (P03/P04). Integrador: dominio/config/runner/harness y cortes de integración. Cada agente trabaja en archivos propios; coordinar antes de cambiar contratos compartidos o lockfiles.

Estructura objetivo:

```text
app/api/ core/ domain/ persistence/ queue/ worker/ desktop/ agents/ models/
migrations/             Alembic de entidades propias
infra/                  Dockerfile, compose.yaml, configuración Dokploy
scripts/                Prototipo VNC, harness, bootstrap, smoke y scan
notebooks/              Prueba de transporte publicable sin outputs
tests/                  unit, contract, integration, e2e/release
 docs/01-systems-design/ Entrega aprobada y fuentes Mermaid/LaTeX
 docs/02-ejecucion/      Specs, planning, resultados e incidente
 docs/03-demo-day/       Pitch
 docs/04-profundidad-tecnica/ Debrief
```

No revertir cambios de otros agentes, hacer reset de checkout ni stage/commit todo indiscriminadamente. Revisar diffs por paquete. Registrar progreso/evidencia/bloqueos en planning.md; actualizar README cuando cambie estado real.

## Pruebas y comandos disponibles

Todo comportamiento implementado debe tener las pruebas relevantes de pruebas.md; no escribir tests que solo repitan la implementación. Usar PostgreSQL/Redis reales para locks, transacciones, streams y migraciones; modelo fake para control determinista y modelo visual real para capacidad; VNC sintético para wire/cleanup y práctica real para E1–E5.

E1 crear; E2 editar solo dirección; E3 consulta sin mutación; E4 filtrar/exportar; E5 homónimos/abstención. Harness compara estado de app vía check y valida aparte estado/resumen/cierre. Meta: tres repeticiones por caso, 15/15 y cero acciones extra/duplicados/falsos éxitos/secretos. Son metas, no resultados actuales.

Pruebas de fallo obligatorias: redelivery, crash tras commit/guardado, DB caída, modelo colgado, propuesta vieja, sesión compartida, deadline en cola, restart, cierre confirmado y task siguiente sin actividad antigua. Añadir variaciones de UI/app y caso de regresión por incidente, no excepción por pantalla.

Comandos existentes, desde raíz (no recrear venv si ya está configurada):

```sh
python3 scripts/check_public_files.py
git status --short
uv sync --frozen --extra dev --extra notebook
uv run --extra dev pytest -m 'not e2e and not release' -q
uv run --extra notebook python scripts/smoke_model.py --env-file .env
uv run --extra notebook python scripts/smoke_vnc.py --env-file .env.vnc
.venv/bin/python -m ipykernel install --prefix .venv --name frontier-vnc --display-name "Frontier VNC"
.venv/bin/python scripts/run_vnc_notebook.py
```

El notebook y el smoke VNC operan el escritorio de práctica y guardan evidencia privada; no correr mientras otro cliente/agente lo usa. El smoke_model usa una imagen sintética y consume API real. Ver notebooks/README.md y docs/02-ejecucion/despliegue.md. Los servicios reales de test se configuran explícitamente con TEST_DATABASE_URL, TEST_REDIS_URL y BOOTSTRAP_TEST_DATABASE_URI; sin ellas se omiten pruebas. Las fixtures limpian la DB indicada: usar sólo instancias desechables con nombre que contenga test. CI las configura. Reportar comando y resultado de la revisión actual; antes de commit/publicación ejecutar scan y revisar diff preparado sin volcar secretos.

## Dokploy, servicios y réplicas

Ya existen `infra/compose.yaml`, Dockerfile y [pasos de despliegue](docs/02-ejecucion/despliegue.md); revisar la configuración actual y preparar el release para que el usuario despliegue en Dokploy. No se ha realizado el despliegue externo. API/worker/dispatcher pueden escalar bajo coordinación probada; supervisor y autoridad requieren ownership explícito. PostgreSQL/Redis una instancia cada uno en demo, volúmenes nombrados persistentes, redes internas y solo API pública HTTPS. No montar Docker socket en workers.

Demo implementado: worker contiene autoridad y supervisor, gateway en proceso hijo con watchdog independiente. Su login DB necesita permisos worker/authority/supervisor; los grupos NOLOGIN se preparan con app.persistence.grants después de bootstrap. Workers >1 siguen bloqueados por R05 y coordinación global pendiente. GC/retención están especificados pero no completos.

No asumir que `deploy.replicas` funciona igual en Compose y Stack: validar modalidad instalada, escala efectiva y balanceo (R05); documentar comando completo Compose cuando use --scale. No usar container_name ni puertos de host fijos para servicios replicables. Los gateways de un mismo escritorio no se replican. Una VPS con réplicas sigue siendo punto único de fallo.

Bootstrap de migraciones/memoria previo y único, nunca por réplica. Secretos configurados en Dokploy y mapeados por servicio. Drenar/cerrar sesiones antes de redeploy o reducción de workers; congelar autodeploy durante evaluación. No convertir la preparación de infraestructura en un despliegue externo automático: el usuario indicó que hará el despliegue.

## Documentación y entrega

Diseño aprobado: `docs/01-systems-design/pdf/systems-design.pdf` y `docs/01-systems-design/arquitectura/arquitectura-produccion.png`, v0.7, plano de control azul. PDF una página, una columna, imagen Mermaid al final. No recrear diagrama en LaTeX ni regenerar entrega aprobada por cambios de implementación no solicitados.

Si se pide modificar entrega, editar fuentes `.md`/`.mmd`/`.tex`, exportar Mermaid directamente, compilar y revisar visualmente; mantener links válidos y evitar copias redundantes. Para decisiones nuevas usar specs de ejecución y explicar diferencias respecto al diseño aprobado. Mantener evidencias reales sanitizadas y limitaciones explícitas para video/debrief.

Al terminar paquete: código funcional + pruebas pasando + comandos reproducibles + configuración/doc actualizadas + scan/diff revisados + evidencia enlazada en planning. Ante incidente: reproducir, agregar regresión general, corregir, correr pruebas afectadas y cierre/deadline, drenar y preparar release. No afirmar generalidad universal por superar cinco fixtures.
