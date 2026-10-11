# P04-C1/C2 — contexto y memoria implementados

Fecha: 10 de octubre de 2026. Estado: contratos y servicio puro implementados y
verificados con evidencia sintética local. Interfaces disponibles para revisión
del integrador. El grafo v2 todavía no está integrado por este paquete.

## Archivos e interfaces

Ownership aplicado exclusivamente a:

- `app/agents/context.py`: modelos Pydantic congelados, `extra="forbid"`, tuplas
  acotadas, IDs UUID, timestamps aware y validación de referencias del run.
- `app/agents/context_service.py`: reducer puro, proyecciones y errores tipados.
- `tests/unit/test_context.py`: contratos, límites, aislamiento y seguridad.
- `tests/unit/test_context_service.py`: MC01–MC03, permisos, vigencia, progreso,
  pendientes y cobertura de proyecciones.
- `docs/02-ejecucion/contexto-implementado.md`: esta evidencia sanitizada.

Contratos principales: `SharedTaskContext`, `TaskContract`, `CurrentUI`, `Fact`,
`Hypothesis`, `PartialAppMap`, `ViewNode`, `TransitionEdge`, `FrontierCandidate`,
`Milestone`, `MilestoneAssignment`, `ActionRecord`, `PendingOperation`, `Progress`,
`Budgets`, `ContextPatch` y `ContextSlice`.

Contratos auxiliares: `EvidenceReference`, `Provenance`, `FactUpdate`,
`VisibleControl`, `BoundingBox`, `Identifier`, `SuccessCriterion`,
`CriterionProgress`, `AssignmentBudget`, `RoleCalls`, `PendingResolution` y
`AdviceReference`. Este último reserva únicamente una referencia; no implementa
Jev ni contiene una recomendación, evidencia o autorización.

```python
EvidenceReference.from_observation(observation: Observation, *, ui_generation: int)

ContextService(max_projection_bytes=65536, recent_actions=10)
ContextService.apply(snapshot: SharedTaskContext, patch: ContextPatch,
                     *, actor_role: str) -> SharedTaskContext
ContextService.project(snapshot: SharedTaskContext, *, role: str) -> ContextSlice
```

`Observation` y `ActionProposal` siguen siendo los contratos existentes de
`app.domain.contracts`. La referencia de evidencia contiene metadatos de una
observación, sin bytes, hashes usados como identidad de vista, rutas de archivos
ni URLs. El ID de `ActionRecord` referencia una propuesta/intento existente; no
duplica sus argumentos. Una propuesta para el verificador se aporta por separado
con `ActionProposal` al integrar el consumidor.

### Corte 1

Se presentaron contratos e interfaces antes de conectar consumidores. La primera
verificación de contratos produjo **12 passed** y Ruff limpio. El corte 2 añade
la reconciliación tipada y vínculos a hechos verificados para confirmar efectos;
las dos firmas propuestas del servicio se mantienen. Los nombres se documentan
aquí para revisión antes de integrar grafo o adapter de consejo.

## Reducer y autoridad

Cada patch declara run, sesión, epoch, versión base, observación, revisión del
frame y generación. `apply` valida el envelope exacto, permisos de campos,
procedencia, evidencia y referencias, y construye un snapshot nuevo con versión
incrementada. Un rechazo conserva intacto el snapshot original. No hay acceso a
reloj, escritorio, DB, red, logs ni persistencia.

Roles aceptados para escritura: `coordinator`, `operator`, `verifier`, `recovery`
y el actor interno `runtime`. `analyst` es sólo lectura; Jev carece de permiso de
escritura o proyección en este servicio.

**`actor_role` debe provenir del código confiable del runtime, nunca de un campo
elegido por el modelo.** Es una frontera interna, no una autenticación autónoma.
La escritura por run debe serializarse antes de persistir el nuevo snapshot.

| Actor | Escrituras relevantes |
|---|---|
| Coordinator | Memoria observada, preguntas, mapa, hitos y asignación dentro del contrato |
| Operator / Recovery | Memoria observada, hipótesis, mapa y progreso no verificado |
| Verifier | Elevación independiente, criterios satisfechos, confirmación de efecto y reconciliación |
| Runtime | Registro de evidencia, situación visual, cambio de generación, presupuesto y envío de acciones |

El contrato de tarea no es un campo escribible del patch. Una asignación sólo
puede usar acciones y cambios permitidos por él; sus cuotas no superan las del
runtime y no pueden aumentar para el mismo hito activo. Presupuesto global sólo
puede disminuir, manteniendo el deadline original. Al disminuir, las cuotas de
la asignación se ajustan hacia abajo sin modificar su alcance o condiciones de
parada.

La metadata de captura y `CurrentUI` se inyectan con autoridad del runtime. Al
integrar la interpretación de controles de un especialista, el runtime necesita
validar y registrar esa interpretación con la observación vigente; el operador
no registra por sí mismo una nueva observación/generación.

### Evidencia, contradicciones y vigencia

- Sólo `verifier` eleva hechos a `independently_verified`. El autor original se
  conserva; cada revisión añade autor, versión, estado y evidencia.
- Los hechos son adiciones con ID nuevo; una revisión cambia estado y añade
  evidencia mediante `FactUpdate`, sin reescribir la afirmación original.
- Una afirmación contradictoria conserva ambos registros, sus referencias y
  procedencia. Marca el anterior contradicho y enlaza ambas afirmaciones. Un
  patch ajeno posterior no cambia esa relación ni borra la contradicción.
- Los criterios satisfechos requieren hechos independientemente verificados y
  referencias a su evidencia. Contradicción, caducidad o invalidación eliminan
  su satisfacción actual y vuelven a dejar el requisito pendiente. Los hitos
  afectados/dependientes quedan bloqueados conservando su información.
- `valid_until` se evalúa contra el timestamp de la observación registrado por
  el runtime, sin leer un reloj en el reducer.
- Una nueva generación exige una nueva observación y controles localizados en
  ella. Conserva etiquetas, capacidades, rutas y evidencia histórica. Marca
  rutas afectadas como stale; un conjunto explícito de vistas afectadas permite
  preservar rutas semánticas no afectadas. Un conjunto vacío significa reset
  amplio. Esto no autoriza coordenadas ni clics de una ruta histórica.
- El ejecutor baseline incrementa `frame_revision` al intentar una acción, no
  por cada captura. Se admite captura posterior con igual revisión y otro ID;
  el envelope sigue exigiendo el ID vigente. Para confirmar efectos se exige
  revisión posterior a la observación de origen de la acción.
- Un cambio de píxeles no reinicia el contador de falta de progreso. Reducirlo
  requiere una nueva transición útil confirmada o un nuevo criterio verificado.
- La evidencia pertenece al mismo run/sesión/epoch del snapshot. Las referencias
  ausentes o cross-run se rechazan; un ID de consejo no es evidencia.

### Acciones y operaciones inciertas

El runtime registra `not_sent|sent|uncertain|rejected`. Un envío nunca satisface
un criterio ni confirma un efecto. Sólo el verificador confirma un registro
existente, con observación posterior, efecto observado y hechos verificados que
soporten la evidencia. Identidad, objetivo y efecto esperado del registro no se
reescriben.

El marcador pending debe registrarse por el runtime junto al estado de envío
cuando corresponda una mutación/exportación incierta. Mientras exista, se
rechaza registrar un nuevo envío y no se permite sustituir, borrar o resolver
el marcador mediante una propuesta del operador. El gateway sigue siendo
responsable de impedir navegación/repetición antes del envío real; el reducer
sólo valida memoria y no controla RFB.

`clear_pending_operation` requiere al verificador, `PendingResolution` y
evidencia posterior respaldada por hechos verificados. Para `applied`, la acción
debe estar confirmada. Para `not_applied`, conserva el outcome histórico de
envío incierto, añade una reconciliación tipada al `ActionRecord` y retira el
marcador actual. La resolución y sus fuentes no se pierden al limpiar pending.
La API no implementa replay ni idempotencia de clics.

## Proyecciones y límites

Todas las proyecciones conservan contrato completo, identificadores, restricciones,
criterios, asignación, presupuesto, hechos y contradicciones con procedencia,
hipótesis, preguntas abiertas y operación pendiente. Mantienen el mapa parcial
semántico; Coordinator y Analyst reciben los controles sin bounding boxes.
Operator, Verifier y Recovery conservan las localizaciones vigentes.

El historial incluye las últimas diez acciones por defecto y todas las acciones
sin reconciliar `sent|uncertain`, incluyendo la referenciada por pending. Las
acciones antiguas omitidas se declaran en `omitted_action_ids`; las imágenes se
recuperan aparte por ID de evidencia. La proyección conserva el cierre de
referencias necesario para todos sus campos, incluidas las procedencias y
reconciliaciones históricas retenidas, y revalida el contexto proyectado.

No se truncan silenciosamente datos críticos ni se expulsan registros al alcanzar
un límite. El tamaño máximo es en **bytes UTF-8 del JSON**, no tokens. Si no cabe
el paquete suficiente, `project` levanta `ProjectionError("insufficient_context")`.
El runtime debe dividir/comprimir con cobertura explícita o abstenerse.

Límites principales por snapshot: 200 referencias de evidencia, 100 hechos,
40 revisiones de procedencia por hecho, 40 hipótesis, 40 vistas, 80 transiciones,
40 alternativas, 12 hitos, 100 acciones y 80 controles vigentes. Los límites
están en los propios modelos y no autorizan aumentar los presupuestos del run.

## Seguridad y errores

Errores: `ContextError`, `PatchRejected` y `ProjectionError`. Exponen un código
estático; no contienen payloads, UUIDs recibidos, mensajes de proveedores o
valores privados. La validación Pydantic reemplaza inputs/errores internos por
un error genérico redactado, sin conservar la excepción original en su contexto.
También se revalidan instancias construidas con bypass de Pydantic, sin emitir
warnings de serialización que podrían incluir valores.

Los modelos rechazan campos extra y patrones explícitos de credenciales, bearer,
URLs, blobs/base64 y trazas de razonamiento. `repr` y `str` de los contratos no
exponen sus valores. Este control conservador puede rechazar literales legítimos
con esos patrones y **no identifica cualquier secreto arbitrario sin etiqueta**:
el runtime debe separar credenciales/transporte antes de construir el contexto.

Los valores de negocio necesarios se conservan literalmente en **JSON privado
del run y slices privados autorizados**. No se redactan indiscriminadamente los
identificadores necesarios para verificar una tarea. Dumps y slices no son
eventos públicos, logs, documentación ni payloads para otras tareas. Los tests
comprueban tanto esta preservación privada como la ausencia de secretos canario
tipados/prohibidos en errores, salida de consola y warnings.

Las instrucciones originales y etiquetas UI permanecen literales. Objetivos,
afirmaciones y decisiones sintéticas están en inglés. La detección automática de
idioma no se implementa: es responsabilidad de los prompts y consumidores del
integrador, preservando los literales que deben mantenerse.

## Evidencia real de verificación local

Directorio de ejecución: raíz de `frontier-agent`. Se utilizó el entorno `.venv`
existente; no se instalaron dependencias. Datos sintéticos, sin DB, VNC, proveedor
visual, fixtures del organizador ni capturas reales.

```sh
.venv/bin/python -m pytest tests/unit/test_context.py tests/unit/test_context_service.py -q
```

Resultado final: **67 passed in 1.41s**. Sin skips. Incluye MC01 (envelope viejo,
rechazo atómico), MC02 (verificación exclusiva y contradicciones con procedencia),
MC03 (generaciones/localizaciones/rutas y aislamiento), cuotas globales y de
asignación, envío versus efecto, reconciliación aplicada/no aplicada, límites,
proyecciones de cinco roles, reobservación con igual revisión, expiración y
canarios de seguridad. Estas pruebas no acreditan precisión visual ni concurrencia
de persistencia.

```sh
.venv/bin/ruff check app/agents/context.py app/agents/context_service.py tests/unit/test_context.py tests/unit/test_context_service.py
```

Resultado final: **All checks passed!**

```sh
python3 scripts/check_public_files.py
```

Resultado del scan público: primera revisión **119 archivos**, revisión posterior
**120 archivos**, ambas **sin coincidencias privadas detectadas**. El checkout
es compartido; el número de candidatos públicos puede variar por trabajo paralelo.
El scan complementa la revisión y no garantiza detectar todo secreto.

## Pendientes del integrador

1. Revisar estos contratos y el actor interno de confianza antes de conectar
   grafo/Jev. Alias del rol baseline `orchestrator` a `coordinator` corresponde al
   consumidor; el servicio requiere nombres explícitos.
2. Construir snapshots desde observaciones existentes, serializar patches por
   run, suministrar cuotas y persistir resultados con el checkpointer/eventos
   seguros. Un reducer puro no prueba exclusión/concurrencia en DB.
3. Adjuntar `ActionProposal`, pregunta del analista o fallo tipado de recovery
   aparte de la slice cuando corresponda, y recuperar imágenes transitoriamente.
4. Integrar barreras del gateway, registro atómico de pending y cierre confirmado.
   Cambios de sesión/epoch requieren reconstrucción/migración explícita validada
   por el runtime; este reducer no rota ownership ni importa evidencia de una
   sesión anterior al snapshot vigente.
5. Definir compacción/retención, estimación de tokens y recuperación por ID cuando
   se alcanzan límites. No hay truncación ni compresión semántica automática.
6. Versionar checkpoints/grafo y ejecutar los gates de integración, pruebas reales
   y benchmark. Jev, analistas paralelos y cambios del grafo quedan en su paquete.

No hubo stage, commit, publicación ni despliegue en este trabajo. Los resultados
de este documento corresponden únicamente a P04-C1/C2; no declaran integrado el
grafo v2 ni resuelto el recorrido de tareas reales.

## Addendum de integración posterior

Este documento conserva la evidencia del corte C1/C2 original. El integrador
conectó después esos contratos al grafo `context-v2`, sin cambiar las firmas de
`apply`/`project`. El reducer recibió extensiones runtime acotadas: interpretación
del frame vigente, disminución de cuotas de la misma asignación y acknowledgement
del mismo intento `uncertain → sent` sin resolver pending. Ver
[evidencia posterior y gates pendientes](integracion-openrouter-v2.md).
