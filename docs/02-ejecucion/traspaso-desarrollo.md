# Traspaso del desarrollo — 10 de octubre de 2026

> Este traspaso conserva el punto de partida del desarrollo delegado. El estado
> posterior está en [integración OpenRouter v2](integracion-openrouter-v2.md):
> routing, transporte Jev y grafo v2 conectados, con pruebas y gates reales
> acotados. Consultar esa evidencia antes de interpretar los pendientes históricos.

El usuario traslada el desarrollo a otro agente. Este corte modifica configuración y documentación; no integra el routing por rol ni cambia el runtime. La decisión vigente es [modelos por OpenRouter](specs/decision-modelos-openrouter.md), incluido Jev. Sustituye las referencias anteriores a acceso directo TypeSafe.

## Implementado y evidencia

- Baseline FastAPI: autenticación Bearer, health con dependencias/heartbeats, admisión durable de tareas mediante PostgreSQL + outbox y consulta de estado/resumen. No espera VNC/modelo en POST.
- Persistencia: SQLAlchemy async, Alembic 001–003, credenciales cifradas, reservas de escritorio, eventos/evidencias e integración nativa de checkpoint/store PostgreSQL. Redis Streams y dispatcher/worker existentes.
- Desktop: AsyncVNC sobre WSS/RFB, gateway en proceso separado, dueño único, validación de propuestas y registro de intención previo a envío. Cutoff/cierre independientes del modelo. Smoke real confirmó captura 1280×800 y cierre; no ejecución de negocio.
- Grafo baseline: orquestador, operador, verificador y recuperación. Prompts en inglés y adaptador visual OpenRouter. Todavía un único modelo para todos los roles.
- Docker/Compose y bootstrap preparados para Dokploy. No hay despliegue público confirmado ni aceptación de réplicas de worker.
- P04-C1/C2 entregados: contexto tipado inmutable, reducer con permisos/procedencia/versiones y proyecciones por rol. Revisión de pruebas repetida en este traspaso: **67 passed**. Ver [entrega del contexto](contexto-implementado.md).
- AdviceRequest/Result/FinalDecision, guards de contexto suficiente, cuota/deduplicación, bridge de contexto y retorno al solicitante existen y tienen pruebas simuladas. **El transporte Jev existente es directo TypeSafe y queda obsoleto bajo la nueva decisión.** No habilitarlo ni presentarlo como integración OpenRouter.

Verificación de este traspaso: `.venv/bin/python -m pytest tests/unit tests/contract tests/infra/test_compose.py -q --tb=short` → **187 passed**; Ruff del paquete C1/C2 aprobado. Scan público aprobado; `.env` confirmado ignorado por Git. No se ejecutó en este corte la suite con PostgreSQL/Redis reales ni llamadas pagadas.

Las pruebas sintéticas no demuestran resolver la app desconocida. Validación pagada de los modelos, Jev real, E1–E5, segunda app e incidente siguen pendientes. Consultar las evidencias anteriores sin reutilizar sus conteos como resultado del árbol actual.

## Configuración preparada

`.env` local ignorado conserva claves existentes y permisos 0600. `.env.example` contiene los mismos valores públicos, sin credenciales.

| Variable | Valor inicial |
|---|---|
| MODEL_COORDINATOR | openai/gpt-6-luna |
| MODEL_OPERATOR | openai/gpt-6-luna |
| MODEL_VERIFIER | anthropic/claude-sonnet-5.5 |
| MODEL_RECOVERY | openai/gpt-6.1-sol |
| MODEL_ANALYST | openai/gpt-6-luna |
| MODEL_JEV | typesafe/jev-1.13 |

`MODEL_PROVIDER=openrouter`, una sola `OPENROUTER_API_KEY`; `OPENROUTER_BASE_URL` pública. `OPENROUTER_JEV_ENDPOINT=/api/alpha/decisions` se resuelve contra el **origen** OpenRouter: no concatenar a `/api/v1`. Routing visual inicial: sort latency, require_parameters true, allow_fallbacks false; no extender esos parámetros al endpoint Decisions sin validarlos.

**Las nuevas variables por rol, endpoint y routing son configuración objetivo; Settings/factory/Compose todavía no las consumen.** El baseline sigue usando MODEL_NAME y MODEL_REASONING_EFFORT. Settings no carga `.env` automáticamente (env_file=None): el launcher debe inyectarlo o usar una carga explícita segura. `JEV_ENABLED=false` evita activar el adaptador antiguo; habilitar sólo después de migración e integración probadas. Variables privadas antiguas locales se preservaron, pero no deben utilizarse como fallback ni trasladarse al ejemplo público.

No inventar API keys ni dar por probados IDs/capacidades/precios. Verificar acceso, imagen y schema por endpoint. Configurar límites/timeout/reasoning por rol mediante perfiles tipados y validados; no enviar a Sonnet parámetros específicos de OpenAI sin comprobar compatibilidad. Guardar perfil resuelto sin secretos al iniciar cada run.

## Interfaces que se deben reutilizar

```python
ContextService.apply(snapshot, patch, *, actor_role) -> SharedTaskContext
ContextService.project(snapshot, *, role) -> ContextSlice
```

El runtime proporciona el rol confiable y serializa patches por run; no confiar en actor_role generado por el modelo. Debe registrar observaciones, presupuestos y resultados persistidos. Proyección acotada en bytes, no tokens; compacción/estimación de tokens pendientes. Revisar reconciliaciones históricas del ActionRecord al conectar advice_context: no perder evidencia que resuelve una operación incierta. Contexto de negocio permanece privado por run.

## Orden del trabajo delegado

1. **Routing por rol:** añadir Settings tipados, factory/perfiles y wiring de Compose. Roles independientes; analyst opcional, sin fanout por defecto. Fijar perfil al inicio del run. Tests: overrides por rol, parámetros por familia, modelo visual inválido, schema/timeout, perfiles reproducibles, sin fallback directo.
2. **Jev exclusivamente OpenRouter:** verificar documentación primaria y contrato del endpoint elegido; sustituir transporte/autenticación del adaptador actual, reutilizando schemas/guards/cuotas. Adaptar respuestas explícitamente, no suponer compatibilidad con TypeSafe directo. Retirar dependencias de TYPESAFE_API_KEY/JEV_MODEL en config, Compose y smoke. Tests: request/auth/endpoint, respuestas inválidas, probabilidades, timeout/cancelación, deduplicación y presupuesto original; sin efectos VNC ni secretos.
3. **Grafo v2:** integrar C1/C2, hitos, context slices, patches serializados/persistidos, advice→mismo solicitante→FinalDecision. Preservar pending_operation y reconciliación por verificador. Versionar checkpoints y drenar runs anteriores, sin replay ni migración automática de acciones. MC04–MC08, aislamiento, crash/restauración y canarios en todos los sinks.
4. **Validación real:** smoke visual con imágenes sintéticas y Jev textual; luego consulta no mutante con dueño único. Harness E1–E5, tres repeticiones, otra app/variantes y fallos. No entregar fixtures/check/reset al agente. Registrar exactitud, falsos éxitos, cambios extra, latencia/coste por tarea correcta y consumo por rol.
5. **Release:** confirmar cierre tras crash del worker, GC/retención y límites de conexiones. Usuario despliega Dokploy; verificar HTTPS/Bearer/readiness, POST <2 s y tarea <=300 s desde internet. No habilitar réplicas worker antes de R05/cierre probado. Preparar evidencia sanitizada/video sólo con resultados reales.

Observabilidad externa permanece apagada en demo. Diseño previo usa LangSmith y nueva decisión menciona Langfuse: resolver y documentar cuál se adoptará antes de instrumentar producción, con exportación filtrada y acotada.

## Encargo listo para copiar

> Continúa el desarrollo existente de Frontier Agent como agente integrador. Lee AGENTS.md, este traspaso, specs/decision-modelos-openrouter.md, multiagente-contexto-jev.md, planning.md, sistema.md, pruebas.md y contexto-implementado.md. No recrees el baseline ni P04-C1/C2. Implementa primero routing configurable por rol desde .env con OpenRouter exclusivo; después migra Jev del transporte TypeSafe directo a OpenRouter Decisions, reutilizando AdviceRequest/Result/FinalDecision y guards existentes. Verifica el contrato real antes de adaptar payloads. Integra contexto e hitos en el grafo v2, consejo al mismo solicitante y patches serializados con rol confiable. Configura reasoning/límites por perfil validado, sin parámetros indiscriminados ni fallback directo. Mantén API pública, deadline original de 300 s, cutoff y dueño único VNC; cierre confirmado antes de estado terminal, sin replay de mutaciones inciertas. Todo cambio requiere pruebas de comportamiento y regresiones pertinentes; reporta archivos, comandos/resultados y pendientes separando mocks de pruebas reales. No leas/imprimas secretos ni publiques capturas/datos; transporte puede leer credenciales sin enviarlas al modelo. No alteres staging ni artefactos de diseño aprobados. No hagas commit, publicación ni deploy. Actualiza planning/documentación y ejecuta Ruff, suites pertinentes y python3 scripts/check_public_files.py. La configuración objetivo ya está en .env/.env.example, pero todavía no está conectada al runtime. No habilites Jev hasta retirar el transporte directo y pasar pruebas. Después ejecuta los gates reales autorizados con recursos aislados y reporta cualquier bloqueo concreto.
