# Desarrollo inicial: implementación y evidencia

El servicio ya tiene FastAPI autenticado, admisión durable PostgreSQL/outbox, Redis Streams, worker, grafo con orquestador/operador/verificador/recuperador, gateway VNC aislado en proceso y cierre previo al estado terminal. Las diez tablas propias usan SQLAlchemy/Alembic; checkpoints/store nativos tienen bootstrap y esquemas separados.

## Evidencia y límites

Suite integrada del 10 de octubre de 2026: **109 pruebas pasaron** con PostgreSQL/Redis desechables, incluyendo adaptadores OpenRouter/OpenAI con HTTP simulado. Ruff y scan público pasan. Cuatro advertencias de deprecación DES provienen de AsyncVNC; cryptography está acotado por compatibilidad. No hubo inferencias reales.

- Integración usa PostgreSQL/Redis reales: POST autenticado → outbox → cola → claim → grafo → captura sintética en proceso aislado → checkpoints → cierre confirmado → GET terminal. Modelo y framebuffer son sintéticos; esto demuestra integración, no exactitud visual en SIGECOM.
- Transporte sintético WSS/RFB con TLS verificado prueba autenticación, framebuffer, Unicode, pulsaciones prolongadas, cierre y rechazo de destinos inseguros.
- Prueba real autorizada de sólo captura: escritorio 1280×800 y proceso dueño detenido al finalizar. Captura privada, sin modificación de datos.
- Grafo mantiene hechos verificados acotados y acciones resumidas por run; verifica propuestas y no repite guardados inciertos. Las delegaciones quedan relacionadas por eventos padre.
- Docker/Compose y CI están preparados. El usuario hará el despliegue Dokploy; aún no hay evidencia de ingress público ni réplica R05.

La prueba visual real requiere configurar las claves localmente. E1–E5 y sus 15 repeticiones siguen pendientes. Un worker terminado por fuerza puede dejar una sesión `uncertain`: no se reasigna sin evidencia del cierre del dueño anterior. Retención/GC operativa, multiworker y pruebas de carga quedan pendientes.

## Configuración local de modelos

`.env` existe, está ignorado por Git y tiene permisos 0600. Completar `OPENROUTER_API_KEY`. Único proveedor visual: `MODEL_PROVIDER=openrouter`, `MODEL_NAME=openai/gpt-6.1-sol`. Jev queda como integración futura separada.

No publicar el archivo ni copiar las claves a chats. El backend lee variables del proceso; el smoke admite explícitamente el archivo local:

```sh
uv sync --frozen --extra dev --extra notebook
.venv/bin/python scripts/smoke_model.py --env-file .env
python3 scripts/check_public_files.py
```

Los servicios Compose reciben las variables por servicio desde Dokploy. `.env.vnc` conserva las credenciales del escritorio de práctica; sólo el harness usa reset/check.

## Reparto y siguiente corte

Plataforma implementó API/DB/cola; grafo implementó especialistas/adaptadores/harness; infraestructura implementó contenedores/bootstrap/CI/wire tests; integrador ensambló dominio, seguridad, gateway y runner. No repetir estos scaffolds con otro agente.

Siguiente encargo para otro agente: preparar y ejecutar la matriz E1–E5 con el harness cuando el modelo real esté disponible, registrar tiempos/resultado/errores sanitizados y crear regresiones sintéticas para cada fallo. Leer `AGENTS.md`, `specs/pruebas.md` y `evaluaciones.md`. Coordinar cualquier edición de desktop/grafo con el integrador; no resetear un escritorio activo, compartir expectativas con el agente ni modificar entregables aprobados.

## Repetir la suite integrada

Configurar `TEST_DATABASE_URL`, `TEST_REDIS_URL` y `BOOTSTRAP_TEST_DATABASE_URI` contra servicios desechables. La DB debe incluir `test` en su nombre: las fixtures borran tablas propias.

```sh
.venv/bin/python -m pytest -q --tb=short
.venv/bin/ruff check app tests scripts
```

El smoke del modelo se intentó con `.env`; se detuvo antes de inferencia por clave principal vacía. No es una prueba de capacidad del proveedor.

Cambio posterior de especificación: se retiró el respaldo OpenAI directo. El conteo 109 anterior es evidencia histórica, no el total de la suite actual. Ver contrato-entrypoint y planning para la revisión vigente.
