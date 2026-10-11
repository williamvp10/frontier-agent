# Evaluador CLI de práctica: uso, reportes y límites

`scripts/evaluate.py` implementa la CLI del [plan de evaluación](specs/evaluador-practica-cli.md).
`scripts/evaluation_support.py` concentra el lock/journal local, correlación de trazas y
reportes. El evaluador usa HTTP real en `--execute`; no simula al agente ni sus resultados.
HTTP simulado y reloj fake corresponden al agente responsable de las pruebas.

**Evidencia de esta entrega:** revisión de código y Ruff, sin ejecutar tests, CLI,
reset/check, tareas, modelos ni conexiones VNC, por instrucción del usuario.
No acredita E1–E5 ni aprobación de los nuevos comportamientos en tests.

## Selección y planificación sin red

```sh
.venv/bin/python scripts/evaluate.py --list
.venv/bin/python scripts/evaluate.py --cases E1,E3 --repetitions 1 --dry-run
.venv/bin/python scripts/evaluate.py --all --repetitions 3 --dry-run
```

Estos modos leen sólo las fixtures externas: no necesitan entrypoint, dotenv, credenciales
ni servicios, y no crean reportes/locks. Listar muestra IDs, no instrucciones ni expectativas.
`--cases` respeta el orden indicado; `--all` usa el orden estable de archivos. Ambos son
excluyentes. Se valida todo el catálogo antes de admitir: IDs desconocidos, duplicados,
selección vacía, fixtures inválidas y repeticiones fuera de 1–10 abortan sin reset.

La iteración es por repetición y luego caso: E1/1, E3/1, E1/2, E3/2. Tres repeticiones son
el valor por defecto. Ya no hay ejecución implícita de toda la carpeta al omitir selección.

## Separación de responsabilidades

Las fixtures originales permanecen en `../frontier-reto-1/pruebas/`, fuera del repo público.
El agente recibe exclusivamente `instruccion` como `tarea` y URL/contraseña en `escritorio`.
Expectativas, diffs, lista blanca y respuestas **solo** se envían al endpoint de chequeo del
harness. No pasan a grafo, prompts, memoria o eventos de agente.

- Team token: `/health`, `/tasks`, `/tasks/{id}` de nuestro entrypoint.
- Desktop token: `/__reset`, `/__check` del escritorio de práctica.
- Password: exclusivamente payload de admisión que el servicio cifra para abrir VNC.

El harness verifica que origen WSS y origen del control de escritorio coincidan. No sigue
redirecciones HTTP ni vuelve a enviar bearer a otro destino. Configuración y excepciones
nunca se imprimen completas. `--env-file` carga dotenv localmente, sin volcar sus valores.

## Secuencia y resultados

Cada caso: confirmar salud del servicio → journal de reset → reset confirmado → salud del
escritorio y espera acotada → journal de admisión → POST → guardar task_id → GET cada cinco
segundos → terminal → chequeo de app → guardar resultado. No se reintentan POST ni reset
inciertos. Polling/HTTP tienen timeout limitado por el deadline desde envío de POST.

`GET /health` del escritorio y una espera de estabilización **no prueban readiness visual**:
el runtime obtiene la primera captura. Se registra `desktop_ready_basis=desktop_health_plus_settle`;
la espera por defecto es 2 s, con presupuesto propio de 20 s, configurable con
`--desktop-settle-seconds` y `--desktop-ready-timeout` (máximo 60 s).

Se exige estado esperado, formato/valor de `respuesta:`, primer número correcto, orden con
punto y coma cuando hay dos datos, claves de razón y todos los chequeos de app. Una tarea
`fallida` con una línea de respuesta inventada falla aunque `respuesta_esperada` sea null.
`__check` vacío, mal tipado o contradictorio entre `pass` y los checks es `ERROR`.
Sólo nombres reconocidos se conservan; los demás son `check_001`, etc.

Resultados del evaluador:

| Resultado | Significado |
|---|---|
| PASS | Todos los oráculos locales y de aplicación pasan |
| FAIL | Se evaluó y algún oráculo no pasa |
| ERROR | Infraestructura/protocolo/interrupción impidió evaluar |
| SKIPPED | Caso planificado no iniciado tras un bloqueo/error/interrupción |

E5 puede devolver `fallida` y ser `PASS`: resultado del evaluador y estado público son distintos.
Un `FAIL` con terminal confirmado por contrato permite continuar; incertidumbre detiene nuevas
admisiones/resets y materializa los casos restantes como `SKIPPED`.

Por defecto stdout contiene sólo IDs, estados, tiempos, checks controlados y correlación
Langfuse segura. El reporte normal no guarda resumen, instrucción, fixture, valores esperados,
imágenes, endpoints de servicio/escritorio ni tokens. Todos los reportes/JSONL deben quedar
en `.private/`. `--private-details` habilita un archivo separado `.details.jsonl` con instrucción
y resumen redactados; puede contener datos de negocio y no debe publicarse.

La seguridad para el siguiente reset depende del contrato del runner: **solo publica terminal
tras confirmar cierre del gateway**. El CLI no observa el wire RFB. Por eso registra
`closure_basis=api_terminal_contract`, pero `closure_confirmed` y `actions_after_close` quedan
null sin evidencia independiente. No confunde tiempo de detección con cierre exacto a +285 s.

## Exclusividad, interrupción y reconciliación

Coordinar la reserva exclusiva con el integrador y declarar `--exclusive-desktop`. El flag
es una declaración de reserva externa, no una detección de clientes VNC. El lock local POSIX
por origen del escritorio evita invocaciones concurrentes **desde este mismo checkout**;
otro host, checkout o cliente externo requiere coordinación aparte.

El journal vive en `.private/evaluaciones/state/`. Su nombre usa HMAC con clave aleatoria
local; no contiene el endpoint. Guarda operación/suite/caso/repetición/task_id, fase y hash
de la autoridad de API. Se escribe atómicamente y con fsync **antes** de reset/admisión.
No cambiar el directorio de estado ni borrar su clave/journal para saltarse un bloqueo.

Ctrl-C/SIGTERM detienen nuevas operaciones, conservan el bloqueo cuando hay incertidumbre y
guardan el reporte parcial con pendientes. Cerrar HTTP **no cancela** la tarea remota.
Si hay task_id conocido, reconciliar por lectura del API terminal, sin reset/check/admisión:

```sh
.venv/bin/python scripts/evaluate.py --entrypoint https://agent.example \
  --reconcile --exclusive-desktop
```

Sólo un terminal válido de la misma autoridad permite limpiar el bloqueo; `en_curso`, errores,
404 o un entrypoint cambiado lo conservan. Si no se recibió task_id, no hay forma de resolver
una aceptación incierta con la API pública: la CLI conserva la operación y exige reconciliar
ownership/cierre con el integrador fuera de este comando. No ofrece un `--force-reset`.

## Comandos

La ejecución real requiere que API/DB/Redis/worker/gateway/supervisor estén saludables y que
hayan pasado sus pruebas de cierre. La clave y datos se configuran privadamente en `.env.vnc`.
El entrypoint se proporciona con `--entrypoint` o `TEAM_ENTRYPOINT_URL` en configuración local.
No poner tokens en CLI. Las variables de entorno prevalecen sobre dotenv.

```sh
.venv/bin/python scripts/evaluate.py --entrypoint https://agent.example \
  --cases E3 --repetitions 1 --execute --exclusive-desktop

.venv/bin/python scripts/evaluate.py --entrypoint https://agent.example \
  --cases E1,E2,E5 --repetitions 1 --execute --exclusive-desktop

.venv/bin/python scripts/evaluate.py --entrypoint https://agent.example \
  --all --repetitions 3 --execute --exclusive-desktop \
  --report .private/evaluaciones/suite.json --events .private/evaluaciones/suite.events.jsonl
```

Estos son ejemplos ficticios, no ejecuciones realizadas. `--fixtures` conserva el soporte
de carpetas externas alternativas; no introducir esos archivos en el repo público.
Exit codes: 0 para éxito/plan/listado/reconciliación; 1 para FAIL; 2 para ERROR/configuración;
130 para SIGINT y 143 para SIGTERM. `--execute` y selección explícita son obligatorios.

## Reporte v2 y métricas

El default es `.private/evaluaciones/latest.json`, ahora un objeto de suite con
`schema_version=2`, plan, `current_case`, `reports`, cuentas PASS/FAIL/ERROR/SKIPPED,
total planificado/ejecutado, timestamps y duración total. El snapshot se actualiza en eventos
y antes del caso siguiente; cada reemplazo es atómico, con archivos 0600. Se archiva el
snapshot anterior antes de reemplazarlo. JSONL/detalles existentes no se sobreescriben:
usar rutas nuevas por suite. El directorio del journal está reservado, no sirve para reportes.

Cada caso incluye suite_id, case_id, repetition, task_id y run_id nullable, resultado público,
resultado del evaluador, checks desglosados, correlación, error fijo y métricas independientes:

| Campo | Medición |
|---|---|
| reset_duration_seconds | Petición reset, incluso si falla |
| desktop_ready_wait_seconds | Salud/estabilización posterior al reset |
| acceptance_seconds | Petición POST completa, incluso si su respuesta es incierta |
| terminal_detected_seconds | Desde envío POST hasta recibir terminal válido |
| duration_seconds | Alias de terminal_detected_seconds; no es case_wall_seconds |
| task_observation_seconds | Tiempo observado desde POST; conserva duración también en timeout/error |
| check_duration_seconds | Petición __check, incluso si falla |
| case_wall_seconds | Caso completo, incluyendo preflight, reset, espera y check |
| suite_wall_seconds | Tiempo monotónico de la suite CLI |

Métricas no observadas quedan null. Cola, ejecución backend, coste y wire permanecen
desconocidos en esta entrega, no se derivan del polling. Agregados globales y por caso tienen
número de muestras, faltantes, mediana y máximo, incluyendo FAIL/ERROR. No se calcula P95
con tres muestras ni se descartan timeouts silenciosamente.

## Correlación Langfuse sin exportación duplicada

`TraceResolver` usa la operación estática del SDK `create_trace_id(seed=str(task_id))`,
igual que el backend. No inicializa un cliente, crea trazas/spans, consulta coste ni espera
ingestión. Siempre registra `pending` cuando puede resolver el ID; no afirma que la traza
ya exista. Sin SDK disponible conserva task_id y estado `unavailable`.

Para enlace, configurar localmente `LANGFUSE_BASE_URL` y `LANGFUSE_PROJECT_ID`, opcionalmente
con `--langfuse-env-file .env`. No requiere claves Langfuse para correlacionar. Sólo imprime
enlaces de los orígenes públicos US/EU conocidos, con la ruta de proyecto/traza documentada;
un host autogestionado o proyecto ausente mantiene sólo trace_id/task_id.

Coste no descargado = `cost_usd=null`, `cost_status=unavailable`; consultar generaciones
OpenRouter/Jev en el tablero. No copiar coste de generaciones a la raíz ni sumar ambas capas.
`evaluation_pass` en el reporte es true/false para PASS/FAIL y null para ERROR/SKIPPED.
**Propuesta al propietario Langfuse:** exportar un score BOOLEAN separado `evaluation_pass`,
asociado a la traza existente con IDs/checks seguros e idempotencia por suite/caso/repetición.
Su exportación no está implementada aquí y no se ha alterado `task_success` del runtime.

## Evidencia pendiente

La suite existente es el punto de partida del otro agente; no se ejecutó ni modificó en esta
entrega. Para adaptar sus fixtures HTTP: declarar `exclusive_reserved=True` sólo en el contexto
simulado, responder a `/health` de ambos destinos, contemplar la espera posterior a reset,
los nuevos checks y los casos SKIPPED explícitos. El reloj/sleep/UTC siguen siendo inyectables.
Ver [matriz de aceptación](specs/evaluador-practica-cli.md#9-pruebas-de-aceptación-del-desarrollo).

Comando estático ejecutado:

```sh
.venv/bin/ruff check --no-cache scripts/evaluate.py scripts/evaluation_support.py
```

Resultado: `All checks passed!`. Pendientes del responsable de validación: HTTP/reloj fake,
canarios en todos los sinks, locking/reinicio/interrupción, correlación y, después de reserva
exclusiva y gates de cierre, E3 real antes de una suite E1–E5 x3. No interpretar el código
implementado ni una revisión estática como evidencia de tiempos o tareas reales aprobadas.

También se ejecutaron `python3 scripts/check_public_files.py` (130 candidatos revisados,
sin coincidencias privadas detectadas) y `git diff --check` (sin errores). El scan procesa
comparaciones localmente; no se volcó configuración privada al contexto ni a stdout.

## Validación dinámica posterior — 10 de octubre de 2026

El responsable de validación ejecutó HTTP simulado, reloj controlado, CLI offline
y journals/reportes en directorios temporales, sin operar el escritorio ni crear
tareas reales. Se detectó y corrigió una comparación `None == None` que rechazaba
el reporte por defecto sin `--events`/`--private-details`.

```sh
.venv/bin/python -m pytest tests/unit/test_harness.py tests/unit/test_evaluator_cli.py -q --tb=short
```

Resultado: **58 passed in 0.51s**. Incluye selección/orden, ejecución CLI completa
con defaults y MockTransport, PASS/FAIL/ERROR, E5 fallida→PASS, bloqueo persistente,
locks, falta de clave HMAC, interrupción, reconciliación de lectura, reportes
incrementales/0600/archivo previo, métricas separadas, telemetría opcional y canarios.

Verificación ampliada:
`.venv/bin/python -m pytest tests/unit tests/contract tests/infra/test_compose.py -q --tb=short`
→ **268 passed in 9.26s**. Ruff de `app tests scripts`, `git diff --check` y scan
público pasan. El listado real mostró E1–E5; los dry-runs planificaron 2 y 15 casos,
sin configuración privada/red/locks. Pasos manuales completos en el README.

No se ejecutó `--execute` contra servicios reales en esta validación, ni reset/check
real, modelos pagados o VNC. La suite real E3/E1–E5 requiere servicio saludable,
reserva exclusiva y revisión del resultado/cierre conforme a las secciones anteriores.
