> **Encargo sustituido:** C1/C2 ya fueron entregados y sus 67 tests se repitieron. El usuario delega ahora la integración completa. Usar el [traspaso y prompt vigentes](../traspaso-desarrollo.md). Las referencias siguientes a TypeSafe directo son históricas y no autorizan su uso; Jev también debe pasar por OpenRouter.

# Siguiente corte de desarrollo: dos agentes

Estado verificado el 10 de octubre de 2026: baseline API/DB/cola/gateway/grafo existe. No rehacerlo. Contexto tipado por hitos, adapter Jev y conexión de ambos al grafo siguen pendientes. La evidencia vigente está en contrato-entrypoint.md; pruebas passing no demuestran éxito de tareas reales.

## Prioridad de entrega

1. Smoke visual real con OpenRouter y primera consulta no mutante, usando el baseline. Registrar exactitud/latencia; no esperar todo v2 para comprobar capacidad básica.
2. Construir contexto tipado y consejero Jev en paralelo; integrar sólo tras acordar contratos.
3. Hitos, actualización de memoria y retorno del consejo al solicitante en el grafo; preservar verificador actual.
4. Harness E1–E5, tres repeticiones; variante de UI/segunda app y fallos de cierre. Corregir con regresiones generales.
5. Usuario despliega Dokploy; probar HTTPS/contrato/latencia desde internet y preparar video. Multihost, observabilidad externa y réplicas de worker después de sus gates.

La recuperación de cierre tras crash de worker es un pendiente de seguridad para release, no una mejora cosmética. GC/retención operativa también queda pendiente. No habilitar takeover de una sesión uncertain ni multiworker para aparentar escalabilidad.

## Agente de apoyo: P04-C1/C2 — contexto y memoria

Archivos exclusivos:

- `app/agents/context.py`
- `app/agents/context_service.py`
- `tests/unit/test_context.py`
- `tests/unit/test_context_service.py`
- `docs/02-ejecucion/contexto-implementado.md` para evidencia sanitaria

No modificar graph.py, runtime.py, modelos/prompts/config, API, worker, ORM/migraciones, lockfile, planning ni artefactos aprobados. Reportar cambios de interfaz necesarios al integrador. No leer secretos ni operar el escritorio.

Implementar contratos de SharedTaskContext, TaskContract, CurrentUI, Fact, Hypothesis, mapa parcial, Milestone/Assignment, ActionRecord, PendingOperation, Progress, Budgets y ContextPatch según multiagente-contexto-jev.md. Tipos inmutables, extra fields prohibidos, colecciones acotadas y IDs/referencias del propio run. Los modelos no escriben directamente DB; la persistencia sigue correspondiendo al checkpointer existente.

Interfaces propuestas para congelar en el primer corte:

```python
class ContextService:
    def apply(self, snapshot: SharedTaskContext, patch: ContextPatch,
              *, actor_role: str) -> SharedTaskContext: ...
    def project(self, snapshot: SharedTaskContext, *, role: str) -> ContextSlice: ...
```

`apply` es un reducer puro: valida identidad/versión/generación/evidencia/rol y devuelve un nuevo snapshot o una excepción tipada sin payload privado. El runtime serializa la aplicación por run; una clase pura no prueba por sí sola concurrencia DB. El runtime inyecta observation/budgets/epoch: un patch de modelo no puede modificarlos ni ampliar scope.

`project` entrega sólo contexto relevante para rol, sin transportar screenshots/passwords/URLs. No truncar identidad, contradicciones ni pending_operation silenciosamente. Si el límite impide un contexto suficiente, devolver error tipado para que el runtime comprima/reobserve/abstenga. Las imágenes se recuperan aparte por referencia.

Pruebas mínimas:

- MC01: patch antiguo/otro run/frame/epoch rechazado; snapshot original intacto.
- MC02: sólo verifier puede elevar independently_verified; contradicciones conservan procedencia.
- MC03: relocalización invalidada por nueva generación; ruta semántica conservada cuando procede; aislamiento entre runs.
- Operación pending no se borra ni confirma por consejo/propuesta del operador.
- Actualizaciones no cambian instrucción original, restricciones o presupuesto.
- Colecciones acotadas; referencias cross-run inválidas; proyecciones por rol completas en datos críticos.
- Canarios y blobs no llegan a serialización/slices/errores; justificar qué valores de negocio privados sí pertenecen al contexto del run.

Primera entrega: context.py y tests de contratos, más listado de interfaces. El integrador revisa antes de conectar grafo/Jev; después entregar reducer/projections. No cambiar nombres en silencio tras el corte.

## Agente principal: consejero, grafo e integración

Mientras avanza contexto, trabajar en `app/models/jev_advisor.py` y contratos internos de AdviceRequest/Result/FinalDecision en un módulo propio de advice, con adapter fake/HTTP simulado. No depende de un cliente VNC ni recibe secretos de escritorio.

Jev separado TypeSafe, preguntas en inglés, texto/JSON y evidencia referenciada; API/modelo/SDK se verifican antes de fijarlos. Coverage guard completa tarea/progreso/historial/pendientes, consulta bounded y devuelve consejo al agente solicitante. No mapear choice directamente a RFB. Optional failure no autoriza éxito ni ampliar el deadline. Comparar solicitudes repetidas y rechazar stale advice. MC05–MC08.

Después del corte de interfaces, integrar C3/C5: contexto por rol, hito activo, patches validados, advice→solicitante→FinalDecision. Versionar graph/checkpoints y drenar runs antiguas. No migrar acciones para replay. Analistas paralelos y bypass de navegación se mantienen deshabilitados hasta MC09 y política aceptada.

## Encargo listo para copiar al otro agente

> Lee AGENTS.md, docs/02-ejecucion/specs/multiagente-contexto-jev.md, pruebas.md y delegacion-desarrollo.md. Implementa únicamente P04-C1/C2: contratos tipados inmutables de contexto y mapa, reducer validado y proyecciones por rol, en app/agents/context.py y context_service.py, con tests/unit/test_context.py y test_context_service.py. Respeta el reparto y las interfaces propuestas. Primero entrega contratos/interfaz y tests para revisión; después reducer/proyecciones. No cambies grafo, prompts, schemas existentes, worker, API, DB, config o lockfile sin coordinar. No leas secretos ni operes VNC. Verifica MC01–MC03, invariantes de scope/budget/pending_operation y aislamiento entre runs. Reporta archivos, interfaces, comandos/resultados y limitaciones en contexto-implementado.md. No publiques, hagas deploy ni marques v2 integrado. Ejecuta scan público antes de entregar. Si necesitas un contrato distinto, presenta la alternativa al integrador antes de modificar consumidores.

## Verificación del corte

Ejecutar tests propios, Ruff y scan; luego el integrador ejecuta grafo/runner/contrato e integración con DB/Redis reales desechables. Validación visual real es independiente. No presentar resultados del baseline como pruebas del contexto/Jev nuevo. Un único propietario del escritorio durante cada smoke/harness.
