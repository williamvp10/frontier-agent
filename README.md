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

Selección y planificación sin red ni configuración privada:

```sh
.venv/bin/python scripts/evaluate.py --list
.venv/bin/python scripts/evaluate.py --cases E1,E3 --repetitions 1 --dry-run
```

El [evaluador](docs/02-ejecucion/evaluaciones.md) añade progreso, métricas diferenciadas,
reportes privados incrementales y bloqueo persistente ante incertidumbre. La operación real
requiere selección explícita, `--execute` y reserva coordinada declarada con
`--exclusive-desktop`. Este corte se revisó estáticamente; tests y escritorio los valida
el agente responsable de pruebas.

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
