# Evidencia inicial de plataforma — P01/P02

Implementado: 10 tablas SQLAlchemy, Alembic 001–003, admisión atómica task/credenciales/outbox, Fernet y destino HMAC, claim exclusivo, reserva por advisory lock e índice parcial, cierre protegido, eventos secuenciales y evidencias con FK compuestas. API bearer con errores sin inputs privados, body limitado, health readiness y estados del contrato. Redis Streams con dispatcher durable, recuperación de pendientes y redelivery sin repetir claim.

La autoridad/supervisor debe aportar evidencia real de cierre; la base de datos no demuestra por sí sola que se cerró un socket. Los tests de esta capa usan evidencia sintética. El grafo/VNC, aplicación oculta y las cinco pruebas del organizador tienen gates separados.

## Evidencia ejecutada

El 10 de octubre de 2026 pasaron **34 pruebas** de contrato e integración, con PostgreSQL 17 y Redis 7 reales, contenedores desechables. Ninguna prueba accedió al escritorio de práctica ni leyó `.env.vnc`.

Se verificaron rollback, carreras de claim/reserva/finalización, lease vencido sin takeover, cifrado, secuencias concurrentes, referencias cross-run, terminal antes de cierre e inmutable, propuesta repetida/vencida/antigua/coordenadas, expiración en cola, reaper de queued y claim perdido marcado uncertain sin fingir cierre/replay, reinicio de repositorio, migrations desde versión previa conservando datos y equivalencia metadata/DB. También se verificaron redelivery, recuperación de pendientes, caída antes/después de publicación, auth, validación sin reflejar secretos, admisión durable <2 s y readiness por heartbeat.

```sh
TEST_DATABASE_URL='<DSN PostgreSQL desechable con nombre de DB que contenga test>' TEST_REDIS_URL='<URL Redis de pruebas>' .venv/bin/python -m pytest tests/contract/test_api.py tests/integration/test_platform.py -q --tb=short
.venv/bin/ruff check app/persistence app/api app/queue migrations tests/contract/test_api.py tests/integration/conftest.py tests/integration/test_platform.py
python3 scripts/check_public_files.py
```

Las fixtures migran y limpian tablas de la DB explícita de pruebas; nunca configurar una DB de producción. Redis usa stream único por test. No utilizar estos comandos sobre servicios que atienden tareas reales.

## Privilegios y bootstrap

`python -m app.persistence.grants` requiere `GRANTS_DATABASE_URL` de administrador con permiso de crear roles y otorgar grants. Ejecutar tras Alembic y bootstrap de memoria. Crea grupos NOLOGIN fijos `frontier_api`, `frontier_worker`, `frontier_authority`, `frontier_dispatcher`, `frontier_supervisor` y `frontier_auditor`; las identidades LOGIN y sus secretos se gestionan en el entorno de despliegue.

Worker independiente puede leer tablas app y escribir eventos, pero no leer payload cifrado private ni borrar eventos. Authority sí puede leer payload cifrado. El servicio worker del demo combina autoridad/supervisor: su login requiere los grupos relevantes. Esto no proporciona aislamiento de procesos equivalente a autoridades separadas; ningún modelo recibe acceso DB. Usar credenciales del propietario de tablas para todos los servicios tampoco prueba separación de roles en producción.

Readiness de API exige DB, Redis, heartbeats de worker/gateway/supervisor <10 s y tablas nativas `graph_checkpoints.checkpoints`/`graph_store.store`. La admisión no abre VNC ni llama a un modelo. El destino recibido se conserva; API rechaza localhost/IP no global por defecto. La resolución DNS y validación del peer real corresponden al transporte y deben probarse allí.

## Comandos de servicios

```sh
.venv/bin/uvicorn app.api.main:app --host 0.0.0.0 --port 8000
.venv/bin/python -m app.queue.main
```

Entorno: `DATABASE_URL`, `REDIS_URL`, `TEAM_ENTRYPOINT_TOKEN`, `CREDENTIALS_ENCRYPTION_KEY`, `DESTINATION_HMAC_KEY`, `TEAM_ID`. La configuración no carga automáticamente `.env.vnc`. No imprimir errores DB/Redis crudos: pueden incluir datos privados.

## Pendientes antes de aceptar paquetes completos

P01/P02 no equivalen todavía a release aprobado. La integración posterior ya probó bootstrap/checkpoints y POST→cola→grafo→captura sintética→cierre→GET; ver [avance inicial](avance-inicial.md). Faltan GC/retención completa y presupuesto de pools con réplicas; cierre de runs perdidas tras crash del consumidor reclamado (supervisor); pruebas reales de dependencia caída/restaurada durante tareas; smoke visual y E1–E5. No inferir éxito del agente visual a partir de los tests de plataforma.
