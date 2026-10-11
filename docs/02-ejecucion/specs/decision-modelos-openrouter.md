# Decisión de modelos: acceso exclusivo mediante OpenRouter

Fecha: 10 de octubre de 2026. Estado: acceso aprobado; asignación inicial para implementar y evaluar, sin benchmark propio completado.

Complementa [arquitectura multiagente](multiagente-contexto-jev.md). No modifica el contrato público, el transporte VNC ni los invariantes de cierre/deadline. Esta decisión sustituye cualquier propuesta anterior de llamadas directas a OpenAI, Anthropic, Google o TypeSafe.

## 1. Decisión y objetivo

Todos los modelos, incluido Jev, se invocarán mediante OpenRouter. Usar una credencial `OPENROUTER_API_KEY` gestionada privadamente; no requieren claves directas de los fabricantes. Un SDK compatible con OpenAI o TypeSafe es una biblioteca cliente, no una excepción al destino OpenRouter.

Objetivo de selección: minimizar tiempo y coste por tarea correcta, con cero falsos éxitos, mutaciones fuera de alcance o acciones después del cierre. Aceptar imágenes no demuestra precisión al localizar controles; cada modelo debe superar pruebas de screenshots y recorridos reales.

No alojar agentes externos que operen la app por nosotros. Los modelos interpretan o aconsejan; nuestro grafo, ejecutor y gateway controlan el escritorio.

## 2. Asignación inicial por rol

| Rol | Modelo inicial | Alternativa a evaluar | Uso |
|---|---|---|---|
| Coordinator | `openai/gpt-6-luna` | `openai/gpt-6.1-sol` | Contrato, hitos y replanteamiento; imágenes solo cuando sean necesarias |
| Operator | `openai/gpt-6-luna` | Haiku 5.5, Qwen3.8 Flash, Gemini 3.8 Flash | Ciclo frecuente: pantalla, memoria, efecto y propuesta |
| Verifier | `anthropic/claude-sonnet-5.5` | `openai/gpt-6.1-sol` | Evidencia visual independiente, identidad, alcance y resultado |
| Recovery | `openai/gpt-6.1-sol` | `anthropic/claude-sonnet-5.5` | Bloqueos, discrepancias y operaciones inciertas |
| Analyst opcional | `openai/gpt-6-luna` | Modelo económico que pase el benchmark | Análisis sobre snapshot, sin VNC |
| Jev advisor | `typesafe/jev-1.13` | Decisión del agente sin consejo si Jev no responde | Preguntas tipadas con contexto completo necesario |

La asignación es una hipótesis inicial, no un ranking demostrado. Mantener un operador por tarea; cambiar modelo no cambia permisos ni permite reiniciar la tarea. Un verificador de otra familia puede aportar contraste, pero debe revisar evidencia original y no asumir que el resumen del operador es verdadero.

## 3. Candidatos y precios de referencia

USD por millón de tokens, entrada/salida sin caché, consultados el 10 de octubre de 2026 en el catálogo público de OpenRouter. Son precios de referencia para contextos cortos; proveedor, longitud de contexto, modalidad y configuración pueden cambiar la factura. Revalidar antes del benchmark y del release.

| Fabricante | ID OpenRouter | Entrada / M | Salida / M |
|---|---|---:|---:|
| OpenAI | `openai/gpt-6-luna` | $0.10 | $0.50 |
| OpenAI | `openai/gpt-6.1-sol` | $2.00 | $10.00 |
| Anthropic | `anthropic/claude-haiku-5.5` | $0.10 | $0.50 |
| Anthropic | `anthropic/claude-sonnet-5.5` | $2.00 | $10.00 |
| Google | `google/gemini-3.5-flash-lite` | $0.30 | $2.50 |
| Google | `google/gemini-3.8-flash` | $0.75 | $3.75 |
| Alibaba | `qwen/qwen3.8-flash` | $0.15 | $0.47 |
| Alibaba | `qwen/qwen3-vl-235b-a22b-instruct` | $0.21 | $1.90 |
| Z.ai | `z-ai/glm-5.3-flash` | $0.15 | $0.50 |
| DeepSeek | `deepseek/deepseek-v4.1-flash` | $0.30 | $1.20 |
| Moonshot | `moonshotai/kimi-k2.5` | $0.50 | $2.50 |
| Moonshot | `moonshotai/kimi-k3` | $0.30 | $14.90 |
| TypeSafe | `typesafe/jev-1.13` | $0.042 | $0.00 |

Los candidatos visuales de la tabla anuncian entrada de imagen y salidas estructuradas en el catálogo consultado. Jev acepta texto y devuelve decisiones. Verificar capacidades del endpoint seleccionado, no solo el nombre de la familia. No usar un modelo exclusivamente textual para interpretar una screenshot.

Prioridad de comparación: Luna, Haiku, Qwen Flash y Gemini Flash como operadores; Sol y Sonnet como verificadores. Qwen3-VL Instruct es candidato específico de percepción sin razonamiento obligatorio. GLM, DeepSeek y Kimi son segunda ronda si aportan una ventaja medible. El coste alto de salida de Kimi K3 requiere justificar su uso en el ciclo frecuente.

No usar variantes `:free`, batch, aliases `latest` ni selección automática abierta como baseline de evaluación. Necesitamos disponibilidad y configuración reproducibles.

## 4. Coste ilustrativo, no resultado medido

Supuesto sintético: 30 llamadas de 4.000 tokens facturados de entrada, incluida imagen, y 500 de salida por llamada. Total: 120.000 de entrada y 15.000 de salida.

| Modelo | Coste estimado bajo ese supuesto |
|---|---:|
| Luna / Haiku | $0.0195 |
| Qwen3.8 Flash | $0.02505 |
| GLM-5.3 Flash | $0.0255 |
| DeepSeek V4.1 Flash | $0.054 |
| Gemini 3.8 Flash | $0.14625 |
| Sol / Sonnet | $0.39 |
| Kimi K3 | $0.2595 |

Fórmula: `input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price`.

No asumir que una screenshot consume 4.000 tokens en todos los modelos. La codificación de imágenes, caché, razonamiento, reintentos y llamadas de especialistas cambian el consumo. Evitar sumar razonamiento dos veces si el usage ya lo incluye en completion. Registrar coste real cuando esté disponible; si falta, marcarlo como desconocido o estimado explícitamente.

Una consulta Jev de 5.000 tokens cuesta aproximadamente $0.00021 con la tarifa de referencia. Su bajo precio no justifica consultas redundantes ni paquetes incompletos.

## 5. Adaptadores y endpoints

Separar transporte de inferencia visual y transporte de decisiones:

```text
LangGraph roles
  ├─ OpenRouterVisualModel → OpenRouter Chat Completions
  └─ OpenRouterJevAdvisor  → OpenRouter Decisions API
                                ↓
                    proveedores upstream por OpenRouter
```

- Modelos visuales: base URL pública `https://openrouter.ai/api/v1`; endpoint `POST /chat/completions`, con contenido de imagen y contrato JSON Schema del rol.
- Jev: elegir inicialmente `POST https://openrouter.ai/api/alpha/decisions`; fijar y validar su contrato específico. No tratarlo como chat completion.
- Si se adopta el SDK TypeSafe, alternativa admitida: `https://openrouter.ai/api/v1/systemone`, siempre con autenticación OpenRouter. Elegir un camino por release; no mantener dos implementaciones sin necesidad.
- Ningún fallback cambia a una API directa del fabricante. Si OpenRouter no está disponible, aplicar recuperación acotada y fallo veraz.

El adaptador devuelve los schemas internos existentes. No exponer formas de respuesta específicas del proveedor al grafo. No presuponer que el adaptador actual basado en Responses API funciona sin cambios con este contrato.

## 6. Routing, parámetros y escalamiento

Para desarrollo, configuración de partida de llamadas visuales:

```json
{
  "provider": {
    "sort": "latency",
    "require_parameters": true
  }
}
```

Adjuntar `response_format` con schema del rol y validarlo localmente. `require_parameters` evita enviar la llamada a endpoints que no soporten los parámetros solicitados. Para release, fijar además proveedores permitidos y fallback probado. No reutilizar esta configuración automáticamente en Decisions API: comprobar su soporte específico.

- Medir duración hasta respuesta completa válida; primer token y tokens/segundo no describen solos el tiempo de decisión.
- Usar razonamiento bajo o desactivado donde el endpoint lo soporte. No enviar el mismo parámetro indiscriminadamente: algunos modelos exigen razonamiento o usan otras opciones.
- Limitar salida según schema; un límite demasiado corto puede truncar JSON y producir más reintentos. Definirlo con pruebas.
- Mantener contexto compacto y prefijo estable, sin omitir evidencia crítica; caché es una optimización opcional, no una suposición de coste.
- Eliminar auto-retries ilimitados. Timeout, retry y escalamiento consumen el presupuesto original de 300 s.

Escalar a un modelo más capaz por contradicción visual, ilegibilidad relevante, salida inválida repetida o falta de progreso; no por cada turno. Pasar contrato, hito, mapa relevante, evidencia y operaciones pendientes al modelo escalado. Una nueva respuesta no autoriza replay de una mutación.

Fallback de proveedor y escalamiento de modelo son distintos. Registrar cuál ocurrió. Una propuesta producida después del cutoff se descarta aunque la llamada haya empezado antes.

## 7. Jev: contexto y autoridad

Implementar el AdviceRequest definido en la spec multiagente. El agente solicitante plantea la pregunta; el context service completa tarea original, restricciones, avance, hito, situación, hechos con procedencia, intentos previos, incertidumbres, alternativas y presupuesto relevantes.

No enviar solo la pregunta o el último error. No afirmar que Jev vio una imagen: recibe descripción textual y evidencias referenciadas. Validar contexto suficiente antes de llamar. Si excede el límite, dividir o resumir sin eliminar datos críticos y declarar omisiones.

La documentación consultada presenta límites de contexto diferentes entre guía y página del modelo. Usar provisionalmente un máximo conservador de 32.000 tokens para estado + preguntas, y verificar el límite real del endpoint antes de fijar la integración. No convertir ese límite provisional en una afirmación sobre la capacidad definitiva.

Jev devuelve probabilidades y recomendaciones; el mismo agente toma la decisión final con evidencia y justificación breve. Jev no aprueba VNC, no reemplaza el verificador visual y no declara éxito. Si falla, continuar sin consejo solo con evidencia suficiente o reobservar/abstenerse.

## 8. Configuración propuesta

Nombres propuestos para implementación; revisar config actual antes de añadirlos. `.env.example` solo tendrá placeholders y valores públicos.

```dotenv
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MODEL_COORDINATOR=openai/gpt-6-luna
MODEL_OPERATOR=openai/gpt-6-luna
MODEL_VERIFIER=anthropic/claude-sonnet-5.5
MODEL_RECOVERY=openai/gpt-6.1-sol
MODEL_ANALYST=openai/gpt-6-luna
MODEL_JEV=typesafe/jev-1.13
JEV_ENABLED=true
```

Presupuestos, reasoning y routing deben configurarse por rol/endpoint mediante un perfil validado. Guardar el perfil resuelto al iniciar el run: ids, proveedor permitido, parámetros, prompt_version y graph_version; no credenciales. No leer aliases cambiantes en mitad de la ejecución.

Actualizar config, dependencias fijadas, adapter, Compose y documentación al implementar. Si quedan variables antiguas de proveedor directo, migrarlas explícitamente; no tener caminos secretos de fallback ni pedir claves adicionales de fabricantes.

## 9. Datos y observabilidad

Screenshots y contexto de negocio se envían transitoriamente solo cuando la decisión los necesita. Credenciales VNC, tokens y endpoints privados nunca entran al payload del modelo. Revisar routing y política de retención de los proveedores seleccionados; aplicar filtros de privacidad compatibles y comprobar disponibilidad efectiva.

Por llamada registrar metadatos seguros: run/rol, request id, modelo/proveedor resueltos, schema/prompt version, duración, usage/coste disponible, timeout y motivo de escalamiento. No registrar prompts crudos, imágenes, respuestas completas ni razonamiento interno. Langfuse debe instrumentarse con exportación filtrada; no activar callbacks que eludan la política.

La observabilidad es asíncrona y acotada. Un fallo de telemetría no amplía el plazo ni retrasa el cierre del escritorio.

## 10. Benchmark y aceptación

1. Validar por candidato: acceso OpenRouter, entrada de imagen, schema estricto, rechazos/errores y timeout. No se requiere acceso VNC para este smoke.
2. Comparar screenshots sintéticas: texto pequeño/acento, tabla, homónimos, modal, control deshabilitado y localización en 1280×800. Mantener conversiones de coordenadas explícitas; no cambiar resolución silenciosamente.
3. Cambiar únicamente el operador entre Luna, Haiku, Qwen y Gemini, manteniendo prompts, contexto, verificador y variantes comparables.
4. Con operadores viables, comparar Sol/Sonnet como verificadores. Evaluar falsos positivos, identidad y preservación, no solo aprobación de casos correctos.
5. Comparar con/sin Jev y escalamiento; conservar el mismo mecanismo de cierre y presupuesto.
6. Ejecutar E1–E5 con harness aislado y tres repeticiones por caso; añadir otra app sintética y fallos. El agente recibe solo instrucción y escritorio.

Métricas: porcentaje correcto, falsos éxitos, cambios extra, duración total mediana/p95, coste por tarea correcta, llamadas por rol, tokens, clics fallidos, navegación repetida, reobservación por propuesta expirada y recuperaciones útiles.

Descartar configuraciones con mutaciones fuera de alcance o acciones tras cierre. Elegir primero corrección y cumplimiento del plazo; entre configuraciones que los cumplen, optimizar duración y coste. 15/15 es una meta de release, no evidencia de generalidad universal. Las cifras de latencia del catálogo no sustituyen mediciones desde la VPS.

## 11. Orden de implementación

1. Reutilizar y revisar el adaptador OpenRouter existente; separar perfiles por rol y validar los schemas actuales.
2. Añadir Jev mediante un adapter propio a OpenRouter y contrato de decisión probado.
3. Integrar el paquete completo de contexto y retorno al agente solicitante.
4. Implementar metadatos/coste, timeout y escalamiento sin cambiar invariantes VNC.
5. Ejecutar comparación y registrar resultados sanitizados; fijar perfil de release sin afirmar resultados pendientes.

Antes de commit/publicación: `python3 scripts/check_public_files.py` y revisión del contenido preparado. Este documento no ejecuta llamadas pagadas, cambia configuración real ni despliega el servicio.

## 12. Fuentes

- [Catálogo y precios de modelos](https://openrouter.ai/api/v1/models).
- [Provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).
- [Structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs).
- [Jev en OpenRouter y APIs](https://openrouter.ai/docs/guides/community/jev).
- [Jev 1.13: precio y capacidades](https://openrouter.ai/typesafe/jev-1.13).
