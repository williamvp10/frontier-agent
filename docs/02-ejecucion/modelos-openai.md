> **Actualización de decisión:** todo acceso a modelos, incluido Jev, será exclusivamente OpenRouter según [decisión de modelos](specs/decision-modelos-openrouter.md). El transporte TypeSafe directo existente requiere migración; las referencias anteriores a ese proveedor directo quedan sustituidas. Routing por rol y grafo v2 aún no integrados; ver el traspaso de desarrollo.

# Modelo visual vía OpenRouter y consejero Jev

Decisión actual: **OpenRouter es el único proveedor del modelo visual**. Configuración: `MODEL_PROVIDER=openrouter`, `MODEL_NAME=openai/gpt-6.1-sol`, `OPENROUTER_API_KEY`. Se retiró el respaldo OpenAI directo. Usar `ChatOpenAI` como cliente compatible no implica llamar al proveedor OpenAI directamente.

El catálogo público consultado el 10 de octubre de 2026 anuncia imagen y structured outputs para ese slug. Debe comprobarse acceso/esquemas/latencia con la cuenta del equipo; no se ha demostrado éxito visual real. [Catálogo](https://openrouter.ai/api/v1/models).

`build_visual_model` sólo construye OpenRouterVisualModel. Chat Completions lleva imagen transitoria detail=high y JSON Schema strict, `require_parameters=true`, `allow_fallbacks=false`, sin hosted tools ni reintentos automáticos. [Structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs). Reasoning low/exclude, límite 4096 tokens y timeout20 s subordinado al deadline original. No habilitar fallback fuera del router por errores de inferencia.

La clase base OpenAIVisualModel conserva el cliente compatible y pruebas históricas Responses; no es una ruta seleccionable por Settings/factory/worker. El runtime no consume OPENAI_API_KEY. Su eventual eliminación/refactor no cambia contratos.

## Jev

Consejero separado de TypeSafe, adapter y flujo auxiliar implementados; integración al grafo pendiente; no se presupone un modelo Jev en OpenRouter. Recibe texto/JSON; no screenshots. El agente solicitante arma pregunta/contexto completos y toma la decisión final tras el consejo. Confianza no prueba identidad ni autoriza una acción. [State](https://docs.typesafe.ai/concepts/state), [Choice](https://docs.typesafe.ai/primitives/choice).

La spec define AdviceRequest/Result, cobertura, cuotas, stale guards y FinalDecision. `JEV_ENABLED=false`, `TYPESAFE_API_KEY` y `JEV_MODEL` configuran el adapter disponible; aún no se activa en el grafo/worker. Ver [evidencia Jev](jev-implementado.md). Fijar acceso/modelo/SDK antes de implementar; no inventar compatibilidad de transporte ni resultados.

## Prompts y memoria

Plantillas inglesas versionadas en app/models/prompts.py. LIVE_ROLES funciona con schemas del baseline; las plantillas completas de hitos/Jev son para el grafo v2. No habilitan herramientas inexistentes. Mantener instrucción original, etiquetas y valores literales; respuesta pública conserva estados españoles y respuesta:.

Baseline: 40 hechos verificados acotados y 20 acciones resumidas por run. V2 exige Fact con procedencia, mapa parcial y contexto versionado. No reutilizar datos de negocio entre tareas. Imágenes sólo en llamada transitoria; sin credenciales/fixtures/base64 en checkpoints ni prompts de Jev. Trazado del modelo deshabilitado; no registrar excepciones/payloads crudos. Retención de proveedor depende de la cuenta, no del flag local.

## Aceptación

HTTP mock del SDK comprueba endpoint, imagen, JSON Schema y ausencia de tools/credenciales en body. No demuestra comprensión visual ni aceptación real del servidor. Antes de release: smoke privado real, E1–E5, variaciones de UI y comparación baseline/contexto/contexto+Jev. Medir corrección, falsos éxitos, latencia, llamadas/coste y cierre.
