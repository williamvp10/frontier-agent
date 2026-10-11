# Integración OpenRouter por rol y contexto v2

10 de octubre de 2026. Implementación conectada al worker y verificada por capas.
No equivale a aceptación de E1–E5, generalidad de la app desconocida o release.
Se reutilizan los contratos de [C1/C2](contexto-implementado.md).

## Implementación

- `app/models/profiles.py`: perfiles congelados por rol; modelo, timeout, límite
  de salida, razonamiento, routing, prompt version y graph version. Overrides en
  `MODEL_PROFILES`, JSON validado. IDs fijos; sin `:free`, `latest` ni router abierto.
- `app/core/config.py`, `app/models/factory.py`, `infra/compose.yaml` y
  `.env.example`: configuración por rol conectada. Settings no carga `.env`
  automáticamente. Los launchers smoke realizan carga explícita privada.
- La factory usa exclusivamente OpenRouter y fija perfiles al construir el
  router. El worker valida el catálogo público (imagen y salida estructurada)
  antes de declarar readiness y registra un perfil seguro por run.
- `app/models/openrouter_visual.py` y el encoder compartido
  `app/models/openai_visual.py`: Chat Completions, schema estricto, cero retries,
  provider sort latency, require_parameters true y allow_fallbacks false.
  El constructor de transporte directo del fabricante está deshabilitado.
  Sonnet no recibe parámetros de razonamiento OpenAI. Metadatos de uso/coste son
  escalares permitidos; las respuestas crudas e imágenes son transitorias.
- `app/models/jev_advisor.py`: transporte anterior retirado. Usa la clave
  OpenRouter y `https://openrouter.ai/api/alpha/decisions`, resolviendo la ruta
  contra el origen, nunca contra `/api/v1`. No hereda routing visual ni parámetros
  de chat. Conserva guards, cuotas, deduplicación, deadline y adaptación interna.
- `app/models/advice.py` y `app/agents/advice_context.py`: coste/uso tipados y
  reconciliaciones históricas con evidencia. Limpiar pending no elimina del
  paquete de advice la resolución `applied|not_applied` ni sus fuentes.
- `app/models/turns.py`, `app/agents/graph_v2.py`, `runtime.py`, `graph.py`,
  `app/models/prompts.py` y `app/worker/runner.py`: contrato/hitos, asignación activa,
  patches y slices por rol, operador por hito, verificación y recuperación,
  advice → mismo solicitante → FinalDecision. El grafo baseline v1 se mantiene
  para compatibilidad explícita; el worker configura `GRAPH_VERSION=context-v2`.
- `app/desktop/executor.py`: recuperación de screenshots históricos sólo por
  referencias registradas en el propio adapter/run. Verifier recibe antes/después
  transitoriamente cuando corresponde, sin paths o base64 en el estado.
- `scripts/smoke_model.py`, `smoke_jev.py`, `smoke_graph_v2.py` y
  `smoke_live_query.py`: gates pagados/sintéticos y consulta VNC real cero-input.
  El último exige DB aislada y bloquea comandos distintos a observe en el gateway.

### Autoridad y compatibilidad C1/C2

Se mantienen `ContextService.apply(snapshot, patch, *, actor_role)` y
`project(snapshot, *, role)`. El runtime serializa con un lock por run y conserva
el último snapshot aceptado, evitando aplicar dos patches contra copias viejas.
Los modelos no escriben observaciones, generaciones, deadline, presupuesto,
estados de envío ni pendientes; los schemas de turnos no exponen esas escrituras.

Extensiones limitadas del reducer:

1. Runtime puede registrar interpretación semántica del mismo frame, manteniendo
   exactamente ID/revisión/timestamp/generación; los controles deben referenciar
   evidencia/vistas válidas. Esto no equivale a una nueva observación.
2. Runtime puede disminuir cuotas de la misma asignación, sin cambiar su identidad,
   acciones, campos, hito o scope. La inferencia consume cuotas; los presupuestos
   iniciales propuestos se ajustan hacia abajo si consumieron tiempo al generarse.
3. La confirmación de envío del mismo intento permite `uncertain → sent` por
   runtime, conservando pending. No significa efecto confirmado ni replay.

Sólo Verifier crea/eleva hechos verificados, satisface criterios y reconcilia
operaciones con evidencia posterior. El grafo registra incertidumbre antes del
nodo ejecutor y checkpoint antes de enviar; el adapter conserva intención durable
previa a RFB. Una capacidad efímera no checkpointed evita ejecutar un intento
restaurado. Todas las propuestas siguen pasando por verificador/gateway.

Una generación nueva requiere reobservación, invalida localizaciones y marca
memoria afectada stale conservando procedencia/rutas. El resultado exitoso exige
todos los criterios verificados y ningún pending. La coordinación determinista
final registra hitos completados; el runner confirma cierre antes de publicar.

**No hay resume ni migración automática.** Un checkpoint existente, incluyendo
v1, provoca `checkpoint_requires_reconciliation`, sin sobrescribirlo o ejecutar
acciones. Drenar runs antes de cambiar versión. El restablecimiento operativo
después de crash/rotación de sesión sigue necesitando coordinación y reconciliación
externa probadas; no se presenta como crash recovery resuelto.

### Diferencia de schema detectada con proveedor real

Sonnet aceptó el smoke visual básico, pero rechazó con HTTP 400 el schema amplio
del verificador que incluía escrituras de mapa ajenas a su rol. Se separó
`VerifierPatch`: sólo envelope y actualizaciones de hechos; las afirmaciones y
criterios salen del reporte tipado y el runtime construye el patch validado.
No se relajó strict ni se activó otro proveedor. Los errores se diagnostican con
códigos/rasgos estáticos de schema, sin imprimir el cuerpo del proveedor.

## Evidencia ejecutada

Raíz de `frontier-agent`, `.venv` existente. PostgreSQL **16.13-bookworm** y Redis
**7.4.8-bookworm** creados para este corte en contenedores desechables con puertos
de loopback y DB con nombre test. Sin apuntar a DB de usuario/producción.

### Pruebas locales y dependencias reales

```sh
# TEST_DATABASE_URL, TEST_REDIS_URL y BOOTSTRAP_TEST_DATABASE_URI
# inyectadas exclusivamente para los servicios desechables de este corte.
.venv/bin/python -m pytest -m 'not e2e and not release' -q --tb=short
.venv/bin/ruff check app tests scripts
python3 scripts/check_public_files.py
```

Ejecución intermedia: **254 passed, 4 warnings in 29.54s**, sin skips. Incluye
PostgreSQL/Redis, bootstrap/migraciones, cola durable, grafo v1/v2 con modelos
sintéticos, checkpoints PostgreSQL, cierre de proceso dueño, RFB/WSS sintético,
contratos, scopes, cuotas, aislamiento y canarios. Los cuatro warnings corresponden
a deprecación TripleDES del upstream AsyncVNC; no se cambiaron dependencias para
ocultarlos. Verificación final después del bookkeeping de hitos: se registra al
final de este documento.

Los tests propios de grafo prueban también incertidumbre sin replay, imágenes
antes/después transitorias, respuesta del consejo al mismo operador, timeout
opcional, reset/relocalización, prohibición de scope ampliado y cuota de hito.
La regresión del bridge verifica evidencia histórica después de resolver pending.

### Modelos reales sobre imágenes sintéticas

```sh
.venv/bin/python scripts/smoke_model.py --env-file .env --role operator
.venv/bin/python scripts/smoke_model.py --env-file .env --role verifier
.venv/bin/python scripts/smoke_model.py --env-file .env --role recovery
.venv/bin/python scripts/smoke_jev.py --env-file .env
.venv/bin/python scripts/smoke_graph_v2.py --env-file .env
```

Resultados reales, una ejecución aprobada por gate:

| Gate | Modelo | Resultado / duración |
|---|---|---|
| Imagen + schema operator | openai/gpt-6-luna | Aprobado; 2,35 s |
| Imagen + schema verifier | anthropic/claude-sonnet-5.5 | Aprobado; 3,88 s |
| Imagen + schema recovery | openai/gpt-6.1-sol | Aprobado; 3,30 s |
| Jev Decisions textual | typesafe/jev-1.13-20260917 | HTTP/schema aprobados; 0,64 s |
| Grafo v2, reporte sintético | Coordinator → Operator → Verifier | Terminada, cero acciones; 19,22 s |

Uso/coste reportado por OpenRouter en el recorrido sintético aprobado:

| Rol | Tokens entrada | Tokens salida | USD reportados |
|---|---:|---:|---:|
| Coordinator | 3333 | 781 | 0,00080705 |
| Operator | 5891 | 480 | 0,00097630 |
| Verifier | 6963 | 415 | 0,01807600 |

Total reportado: **USD 0,01985935**. No se estima por tarifas de catálogo ni se
suma de nuevo razonamiento. No es benchmark mediana/p95 ni coste por tarea real
de negocio. Jev smoke verifica transporte y shape, no autoridad o precisión.

Hubo intentos fallidos documentados por resultado, no ocultados como passing:
carga literal de booleanos de entorno (corregida con regresión), schema amplio
Sonnet rechazado, presupuesto propuesto anterior al tiempo de inferencia y formato
del oráculo sintético. El oráculo final verifica el primer número de `respuesta:`
según el contrato público, sin alterar la respuesta del modelo silenciosamente.

### Consulta VNC real autorizada

El usuario confirmó escritorio libre y exclusivo antes de la prueba. Se consultó
únicamente el título visible, prohibiendo navegación y excluyendo credenciales,
URLs y datos de registros del resultado. `max_actions=0`, Jev apagado, exportación
externa apagada explícitamente y gateway read-only como segunda barrera.

```sh
# TEST_DATABASE_URL corresponde sólo al PostgreSQL desechable ya inicializado.
.venv/bin/python scripts/smoke_live_query.py --model-env-file .env --vnc-env-file .env.vnc
```

Resultado: **terminada / verified**, **0 intentos de acción**, **1 observación**,
**owner_stopped=true**, **22,40 s**. Credenciales leídas sólo programáticamente
por transporte; respuesta y capturas no impresas ni publicadas. Capturas privadas
en `.private/artifacts/` y recibo sanitizado en `.private/evidencias/`. Escritorio
liberado después de confirmar cierre. No se ejecutó reset/check ni tarea mutante.

## Límites y siguientes gates

- `JEV_ENABLED=false` permanece como default. Transporte real aprobado;
  combinación Jev + tarea real de negocio y comparación con/sin consejo pendientes.
- E1–E5 ×3 requieren autorización para tareas mutantes/reset y un harness aislado
  con expectativas fuera del contexto del agente. La autorización recibida en
  este corte fue para la consulta cero-input; no se extiende a esos casos.
- Segunda app, variantes adversariales y benchmark MC10 pendientes; fanout/analyst
  y bypass de verificador no se habilitan.
- Recuperación de cierre después de crash del worker, GC/retención y R05 siguen
  siendo gates de release; no habilitar réplicas de worker todavía.
- Estimación de tokens, compacción suficiente y escalamiento automático dentro de
  un rol no están implementados. Recovery usa el perfil separado Sol. Los límites
  de slices son bytes y fallan explícitamente sin truncar datos críticos.
- No hay despliegue público confirmado. Dokploy lo realiza el usuario; HTTPS,
  readiness, POST <2 s desde internet y plazo total requieren prueba externa.
- Cambios de observabilidad Langfuse llegaron concurrentemente de otro agente y
  se preservaron; su evidencia pertenece a `observabilidad-langfuse.md`. Este
  corte no activa exportación en sus smokes ni atribuye esos cambios como propios.

No hubo stage, commit, publicación ni despliegue. Los servicios de test de este
corte se retiran después de la verificación final; no se tocan contenedores ajenos.

## Cierre de verificación

- Última suite completa local, con PostgreSQL/Redis desechables:
  **257 passed, 4 warnings in 31.92s**, sin skips.
- `.venv/bin/ruff check app tests scripts`: **All checks passed!**
- `python3 scripts/check_public_files.py`: **OK: 130 archivos revisados; sin
  coincidencias privadas detectadas** en la última ejecución registrada.
- Los dos contenedores propios de prueba fueron retirados por sus IDs exactos.
- La reserva VNC de la consulta fue liberada con cierre confirmado; no hubo
  tareas mutantes ni reset posterior.

Durante la verificación global aparecieron siete fallos por actualización
concurrente del harness (reserva explícita/readiness y casos SKIPPED). Se ajustaron
sólo las fixtures de `tests/unit/test_harness.py`, conservando las barreras del
harness y usando exclusivamente MockTransport. Se añadió la regresión de ausencia
de reserva → cero requests/resets. La suite completa posterior pasa; esto no es
autorización ni evidencia de reset contra el escritorio real.
