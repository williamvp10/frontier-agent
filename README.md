# Frontier Agent

Sistema multiagente que ejecuta tareas en lenguaje natural sobre aplicaciones
desconocidas mediante **capturas de pantalla y VNC**, sin DOM ni APIs de negocio.
Construido para el reto 1 de Frontier Lab10.

La API admite tareas de forma durable; un worker coordina planificación, operación
visual, verificación y recuperación. Un único ejecutor controla cada escritorio y
confirma su cierre antes de publicar un resultado terminal. El plazo de 300 s
incluye admisión, cola y ejecución. El corte de ejecución es a los 290 s (4:50);
el objetivo de cierre es 295 s, dejando margen hasta el límite de 300 s.

**Estado:** contexto/hitos v2, modelos por rol y Jev OpenRouter están integrados.
Hay pruebas con servicios desechables, smokes de modelos y consulta VNC sin acciones.
E1–E5 completos, cierre tras crash y aceptación del despliegue público siguen pendientes.

## Contenido

- [Stack](#stack)
- [Ejecutar localmente con Python](#ejecutar-localmente-con-python)
- [Desplegar con Docker y Dokploy](#desplegar-con-docker-y-dokploy)
- [Ejecutar el evaluador de práctica](#ejecutar-el-evaluador-de-práctica)
- [Ejecutar pruebas unitarias e integración](#ejecutar-pruebas-unitarias-e-integración)
- [API y documentación](#api-y-documentación)

## Stack

| Tecnología | Uso |
|---|---|
| Python 3.12/3.13 + uv | Runtime y dependencias fijadas en `uv.lock` |
| FastAPI, Uvicorn y Pydantic | API Bearer, validación y contratos tipados |
| LangGraph y LangChain | Orquestación por hitos y salidas estructuradas |
| OpenRouter | Acceso exclusivo a modelos visuales y Jev Decisions |
| PostgreSQL 16, SQLAlchemy, psycopg y Alembic | Tareas, credenciales cifradas, eventos y migraciones |
| AsyncPostgresSaver / Store | Memoria y checkpoints privados por run |
| Redis 7 Streams | Outbox, transporte de referencias y entrega durable |
| AsyncVNC, websockets y Pillow | VNC sobre WSS/RFB, capturas y ejecutor aislado |
| Docker Compose y Dokploy | Servicios, volúmenes persistentes y proxy HTTPS |
| Langfuse | Observabilidad explícita con metadatos permitidos |
| pytest y Ruff | Pruebas deterministas/integración y lint |

Los perfiles por rol se configuran con `MODEL_COORDINATOR`, `MODEL_OPERATOR`,
`MODEL_VERIFIER`, `MODEL_RECOVERY` y `MODEL_ANALYST`. Jev comparte
`OPENROUTER_API_KEY` y permanece opcional con `JEV_ENABLED=false` por defecto.

Los cuatro roles principales son Coordinator, Operator, Verifier y Recovery.
La [mejora de navegación visual](docs/02-ejecucion/mejora-navegacion-visual.md)
describe descubrimiento de menús según la tarea, pruebas parciales y auditoría
de efectos. `ACTION_PROPOSAL_TTL_SECONDS=30` limita la vigencia de cada propuesta
sin superar el cutoff; las propuestas caducadas requieren nueva observación.

## Ejecutar localmente con Python

Los comandos siguientes se ejecutan desde la raíz de `frontier-agent` en Linux/macOS.
Requisitos: Python 3.12 o 3.13, [uv](https://docs.astral.sh/uv/), PostgreSQL 16 y
Redis 7 disponibles localmente; crear previamente una base de desarrollo `frontier`.

### 1. Instalar dependencias y preparar configuración

```sh
uv sync --frozen --extra dev --extra notebook
cp -n .env.example .env
chmod 600 .env
```

`uv sync` crea `.venv`. Completar `.env` localmente; `cp -n` conserva un archivo existente.

| Variable | Configuración local |
|---|---|
| `DATABASE_URL` | DSN SQLAlchemy: `postgresql+psycopg://<usuario>:<password>@127.0.0.1:5432/frontier` |
| `LANGGRAPH_DATABASE_URI` | URI nativa: `postgresql://<usuario>:<password>@127.0.0.1:5432/frontier` |
| `REDIS_URL` | Redis local, por ejemplo `redis://127.0.0.1:6379/0` |
| `TEAM_ENTRYPOINT_TOKEN` | Token aleatorio para autenticar la API |
| `CREDENTIALS_ENCRYPTION_KEY` | Clave Fernet válida para cifrar credenciales |
| `DESTINATION_HMAC_KEY` | Secreto aleatorio independiente para identificar destinos |
| `OPENROUTER_API_KEY` | Credencial del proveedor de modelos |

Los ejemplos contienen placeholders. URL-encodear usuario/password en los DSN.
Para desarrollo puede usarse un propietario SQL local; producción requiere logins por rol.
Langfuse es opcional: `LANGFUSE_ENABLED=false` desactiva exportación. Las imágenes
quedan en `.private/artifacts/`; no compartir memoria de negocio entre runs.

### 2. Inicializar la base y memoria

```sh
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python scripts/bootstrap.py
```

Aplica Alembic y prepara los esquemas nativos de LangGraph. El usuario SQL necesita
permisos DDL; ejecutar antes de arrancar el runtime. Si se usan logins separados,
configurar `GRANTS_DATABASE_URL` administrativo y aplicar los grupos/grants:

```sh
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m app.persistence.grants
```

El job de grants crea grupos `NOLOGIN`, no usuarios ni contraseñas.

### 3. Arrancar los tres procesos en terminales separadas

```sh
# Terminal 1: API local
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m uvicorn \
  app.api.main:app --host 127.0.0.1 --port 8000 --no-access-log

# Terminal 2: publicación de outbox a Redis
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m app.queue.main

# Terminal 3: worker, modelos y dueño VNC por tarea
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m app.worker.main
```

`Settings` no carga `.env` automáticamente: `dotenv run` inyecta el entorno sin
mostrar sus valores. No iniciar otra instancia del worker durante pruebas del escritorio.

### 4. Comprobar readiness con Python

```sh
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -c '
import os, urllib.request
r = urllib.request.Request("http://127.0.0.1:8000/health", headers={
    "Authorization": "Bearer " + os.environ["TEAM_ENTRYPOINT_TOKEN"]})
print(urllib.request.urlopen(r, timeout=5).read().decode())
'
```

Esperado: `{"ok":true}`. La API devuelve 503 si faltan DB, Redis, memoria o
heartbeats del worker/gateway/supervisor; arrancar sólo Uvicorn no basta.

## Desplegar con Docker y Dokploy

Requisitos: Docker Compose v2, variables privadas de Dokploy y acceso a OpenRouter.
El archivo de runtime es `infra/compose.yaml`; construir desde la raíz del proyecto.

### 1. Configurar variables

Usar la plantilla específica de despliegue (sin variables VNC ni DSN manuales):

```sh
cp -n infra/dokploy.env.example .env.dokploy
chmod 600 .env.dokploy
# Completar privadamente los valores antes de validar.
docker compose --env-file .env.dokploy -f infra/compose.yaml config --quiet
```

En Dokploy pegar la plantilla en **Environment** y completar:
`POSTGRES_PASSWORD`, `TEAM_ENTRYPOINT_TOKEN`, `CREDENTIALS_ENCRYPTION_KEY`
(Fernet), `DESTINATION_HMAC_KEY` y `OPENROUTER_API_KEY`. Para Langfuse completar
`LANGFUSE_PUBLIC_KEY` y `LANGFUSE_SECRET_KEY`; ajustar su región con
`LANGFUSE_BASE_URL` si corresponde. Los valores reales quedan fuera de Git.

El token autentica el contrato público; Fernet cifra las credenciales VNC recibidas;
HMAC identifica destinos para la exclusión de sesiones. Conservar esas claves entre
releases. Redis y los DSN internos se configuran automáticamente. Los recursos y
modelos por rol mantienen los defaults de Compose, con overrides opcionales.

### 2. Bootstrap automático

El primer `up` aplica migraciones, memoria y grants, crea tres logins PostgreSQL
con passwords aleatorias y guarda las conexiones en volúmenes privados separados.
API, dispatcher y worker montan únicamente su conexión en lectura y esperan al job.
Solo bootstrap recibe la contraseña administrativa; runtime no puede ejecutar DDL.
Reintentos conservan las contraseñas generadas. No hace falta entrar a psql.

En Dokploy usar su red de proxy existente. En Docker independiente, crear la red
si no existe (debe coincidir con `DOKPLOY_NETWORK`):

```sh
docker network inspect dokploy-network >/dev/null 2>&1 || docker network create dokploy-network
```

Para instalaciones existentes siguen admitidos los overrides privados
`MIGRATION_DATABASE_URL`, `BOOTSTRAP_LANGGRAPH_DATABASE_URI`, `GRANTS_DATABASE_URL`,
`API_DATABASE_URL`, `DISPATCHER_DATABASE_URL`, `WORKER_DATABASE_URL` y
`LANGGRAPH_DATABASE_URI`. Omitirlos en una instalación nueva.

### 3. Arrancar y consultar estado

```sh
docker compose --env-file .env.dokploy -f infra/compose.yaml up -d --build
docker compose --env-file .env.dokploy -f infra/compose.yaml ps
docker compose --env-file .env.dokploy -f infra/compose.yaml logs --tail=100 frontier-agent-bootstrap
```

**Dokploy:** seleccionar Compose, archivo `infra/compose.yaml`, variables privadas
y dominio HTTPS hacia **`frontier-agent-api:8000`**. PostgreSQL/Redis quedan en red
interna; sólo la API entra a la red del proxy. Mantener `API_REPLICAS=1` y un worker
inicialmente. El worker está fijado a una réplica hasta validar R05/crash.

**Docker local con HTTPS, listo para el evaluador:** el helper crea configuración
privada y certificados, utiliza un proyecto/volúmenes separados y conserva claves
entre arranques. No envía tareas ni hace reset del escritorio.

```sh
.venv/bin/python scripts/local_runtime.py prepare --port 18443
.venv/bin/python scripts/local_runtime.py start
.venv/bin/python scripts/local_runtime.py status
# Preparar sólo el archivo privado del evaluador; no ejecuta pruebas
.venv/bin/python scripts/local_runtime.py configure-evaluator
```

API local: **`https://127.0.0.1:18443`**. Ejecutar manualmente E3 cuando el escritorio
esté reservado, confiando explícitamente en la CA local (sin desactivar TLS):

```sh
.venv/bin/python scripts/evaluate.py \
  --env-file .private/local-docker/evaluator.env \
  --ca-file .private/local-docker/tls/ca.pem \
  --cases E3 --repetitions 1 --execute --exclusive-desktop
```

Detener sólo este runtime local, después de confirmar cierre de tareas activas:

```sh
.venv/bin/python scripts/local_runtime.py stop
```

**Alternativa Docker local HTTP:** Compose base no publica puertos al host. Para acceder por HTTP local,
crear `.private/compose.local.yaml` con este override:

```yaml
services:
  frontier-agent-api:
    ports:
      - "127.0.0.1:8000:8000"
```

```sh
docker compose --env-file .env -f infra/compose.yaml -f .private/compose.local.yaml up -d --build
```

La API queda en `http://127.0.0.1:8000`. El evaluador exige HTTPS: para usarlo con una
instancia local, configurar un proxy TLS; Dokploy aporta ese routing en despliegue.
Conservar nombre de proyecto y volúmenes entre releases. Para detener sin borrar datos:

```sh
docker compose --env-file .env -f infra/compose.yaml down
```

No añadir `--volumes` a la parada de un runtime con datos. Confirmar cierre de tareas
activas antes de reiniciar/desplegar. Guía ampliada: [despliegue](docs/02-ejecucion/despliegue.md).

## Ejecutar el evaluador de práctica

El evaluador usa las fixtures externas de `../frontier-reto-1/pruebas/` por defecto.
Para otra ubicación añadir `--fixtures "<carpeta externa>"`; no copiarlas al repo.

### Planificar sin red ni credenciales

```sh
.venv/bin/python scripts/evaluate.py --list
.venv/bin/python scripts/evaluate.py --cases E1,E3 --repetitions 1 --dry-run
.venv/bin/python scripts/evaluate.py --all --repetitions 3 --dry-run
```

### Preparar configuración privada y ejecutar

API/worker y dependencias deben estar saludables, con cierre confirmado antes de
terminal. Reservar exclusivamente el escritorio de práctica: `--exclusive-desktop`
declara esa reserva, y el lock local no detecta clientes VNC externos.

Configurar en `.env.vnc`: `TEAM_ENTRYPOINT_URL` (HTTPS), `TEAM_ENTRYPOINT_TOKEN`,
`VNC_BASE_URL`, `VNC_WS_URL`, `VNC_PASSWORD` y `VNC_DESKTOP_TOKEN`. El origen HTTPS/WSS
del escritorio debe coincidir. El token de equipo autentica nuestra API; el token
desktop autentica health/reset/check del harness. La CLI carga sólo el archivo
indicado con `--env-file`, y el entorno del proceso prevalece.

**`--execute` resetea el escritorio antes de cada caso.** Ejecutar primero E3 en
práctica aislada, revisar su resultado y confirmar cierre antes de la suite:

```sh
.venv/bin/python scripts/evaluate.py --env-file .env.vnc \
  --cases E3 --repetitions 1 --execute --exclusive-desktop

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
.venv/bin/python scripts/evaluate.py --env-file .env.vnc \
  --all --repetitions 3 --execute --exclusive-desktop \
  --report ".private/evaluaciones/suite-$STAMP.json" \
  --events ".private/evaluaciones/suite-$STAMP.events.jsonl"
```

La suite planifica 15 casos. Reportes incrementales y JSONL privados con permisos
0600; el default es `.private/evaluaciones/latest.json`. Usar nombres nuevos para
eventos. Resultados: **PASS** todos los oráculos pasan, **FAIL** resultado incorrecto,
**ERROR** no se pudo evaluar, **SKIPPED** no iniciado tras bloqueo/interrupción.
E5 puede ser PASS con estado público `fallida`.

Ctrl-C/SIGTERM no cancela la tarea remota. Si queda un bloqueo, reconciliar por
lectura con la misma API y el escritorio reservado, sin otro reset:

```sh
.venv/bin/python scripts/evaluate.py --env-file .env.vnc --reconcile --exclusive-desktop
```

Sólo un terminal válido de la misma autoridad limpia un task_id conocido. Sin
task_id, el integrador debe reconciliar ownership/cierre; no borrar el journal de
`.private/evaluaciones/state/` para eludirlo. Exit codes: 0 éxito, 1 FAIL, 2 ERROR,
130 SIGINT, 143 SIGTERM. Polling mide detección, no cierre físico; coste ausente es
`null`. Correlación opcional: `--langfuse-env-file .env`, sin crear otra traza.
Detalles y límites: [evaluaciones](docs/02-ejecucion/evaluaciones.md).

## Ejecutar pruebas unitarias e integración

### Pruebas unitarias

Tras `uv sync --frozen --extra dev --extra notebook`, las pruebas locales usan
fakes y no necesitan `.env`, modelos pagados ni escritorio real:

```sh
# Todas las pruebas unitarias
.venv/bin/python -m pytest tests/unit -q

# Sólo el evaluador: CLI, locks, reportes y reconciliación con HTTP simulado
.venv/bin/python -m pytest tests/unit/test_harness.py tests/unit/test_evaluator_cli.py -q

# Unitarias, contrato API y parser de Compose (Docker CLI para el último)
.venv/bin/python -m pytest tests/unit tests/contract tests/infra/test_compose.py -q

# Lint y revisión de archivos públicos
.venv/bin/python -m ruff check app tests scripts
python3 scripts/check_public_files.py
```

### Pruebas de integración con servicios desechables

Para la suite con **PostgreSQL/Redis desechables reales**, se requieren Docker
Compose v2 y uv:

```sh
python3 scripts/test_with_services.py
# Opcional: sólo infraestructura, usando las mismas dependencias aisladas
python3 scripts/test_with_services.py tests/infra -q
```

El runner crea un proyecto aislado, inyecta las tres variables de test y limpia
sus contenedores al terminar; no carga `.env` ni usa DB runtime. Sin ese runner,
los tests de DB/Redis requieren `TEST_DATABASE_URL`, `TEST_REDIS_URL` y
`BOOTSTRAP_TEST_DATABASE_URI` de instancias desechables; las fixtures limpian tablas.
Los tests locales no acreditan completar E1–E5 en la app real.

## API y documentación

Los endpoints operativos requieren `Authorization: Bearer <TEAM_ENTRYPOINT_TOKEN>`:

| Ruta | Respuesta |
|---|---|
| `GET /health` | `{"ok":true}` cuando dependencias/heartbeats están listos |
| `POST /tasks` | `{"task_id":"<uuid>"}` tras admisión durable |
| `GET /tasks/{task_id}` | Estado `en_curso|terminada|fallida` y resumen |

Abrir **`/docs`** en el origen de la API (local: `http://127.0.0.1:8000/docs`;
Dokploy: por ejemplo, `https://agent.example/docs`) para ver Swagger y los esquemas.
La documentación y **`/openapi.json`** se pueden consultar sin token. Para usar
**Try it out**, pulsar **Authorize** e introducir el valor de `TEAM_ENTRYPOINT_TOKEN`,
sin escribir el prefijo `Bearer`: Swagger lo añade. `/health` y `/tasks` mantienen
la autenticación; ReDoc permanece deshabilitado.

```json
{
  "tarea": "Consulta el dato solicitado sin cambiar registros.",
  "escritorio": {"url": "wss://desktop.example/websockify", "password": "<valor privado>"}
}
```

`.env`, `.env.vnc`, `.private/`, capturas y reportes locales quedan fuera de Git.
Nunca publicar credenciales, respuestas de negocio, imágenes o razonamiento del
proveedor. Mantener `LANGSMITH_TRACING=false`; Langfuse exporta sólo metadatos permitidos.

- [Diseño del sistema](docs/01-systems-design/README.md)
- [Modelos y perfiles OpenRouter](docs/02-ejecucion/specs/decision-modelos-openrouter.md)
- [Integración y evidencia](docs/02-ejecucion/integracion-openrouter-v2.md)
- [Observabilidad Langfuse](docs/02-ejecucion/observabilidad-langfuse.md)

### Presupuesto y recuperación para evaluación

El límite contractual sigue siendo 300 segundos por tarea. La configuración permite
120 llamadas de modelo y seis recuperaciones, con corte de acciones a los 290 segundos
y objetivo de cierre a los 295. El verificador Sonnet dispone de 40 segundos por llamada;
los reintentos de modelo requieren tiempo restante y una nueva captura, sin repetir
acciones ya enviadas. Estos límites permiten recuperar errores de interpretación y
latencia sin declarar éxito cuando falta evidencia.

El coordinador delimita el objetivo; el operador identifica menús, controles y campos
visibles; el verificador contrasta identidad, efecto y cumplimiento; el recuperador
propone alternativas ante navegación, contratos o evidencia insuficientes. Los campos
de texto visibles pueden completarse en un grupo acotado antes de verificar; confirmar
o guardar sigue siendo una acción independiente. Las evidencias parciales se conservan
y una verificación final incompleta reabre el trabajo pendiente.

Las pruebas automatizadas y la evaluación real del escritorio son validaciones distintas.
Los casos reales todavía requieren repetir la evaluación tras estos cambios; no se
presentan como una suite completamente aprobada. Las capturas, trazas y resultados
permanecen en almacenamiento local ignorado, fuera del repositorio público.
