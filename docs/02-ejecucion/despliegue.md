# Despliegue preparado: Dokploy y Docker Compose

Infraestructura implementada en `infra/Dockerfile` y `infra/compose.yaml`, para **Docker Compose v2 en Dokploy**, origen Git. El usuario realiza el despliegue; todavía no se ha probado un dominio público ni una entrega remota. No activar autodeploy durante evaluación. La estructura de red/volúmenes sigue el [ejemplo High Garden Coffee](https://github.com/williamvp10/high-garden-coffee/blob/main/compose.dokploy.yaml), con nombres propios de Frontier Agent.

## Servicios y acceso

| Servicio | Configuración privada |
|---|---|
| frontier-agent-postgres | POSTGRES_PASSWORD |
| frontier-agent-redis | Conexión interna automática, sin claves externas |
| frontier-agent-bootstrap | POSTGRES_PASSWORD; migraciones, memoria, grants y creación de logins |
| frontier-agent-api | Conexión propia en volumen, token, Fernet y HMAC |
| frontier-agent-dispatcher | Conexión propia en volumen |
| frontier-agent-worker | Conexión propia en volumen, Fernet/HMAC, OpenRouter y Langfuse |

La plantilla mínima es `infra/dokploy.env.example`: cinco secretos obligatorios y
las dos claves Langfuse opcionales para activar telemetría. `.env.example` corresponde
al desarrollo Python y pruebas VNC; no copiar todas sus variables a Dokploy.
No inyectar un `.env` completo en cada contenedor.

El bootstrap construye los DSN administrativos internamente (incluye escaping de
passwords), ejecuta Alembic/setup/grants y crea `frontier_compose_api_login`,
`frontier_compose_dispatcher_login` y `frontier_compose_worker_login`. Son logins
sin superusuario, CREATEDB ni CREATEROLE. Reciben respectivamente `frontier_api`,
`frontier_dispatcher` y `frontier_worker` + `frontier_authority` + `frontier_supervisor`.
El worker demo incluye autoridad y supervisor.

Las passwords aleatorias se conservan en tres volúmenes de conexión con archivos
0600 y dueño UID 10001. Bootstrap monta los tres; cada runtime monta solo el suyo
en lectura. Conservar esos volúmenes y el nombre de proyecto entre releases.
Los DSN manuales anteriores siguen funcionando como overrides para logins existentes.
No se modifican sus passwords: los logins automáticos usan nombres distintos.

Red `backend` interna para PostgreSQL/Redis, red `egress` para modelo y VNC, red externa `dokploy-network` para la API. Solo `frontier-agent-api` se conecta a la red compartida del proxy: su nombre DNS no colisiona con `api`, `web` o servicios Coffee. Los demás nombres DNS quedan en redes privadas del proyecto.

Volúmenes `frontier_agent_pg`, `frontier_agent_redis`, `frontier_agent_artifacts`; Compose antepone el nombre de proyecto, igual que en el ejemplo Coffee. **Conservar el nombre de proyecto Dokploy entre releases** para reutilizar datos. No fijar `name:` global en los volúmenes; permite instalaciones independientes. Si ya existe un despliegue con las claves antiguas `postgres_data`, `redis_data`, `artifacts_data`, respaldar y migrar/reutilizar expresamente esos volúmenes antes de cambiar el Compose: los nombres nuevos no importan los datos anteriores. Las capturas permanecen privadas. No montar el socket Docker. No hay puertos de host ni `container_name` fijos. El volumen de artefactos se inicializa con UID/GID 10001 desde la imagen; verificar permisos al reutilizar un volumen existente.

Logs Docker rotan a 3 archivos de 10 MB por contenedor. Redis usa AOF con `appendfsync everysec` y `noeviction`: la DB/outbox sigue siendo la fuente durable. `REDIS_MAXMEMORY=256mb` deja margen respecto al límite de contenedor de 512 MB para AOF/reescrituras; medir y mantener ese margen si se ajusta. Revisar disco y hacer backup PostgreSQL/artefactos fuera de la VPS; el volumen persistente no es un backup. GC/retención de artefactos sigue pendiente.

## Presupuesto inicial de VPS

Base orientativa: **4 vCPU / 8 GB RAM**, disco SSD con espacio para imágenes, DB y capturas. Incluye margen para Dokploy/Traefik/SO y el build; medir junto con los otros proyectos de la VPS antes de reservar capacidad. Los límites por servicio no son una garantía de rendimiento.

| Servicio | Réplicas iniciales | Límite CPU / RAM por contenedor | Reserva CPU / RAM |
|---|---:|---|---|
| PostgreSQL | 1 | 1 / 1 GB | 0,25 / 256 MB |
| Redis | 1 | 0,5 / 512 MB | 0,1 / 128 MB |
| Bootstrap (temporal) | 1 | 1 / 512 MB | 0,1 / 128 MB |
| API | 1 | 0,5 / 512 MB | 0,1 / 128 MB |
| Dispatcher | 1 | 0,25 / 256 MB | 0,05 / 64 MB |
| Worker + gateway | 1 | 2 / 2 GB | 0,5 / 512 MB |

Runtime: límites agregados de 4,25 CPU y 4,25 GB; bootstrap añade hasta 512 MB durante el arranque. Los límites CPU pueden compartir núcleos; en Compose las reservas no actúan como un scheduler Swarm. La segunda API suma 0,5 CPU / 512 MB. `.env.example` permite ajustar `*_CPUS`, `*_MEMORY`, `REDIS_MAXMEMORY`, `API_REPLICAS`; no subir workers sin los gates de coordinación. Pool máximo previsto: API 5 conexiones por réplica, dispatcher 2, worker 5 ORM + 3 checkpoints + 2 store; dejar margen para bootstrap, administración y picos respecto al `max_connections` de PostgreSQL.

## Configuración en Dokploy

1. Seleccionar origen Git y **Docker Compose**, archivo `infra/compose.yaml`.
2. Pegar `infra/dokploy.env.example` en **Environment** y completar los cinco secretos.
   Añadir ambas claves Langfuse para observabilidad y verificar su región.
3. Guardar las variables antes de Deploy. Dokploy las escribe en `infra/.env`,
   junto al Compose; ese archivo del servidor es independiente del `.env` local.
4. Dominio HTTPS dirigido a **`frontier-agent-api`**, puerto interno **8000**.
   Ajustar `DOKPLOY_NETWORK` solo si la instalación usa otra red de proxy.
5. Deploy ejecuta bootstrap automáticamente; API/dispatcher/worker esperan exit 0.
   No se requieren DSN manuales, psql ni claves de proveedores distintos de OpenRouter.

Referencia: [Environment de Dokploy](https://docs.dokploy.com/docs/core/docker-compose#environment).
El mensaje `Detected: 0 mounts` no indica ausencia de volúmenes nombrados en Compose.
Un error `required variable ... is missing a value` es interpolación previa al build;
DNS y HTTPS no intervienen en ese paso.

`TEAM_ENTRYPOINT_TOKEN` autentica la API, `CREDENTIALS_ENCRYPTION_KEY` debe ser una
clave Fernet válida y `DESTINATION_HMAC_KEY` un secreto independiente. Son necesarios
para el contrato y almacenamiento/exclusión de escritorios, aunque las dependencias
externas sean PostgreSQL, Redis, OpenRouter y Langfuse. No regenerarlos entre releases.

Cambiar `POSTGRES_PASSWORD` en Environment tras inicializar PostgreSQL no cambia la
password SQL: rotarla en la base de forma coordinada. No borrar volúmenes para corregir
configuración. Drenar tareas antes de redeploy y mantener autodeploy desactivado durante
la evaluación.

Bootstrap usa un advisory lock para migraciones/memoria y rechaza otro setup concurrente.
Los logins se provisionan transaccionalmente, guardando su configuración antes del DDL
para que un reintento pueda reutilizarla. Ejecutar un despliegue por proyecto a la vez.
Las réplicas no ejecutan `setup()` ni crean logins.

## Réplicas y operación

Inicio: `API_REPLICAS=1`, 1 dispatcher, **1 worker**. `deploy.replicas` expresa la capacidad en Compose v2 moderno; comprobar número efectivo y límites con `docker compose ps` / `docker stats`. API puede pasar a `API_REPLICAS=2`; si la modalidad de Dokploy requiere override, usar el comando completo (incluyendo el build):

```sh
docker compose -f infra/compose.yaml up -d --build --scale frontier-agent-api=2 --scale frontier-agent-worker=1 --scale frontier-agent-dispatcher=1
```

Confirmar que la versión instalada aplica `deploy.resources` y `deploy.replicas`; este archivo no es un Stack y sus `build`/dependencias son Compose. Las réplicas API requieren verificación de balanceo Traefik. Worker 2+ queda bloqueado hasta validar R05: exclusión por destino, claims/outbox concurrentes, límite global de tareas, cierre tras crash y PostgreSQL/Redis compartidos. Agregar contenedores no habilita takeover seguro de un socket incierto. Con autoridad dentro de worker, separar autoridades para producción antes de escalar sin esa evidencia.

`init: true` recolecta hijos; worker dispone de 300 s de stop grace. Esto **no implementa drenaje**: cancelar admisión/drenar y confirmar cierre del gateway antes de redeploy/reducción. No confiar en SIGTERM + grace como prueba de cierre, y no usar `down -v` con datos reales. Mantener el plazo original de la tarea.

Después de nuevas migraciones/setup nativo, volver a aplicar grants: las tablas añadidas no heredan automáticamente permisos anteriores.

Antes de entrega: comprobar GET /health autenticado desde internet, POST <2 s con commit durable, task terminal y objetivo de cierre antes de 295 s, reinicio durable y R05. Healthcheck API usa token desde su entorno sin imprimirlo. PostgreSQL comprueba conexión TCP autenticada/`SELECT 1`; Redis comprueba PING; readiness público incluye dependencias/esquemas/heartbeats en la API. La API no depende de que worker sea healthy en Compose: evita circularidad y su `/health` valida el worker listo.

## Verificación local reproducible

```sh
python3 scripts/test_with_services.py
# Suite enfocada, con las mismas dependencias reales:
python3 scripts/test_with_services.py tests/infra -q
docker build -f infra/Dockerfile -t frontier-agent:infra-smoke .
python3 scripts/check_public_files.py
```

El runner necesita Docker Compose v2 con `up --wait` y `uv`. Arranca `infra/compose.test.yaml` bajo un nombre de proyecto aleatorio, espera healthchecks, descubre puertos loopback aleatorios e inyecta `TEST_DATABASE_URL`, `TEST_REDIS_URL` y `BOOTSTRAP_TEST_DATABASE_URI` **sólo al proceso de pruebas**. Ejecuta `uv run --frozen --extra dev pytest -m 'not e2e and not release' -q` y elimina los contenedores al terminar, incluso si falla pytest. Datos en tmpfs: no monta producción, no usa red Dokploy, no lee `.env`, no requiere OpenRouter ni VNC real. Dos ejecuciones no comparten DB/Redis ni puertos. No usar estas variables contra la DB runtime.

La prueba de bootstrap comprueba doble setup secuencial, esquemas correctos y exclusión concurrente. Las pruebas de Compose validan con el parser real y valores sintéticos. Este entorno elimina los skips por falta de DB/Redis; E1–E5 y pruebas release requieren sus gates reales y siguen separados.

### Evidencia del ajuste de infraestructura

- `python3 scripts/test_with_services.py`: **300 passed**, sin skips, 4 warnings upstream de deprecación TripleDES/AsyncVNC; PostgreSQL/Redis reales desechables, sin inferencia facturada ni escritorio real.
- `docker build -f infra/Dockerfile -t frontier-agent:infra-smoke .`: build exitoso en Docker Desktop ARM64.
- Smoke del Compose runtime en proyecto/red/volúmenes temporales con credenciales sintéticas: PostgreSQL/Redis healthy, bootstrap + grants exit 0, API/dispatcher arrancados después del job. `docker inspect` confirmó límites efectivos CPU/RAM de PostgreSQL, Redis, API y dispatcher. Los recursos del worker se validaron con el parser, sin arrancar inferencia real.
- `uv run --frozen --extra dev ruff check scripts/test_with_services.py tests/infra/test_compose.py`: pasa.
- No acredita todavía HTTPS/Dokploy externo, readiness con worker real ni balanceo multi-API/R05. Los contenedores/redes/volúmenes temporales se eliminaron al finalizar.

OpenRouter es el único acceso de modelos, incluido Jev. Jev comparte `OPENROUTER_API_KEY`
y no es un health gate mientras siga deshabilitado. No mapear credenciales de modelos
o Langfuse a la API/dispatcher.

## Langfuse en Dokploy

Configurar `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` y
`LANGFUSE_TRACING_ENVIRONMENT=production`. Compose los mapea únicamente al worker.
`LANGFUSE_ENABLED=true` exporta metadatos seguros si ambas claves existen; no requiere
un servicio Langfuse local ni afecta readiness. Mantener `LANGSMITH_TRACING=false`.
Preparar/verificar el tablero desde la máquina autorizada con
`uv run python scripts/setup_langfuse.py --env-file .env --apply`.
Ver [observabilidad y límites](observabilidad-langfuse.md).

## Corrección de primer despliegue Dokploy — 2026-10-10

El error de interpolación se reproduce cuando faltan las variables obligatorias.
El Compose ahora requiere solo cinco secretos: PostgreSQL, token, Fernet, HMAC y
OpenRouter. Langfuse conserva sus dos claves y URL regional. Se retiraron del ejemplo
las claves LangSmith sin uso y los DSN manuales requeridos para Dokploy; los overrides
siguen compatibles con el helper local y conexiones existentes.

Implementado `app.persistence.compose_runtime`: bootstrap administrativo, logins
por servicio, archivos 0600 en volúmenes independientes y carga de conexión antes de
`exec` del proceso runtime. La API declara explícitamente su comando porque Compose
ignora el CMD de imagen cuando se reemplaza entrypoint.

Evidencia de este cambio:

- `python3 scripts/test_with_services.py tests/infra tests/unit/test_local_runtime.py tests/integration/test_platform.py -q`: **34 passed**, PostgreSQL/Redis desechables.
- Parser final: `.venv/bin/python -m pytest tests/infra/test_compose.py -q`: **2 passed**; exige exactamente cinco secretos y montajes por servicio en lectura.
- `docker build -f infra/Dockerfile -t frontier-agent:dokploy-smoke .`: exitoso.
- Smoke del Compose completo en proyecto/red/volúmenes desechables, variables sintéticas y Langfuse desactivado: API healthy, PostgreSQL/Redis healthy, dispatcher/worker running; `/health` autenticado **200**. Segundo bootstrap exitoso sobre los mismos volúmenes. Sin tareas, VNC ni inferencia facturada; consulta del catálogo público de modelos en el startup del worker.
- Ruff y `git diff --check` pasan. `python3 scripts/check_public_files.py`: sin coincidencias privadas detectadas.

No se ha desplegado en Dokploy ni verificado HTTPS público. El usuario conserva la
responsabilidad del despliegue externo. Los contenedores y volúmenes desechables del
smoke se eliminaron; la configuración local privada queda ignorada.

## Ajuste del plazo solicitado por el usuario

El corte vigente es a +290 s desde recepción (4:50), incluyendo cola y conexión.
El worker cancela el grafo, el gateway bloquea nuevas acciones y cierra el dueño;
solo después se publica el resultado terminal. Objetivo de cierre +295 s; límite
externo +300 s. Sustituye los márgenes anteriores +268/+270/+285 de las specs.
Las tareas existentes conservan su cutoff persistido: no se amplía su presupuesto.
Variables de Dokploy: TASK_SECONDS=300, ACTION_CUTOFF_SECONDS=290,
DRAIN_SECONDS=290, CLOSE_TARGET_SECONDS=295. Si fallan DB o confirmación de cierre,
el terminal puede demorarse: este ajuste no garantiza recuperación tras crash.
