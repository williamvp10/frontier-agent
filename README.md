# Frontier Agent

Sistema multiagente para ejecutar tareas en lenguaje natural sobre aplicaciones desconocidas, accesibles exclusivamente por VNC. Construido para el reto 1 de Frontier Lab10.

## Estado

Existe una primera implementación integrada: API autenticada, PostgreSQL y migraciones, Redis Streams, grafo multiagente, runner y gateway VNC en proceso separado, más Docker Compose para Dokploy. Las pruebas deterministas y de integración usan modelos/escritorios sintéticos y PostgreSQL/Redis reales; ver [avance y límites](docs/02-ejecucion/avance-inicial.md).

El smoke real de captura validó 1280×800 y cierre del proceso dueño (`owner_stopped=true`), sin ejecutar una tarea de negocio. **E1–E5 con modelo visual real, despliegue HTTPS y escalado de workers todavía están pendientes.** El agente opera píxeles y siempre usa el escritorio recibido por petición, sin DOM ni API de negocio.

## Stack elegido

| Tecnología | Responsabilidad | Estado |
|---|---|---|
| Python | Backend, agentes y controlador | Implementación inicial y lock de dependencias |
| FastAPI + Uvicorn + Pydantic | API, autenticación y contratos tipados | Implementado; pruebas de contrato |
| LangGraph | Estado, rutas, orquestador y especialistas | Contexto/hitos v2 integrados; consulta real cero-input verificada |
| LangChain | Adaptadores visuales y salidas estructuradas | Perfiles por rol y smokes pagados con imágenes sintéticas aprobados |
| AsyncVNC + websockets | RFB y puente de transporte WSS | Wire tests TLS y smoke real de captura |
| Pillow | Framebuffer a imágenes | Validado |
| Redis | Streams, outbox y entrega al menos una vez | Implementado; integración real |
| Dokploy + Docker Compose + HTTPS | Servicios y réplicas en VPS | Imagen/Compose preparados; despliegue pendiente |
| PostgreSQL | Tareas, ejecuciones, interacciones y memoria | 10 tablas propias; integración real |
| SQLAlchemy 2 async + psycopg 3 + Alembic | Entidades de aplicación y migraciones | Implementado; Alembic 001–003 |
| AsyncPostgresSaver / AsyncPostgresStore | Checkpoints y memoria nativa LangGraph | Bootstrap separado y conexión runtime; reutilización de negocio deshabilitada |
| Almacenamiento de objetos privado | Capturas con retención y acceso restringido | Producción propuesta |
| Langfuse | Trazas, spans, métricas y tablero | Instrumentación explícita; ingestión sintética y 13 widgets remotos confirmados |

Acceso exclusivo de todos los modelos: **OpenRouter**, incluido Jev. Los perfiles por rol de [la decisión vigente](docs/02-ejecucion/specs/decision-modelos-openrouter.md) ya están conectados a Settings/factory/Compose. El grafo `context-v2` reutiliza C1/C2, mantiene el verificador y devuelve consejo al mismo solicitante. Jev usa OpenRouter Decisions; `JEV_ENABLED=false` sigue como default. Ver [integración, comandos y evidencia actual](docs/02-ejecucion/integracion-openrouter-v2.md). Los smokes pagados y una consulta VNC cero-input están aprobados; E1–E5 y aceptación de release siguen pendientes. La factory recibe claves desde entorno privado, sin fallback directo. Playwright/Chromium no forman parte del camino de ejecución. Revisar licencia y mantenimiento de AsyncVNC antes de distribuir.

## Arquitectura

```text
FastAPI → cola → worker con LangGraph/LangChain
                    ├── Orquestador
                    ├── Operador visual
                    ├── Verificador
                    └── Recuperador
                              ↓
                 Validador y ejecutor único
                              ↓
                    Adaptador VNC directo
                              ↓
                  Escritorio de la petición
```

Supervisor determinista independiente del modelo: revocar acciones y desconectar antes del resultado terminal. Escalar por escritorios independientes; los subagentes no controlan una sesión simultáneamente.

- [Global de producción](docs/01-systems-design/arquitectura-produccion.md).
- [Demo en VPS](docs/01-systems-design/arquitectura-demo.md).
- [Grafo multiagente](docs/01-systems-design/arquitectura-multiagente.md).
- [Persistencia, memoria y trazabilidad](docs/01-systems-design/persistencia.md).
- [Decisiones, escalabilidad y observabilidad](docs/01-systems-design/decisiones.md).

## Plan de desarrollo

[Especificación, planning, PostgreSQL y pruebas](docs/02-ejecucion/specs/README.md): contratos compartidos, paquetes de implementación y coordinación entre agentes.

## Persistencia desde el demo

PostgreSQL es la fuente de verdad: tareas, estado, resumen, ejecuciones y eventos de los agentes. SQLAlchemy asíncrono administra nuestras tablas; Alembic sus migraciones. Los adaptadores nativos de LangGraph almacenan checkpoints por thread y memoria de largo plazo permitida, sin pasar por nuestro ORM.

FastAPI confirma tareas y outbox en una transacción; dispatcher publica referencias a Redis. El historial relaciona task_id, run_id, agente y evento padre para reconstruir delegaciones y acciones. Imágenes en almacenamiento privado; la DB conserva referencias. Checkpoints no autorizan repetir mutaciones ni operar tras el plazo. En evaluación no compartir datos de negocio entre tareas.

## Contrato del reto

Todas las llamadas llevan `Authorization: Bearer <token>`.

- `GET /health`: 200 cuando el sistema está listo.
- `POST /tasks`: devuelve `{"task_id":"…"}` en menos de dos segundos.
- `GET /tasks/{task_id}`: devuelve `{"estado":"en_curso|terminada|fallida","resumen":"…"}`.

Entrada con valores ficticios:

```json
{
  "tarea": "Consulta el número de facturas vencidas del cliente indicado.",
  "escritorio": {
    "url": "wss://desktop.example/websockify",
    "password": "<contraseña recibida>"
  }
}
```

El plazo de cinco minutos incluye admisión, cola y ejecución. Objetivo: bloquear nuevas acciones a los 270 segundos y cerrar antes de los 285. Sin interacción humana durante evaluación; ambigüedad o imposibilidad se reportan como fallida con razones observadas.

## Prueba VNC y organización

Seguir [instrucciones del notebook](notebooks/README.md). Cada comando muestra resultados y capturas; las copias ejecutadas quedan en `.private/`.

```text
app/                         API, persistencia, cola, grafo, worker y gateway
migrations/                  Alembic para los esquemas propios
infra/                       Dockerfile y Compose para Dokploy
notebooks/                   Prueba de acceso sin outputs publicables
scripts/                     Bootstrap, harness, smoke y herramientas privadas
tests/                       Unit, contrato, integración y pruebas de infraestructura
docs/01-systems-design/       Diseño, decisiones y tres vistas Mermaid
docs/02-ejecucion/            Despliegue, resultados e incidente
docs/03-demo-day/             Pitch y material opcional
docs/04-profundidad-tecnica/  Preparación del debrief
.env.example                 Variables vacías y flags públicos
```

## Desarrollo y validación

```sh
uv sync --frozen --extra dev --extra notebook
uv run --extra dev pytest -m 'not e2e and not release' -q
python3 scripts/check_public_files.py
```

Sin `TEST_DATABASE_URL`, `TEST_REDIS_URL` y `BOOTSTRAP_TEST_DATABASE_URI`, las pruebas que requieren esos servicios se omiten. Configurarlas sólo para PostgreSQL/Redis **desechables**: las fixtures limpian tablas y migran. CI proporciona servicios separados. Ver [evidencia de plataforma](docs/02-ejecucion/plataforma-implementada.md) y [despliegue](docs/02-ejecucion/despliegue.md).

Para ejecutar la suite determinista completa con PostgreSQL/Redis desechables automáticamente (Docker Compose v2 y `uv`):

```sh
python3 scripts/test_with_services.py
```

El runner crea un proyecto aislado, inyecta las tres variables de test y limpia sus contenedores al terminar. Para Dokploy usar `infra/compose.yaml`: servicios `frontier-agent-*`, volúmenes persistentes por proyecto, bootstrap/grants automáticos y recursos configurables. Dominio HTTPS hacia **`frontier-agent-api:8000`**; comenzar con una API y un worker. Ver [presupuesto VPS, logins SQL y pasos de despliegue](docs/02-ejecucion/despliegue.md).

Smoke de visión real con imagen sintética (usa el proveedor configurado y consume API):

```sh
uv run --extra notebook python scripts/smoke_model.py --env-file .env
```

Smoke VNC de captura y cierre, sin mutar la app; correr sólo sin otro cliente activo:

```sh
uv run --extra notebook python scripts/smoke_vnc.py --env-file .env.vnc
```

Release exige E1–E5, HTTPS/readiness y cierre verificado. Worker demo combina autoridad/supervisor y opera secuencialmente; múltiples workers, GC/retención y recuperación de sockets tras crash no se consideran aprobados. Un destino `uncertain` no se reasigna sin confirmar cierre del dueño anterior.

### Evaluador de práctica CLI

Ejecutar los comandos desde la raíz de `frontier-agent`. El evaluador usa las
fixtures externas de `../frontier-reto-1/pruebas/`; no copiarlas al repositorio.
Con `--fixtures "<carpeta externa>"` se puede indicar otra ubicación.

#### 1. Validar el evaluador localmente, sin servicios ni escritorio

```sh
uv sync --frozen --extra dev --extra notebook
.venv/bin/python -m pytest tests/unit/test_harness.py tests/unit/test_evaluator_cli.py -q
```

Estas pruebas usan HTTP simulado, reloj controlado y directorios temporales.
Comprueban el flujo CLI, tokens separados, bloqueo/reinicio/reconciliación,
reportes y canarios; no resetean ni operan el escritorio real.

Validación del 10 de octubre de 2026: **58 pruebas del evaluador pasan**. Se corrigió
el reporte con opciones por defecto (sin eventos/detalles), antes bloqueado por
una comparación entre rutas `None`. `--list` y los dry-runs de 2 y 15 casos también
se ejecutaron correctamente. Esto no acredita E1–E5 reales.

#### 2. Listar y planificar sin red ni credenciales

```sh
.venv/bin/python scripts/evaluate.py --list
.venv/bin/python scripts/evaluate.py --cases E1,E3 --repetitions 1 --dry-run
.venv/bin/python scripts/evaluate.py --all --repetitions 3 --dry-run
```

El listado debe mostrar E1–E5. El primer dry-run planifica dos casos; el segundo,
15. `--cases` respeta el orden indicado. Estos modos no cargan `.env`, no crean
locks/reportes y no llaman al servicio. Selección desconocida/duplicada, catálogo
inválido o repeticiones fuera de 1–10 terminan con error antes de ejecutar.

#### 3. Preparar el entorno real de práctica

API, PostgreSQL, Redis, worker y gateway/supervisor deben estar disponibles; el
runner debe publicar terminal únicamente después de confirmar cierre del dueño
VNC. Reservar el escritorio sin otros clientes/agentes antes de la prueba.
`--exclusive-desktop` declara esa reserva: el lock local no detecta clientes externos.

Completar **privadamente** `.env.vnc` con estas variables, sin poner tokens en
argumentos shell ni en el README:

| Variable | Uso |
|---|---|
| `TEAM_ENTRYPOINT_URL` | URL HTTPS de nuestra API, por ejemplo `https://agent.example` |
| `TEAM_ENTRYPOINT_TOKEN` | Bearer de nuestra API: health, admisión y consulta |
| `VNC_BASE_URL` | Origen HTTPS del control del escritorio de práctica |
| `VNC_WS_URL` | URL WSS del mismo escritorio; host/puerto deben coincidir con VNC_BASE_URL |
| `VNC_PASSWORD` | Contraseña del escritorio, enviada sólo a admisión |
| `VNC_DESKTOP_TOKEN` | Bearer del harness de práctica para health/reset/check |

El entorno del proceso prevalece sobre el archivo. Si los tokens están en otro
archivo local, combinar la configuración privadamente antes de ejecutar; la CLI
no carga automáticamente `.env` además de `.env.vnc`. `--entrypoint` permite
sobrescribir la URL de API cuando sea necesario, sin cambiar la autoridad durante
una operación pendiente.

**Cada caso con `--execute` resetea el escritorio de práctica**, espera su health
y estabilización, envía únicamente tarea/escritorio al agente, hace polling cada
5 s y llama a `__check` con las expectativas desde el harness. Usarlo sólo contra
práctica aislada. Las expectativas nunca se entregan al agente.

#### 4. Ejecutar primero E3 y revisar su resultado

```sh
.venv/bin/python scripts/evaluate.py --env-file .env.vnc \
  --cases E3 --repetitions 1 --execute --exclusive-desktop
```

La terminal muestra progreso y `PASS`, `FAIL`, `ERROR` o `SKIPPED`, sin instrucciones,
respuestas de negocio ni credenciales. El reporte incremental queda en
`.private/evaluaciones/latest.json`; una ejecución posterior archiva el anterior.
Revisar `counts`, `reports[].checks`, `error_code` y las métricas de cada fase.

- `PASS`: todos los oráculos pasan. E5 puede ser PASS con estado público `fallida`.
- `FAIL`: hay terminal válido, pero un resultado/chequeo no coincide.
- `ERROR`: infraestructura, protocolo o interrupción impidió evaluar.
- `SKIPPED`: no se inició tras error/bloqueo/interrupción anterior.

El tiempo de detección por polling no es el instante exacto de cierre. El reporte
identifica `closure_basis=api_terminal_contract`; cierre físico/acciones tardías
permanecen desconocidos sin evidencia adicional del gateway. Health del escritorio
más espera tampoco prueba readiness visual. Un coste no obtenido queda `null`.

#### 5. Ejecutar la suite completa tras aprobar el smoke y confirmar exclusividad

```sh
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
.venv/bin/python scripts/evaluate.py --env-file .env.vnc \
  --all --repetitions 3 --execute --exclusive-desktop \
  --report ".private/evaluaciones/suite-$STAMP.json" \
  --events ".private/evaluaciones/suite-$STAMP.events.jsonl"
```

La suite planifica 15 casos, por repetición y luego caso. JSON/JSONL se guardan
privadamente con permisos 0600. Usar nombres nuevos para los eventos: un JSONL
existente no se sobrescribe. `--private-details` es opcional y guarda instrucciones/
resúmenes redactados en otro archivo privado; no publicarlo.

Para correlacionar con Langfuse sin crear otra traza, añadir
`--langfuse-env-file .env` con `LANGFUSE_BASE_URL` y `LANGFUSE_PROJECT_ID` configurados
localmente. No requiere claves de trazado; `pending` no confirma ingestión ni coste.

#### 6. Resolver una interrupción o bloqueo antes de volver a ejecutar

Ctrl-C/SIGTERM detienen nuevos resets/admisiones y conservan el journal cuando
hay incertidumbre. Cerrar el CLI no cancela la tarea remota. Reconciliar mediante
lectura, con la misma API y el escritorio todavía reservado:

```sh
.venv/bin/python scripts/evaluate.py --env-file .env.vnc \
  --reconcile --exclusive-desktop
```

Con task_id conocido, sólo un terminal válido de la misma autoridad limpia el
bloqueo. `en_curso`, 404, error o API diferente lo conservan. Sin task_id conocido,
el integrador debe reconciliar la admisión incierta y confirmar cierre del dueño;
no borrar `.private/evaluaciones/state/` ni su clave para forzar otro reset.

Códigos de salida: **0** éxito/listado/plan/reconciliación, **1** FAIL, **2** ERROR o
configuración, **130** SIGINT y **143** SIGTERM. Las pruebas locales y el dry-run
validan el evaluador; la suite E1–E5 real debe ejecutarse y revisarse por separado.
Detalles adicionales: [evaluaciones](docs/02-ejecucion/evaluaciones.md).

## Configuración y privacidad

Completar `.env` para el backend y `.env.vnc` para pruebas VNC, ambos excluidos de Git. OpenRouter requiere `MODEL_PROVIDER=openrouter`, `OPENROUTER_API_KEY` y los modelos por rol de `.env.example`; `MODEL_PROFILES` permite overrides tipados. Settings requiere inyección de entorno o carga explícita. No se usa respaldo directo. No pegar claves en instrucciones de agentes. `VNC_DESKTOP_TOKEN` autentica reset/check de práctica; `TEAM_ENTRYPOINT_TOKEN` autentica nuestra API. Nunca incluirlos en prompts, resúmenes, trazas ni documentos públicos.

LangGraph orquesta la ejecución; **Langfuse aporta observabilidad con metadatos seguros**.
Configurar `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` y `LANGFUSE_BASE_URL` en `.env`;
se activa al tener ambas claves (`LANGFUSE_ENABLED=true`). Mantener `LANGSMITH_TRACING=false`.
Una traza por tarea y spans de agentes/Jev/VNC exportan tiempos, llamadas, falta de progreso,
escalamiento y uso de tokens, sin prompts, contextos privados, imágenes ni resúmenes.
Ver [configuración, tablero y evidencia](docs/02-ejecucion/observabilidad-langfuse.md).

```sh
uv run python scripts/setup_langfuse.py --env-file .env --smoke
uv run python scripts/setup_langfuse.py --env-file .env --apply
```

Antes de publicar: `python3 scripts/check_public_files.py` y revisión de `git diff --cached`. El verificador bloquea coincidencias privadas conocidas y notebooks con outputs; no garantiza detectar todos los secretos.

Ver [requisitos](docs/requisitos-reto-1.md), [entregables](docs/README.md) y [archivos finales de Systems design](docs/01-systems-design/README.md).

La [spec multiagente con Jev](docs/02-ejecucion/specs/multiagente-contexto-jev.md) define memoria con procedencia, mapa parcial y ejecución por hitos. Los prompts son inglés; etiquetas UI, valores e instrucción original se preservan. Contrato público y resumen siguen la [validación del entrypoint](docs/02-ejecucion/specs/contrato-entrypoint.md).
