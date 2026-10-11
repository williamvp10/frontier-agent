> **Actualización de decisión:** todo acceso a modelos, incluido Jev, será exclusivamente OpenRouter según [decisión de modelos](specs/decision-modelos-openrouter.md). El transporte TypeSafe directo existente requiere migración; las referencias anteriores a ese proveedor directo quedan sustituidas. Routing por rol y grafo v2 aún no integrados; ver el traspaso de desarrollo.

# Consejero Jev: implementación inicial

10 de octubre de 2026. Componentes implementados y probados; todavía no activados en el grafo/worker de evaluación. API pública, política de verificación y gateway conservan su comportamiento.

## Entrega

- `app/models/advice.py`: contratos privados inmutables y acotados, AdviceRequest/Result, ChoiceAnswer y FinalDecision. Errores de validación sin inputs privados, repr sin datos de negocio.
- `app/models/jev_advisor.py`: TypeSafe HTTP, coverage guard, binding crítico del runtime, validación de evidencia, cuota por run, deduplicación concurrente, timeout UTC/monotónico y consejo stale. No retries automáticos ni redirects; respuesta máxima 64 KiB. Ninguna operación VNC.
- `app/agents/advice_context.py`: puente desde el contexto v2 aceptado a request/binding. Conserva instrucción original, identidad, restricciones, preservación, progreso, hechos con procedencia, historial, incertidumbres, mapa y operación pendiente. No truncar silenciosamente al superar límites; devuelve error para recuperar contexto suficiente.
- `app/agents/advice_flow.py`: consulta → callback del mismo solicitante → FinalDecision. Revalida contexto tras inferencia; devuelve sólo candidato. El callback debe consumir su presupuesto normal de modelo/rol; la función no lo reinicia ni autoriza acciones.
- Settings/env/Compose: `JEV_ENABLED=false`, `TYPESAFE_API_KEY`, `JEV_MODEL=jev-1.13.0`, timeout3 s, cuota4. Credencial mapeada sólo al worker. Factory optional disponible, todavía no conectada al runner.
- `scripts/smoke_jev.py`: smoke explícito sobre texto sintético, sin escritorio ni datos de negocio.

## Contrato del proveedor

Se usa HTTPX ya instalado, sin otro SDK. Endpoint fijo `POST https://api.typesafe.ai/v1/systemone`, Bearer de TypeSafe; body state/model/questions. Primera versión soporta Choice con opción insufficient, máximo cuatro preguntas independientes y doce alternativas por pregunta. No soporta todavía Score/Noul. Comprueba claves, opciones, probabilidades finitas que suman uno, confidence y uso; no interpreta confidence como evidencia.

Fuentes oficiales verificadas: [API](https://docs.typesafe.ai/api), [modelos](https://docs.typesafe.ai/models), [Choice](https://docs.typesafe.ai/primitives/choice). Modelo versionado fijado a jev-1.13.0 para evitar cambios implícitos de alias; acceso real del equipo pendiente.

## Seguridad y control

Sólo el runtime construye AdviceBinding desde estado aceptado. El request no puede omitir/cambiar contrato, pending_operation o IDs críticos; evidence_ids deben existir en el propio run. El bridge proyecta los hechos y antecedentes desde snapshot, sin aceptar un paquete de contexto escrito libremente por un LLM. Las opciones/preguntas deben seguir grounded en el contexto; su validación semántica corresponde al grafo/verificador, aún pendiente.

Nunca enviar imágenes, URLs/endpoints o secretos. Defensa por tipos, patrones y lista de secretos exactos que debe suministrar el runtime. Patrones no garantizan detectar una contraseña arbitraria; por eso el contexto no recibe credenciales y los productores tienen que mantener ese aislamiento. Contexto privado no se imprime ni se registra como evento crudo. Guard de privacidad conservador puede rechazar URLs legítimas del negocio: continuar sin consejo o construir un contexto suficiente y seguro, sin omitir datos críticos silenciosamente.

Un error/timeout devuelve estado typed; no aprueba task ni reintenta acciones. Cancelación del caller se propaga. FinalDecision se verifica contra binding vigente y consejo recibido; sus precondiciones pendientes bloquean propose_action. Aun válida, debe pasar validación normal de acciones y verificador VNC.

## Evidencia ejecutada

```sh
.venv/bin/python -m pytest tests/unit/test_jev_advisor.py tests/unit/test_advice_context.py tests/unit/test_advice_flow.py tests/infra/test_compose.py -q --tb=short
.venv/bin/ruff check app/models app/agents/advice_context.py app/agents/advice_flow.py app/core/config.py scripts/smoke_jev.py tests/unit/test_jev_advisor.py tests/unit/test_advice_context.py tests/unit/test_advice_flow.py tests/infra/test_compose.py
.venv/bin/python scripts/check_public_files.py
```

**48 pruebas pasan**, Ruff limpio. HTTP simulado comprueba request completo, labels/instrucción literales, scope/evidencia, deadline, cuota, duplicados, cancelación, response inválido/oversized/secret, errores 401/422/429/529/503/redirect, retorno al solicitante y rechazo tras cambio de contexto. No son inferencias Jev reales ni pruebas de éxito visual.

Intento de smoke real se detuvo antes de red por TYPESAFE_API_KEY ausente; OpenRouter también sigue sin clave configurada en el entorno local comprobado. Completar claves localmente, nunca en chat, para ejecutar smokes autorizados.

## Integración pendiente

Revisar/congelar contratos del agente de contexto; después conectar C3/C5 al grafo: hitos, patches aceptados, consult_jev y retorno al mismo rol. Deben añadirse cuotas de modelo/advice consumidas al contexto por runtime, eventos allowlisted por referencia, cierre independiente, graph_version y pruebas de checkpoint/cancel/restart. No reclamar C4/C5 completos antes de ese recorrido.

Persistir ledger de cuotas/deduplicación al integrar checkpoints si hay recuperación de una run: el ledger actual es local al proceso y por sí solo no prueba conservación de cuota tras restart. Evitar replay de consultas/acciones al restaurar. Consejos y decisiones se almacenan sólo en el contexto privado validado, nunca como verdad de negocio o como autorización.

E1–E5 y benchmark sin/con Jev siguen pendientes. Jev no reemplaza OpenRouter, verificador, evidencia visual ni supervisor.

Revisión conjunta ejecutada: tests/unit, tests/contract y tests/infra/test_compose.py pasaron **187 pruebas**, incluyendo el endurecimiento de errores y deduplicación. Incluye contexto del agente de apoyo presente en el workspace; no sustituye su entrega/revisión de interfaces. DB/Redis real y E1–E5 no se reejecutaron en este corte.
