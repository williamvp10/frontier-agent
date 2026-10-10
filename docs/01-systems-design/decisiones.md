# Decisiones, escalabilidad y operación

Versión 0.3. Arquitectura objetivo; el notebook solo valida transporte y entrada VNC. Este anexo desarrolla el documento compacto de entrega.

## Stack y alternativas

| Decisión | Alternativa | Motivo y coste asumido |
|---|---|---|
| FastAPI y worker separados | Petición larga o tarea dentro de API | Aceptación <2 s y aislamiento; exige cola/estado compartido |
| LangGraph para rutas, estado y subgrafos | Bucle libre del modelo | Control explícito de pasos y recuperación; mayor diseño inicial |
| LangChain para modelos y tools | Integraciones específicas dispersas | Contratos uniformes y modelo sustituible; dependencia adicional |
| VNC directo con AsyncVNC y puente WSS | noVNC + Playwright | Transporte validado, menos procesos; licencia y mantenimiento por revisar |
| Orquestador y tres especialistas | Agente monolítico | Contextos especializados y verificación separada; más llamadas, delegación por hitos |
| Ejecutor exclusivo; gateway en producción | Cada subagente conecta a VNC | Evitar carreras; coordinación y servicio adicional |
| VPS con Compose para demo | Clúster desde el inicio | Despliegue rápido; punto único de fallo |
| PostgreSQL desde demo; objetos privados en producción | Redis como único estado | Tareas, historial y memoria durables; pools/migraciones adicionales |
| LangSmith solo en producción | Trazar todo desde el prototipo | Diagnóstico por nodo y evaluación; filtrado y costes adicionales |

El patrón multiagente se implementará sobre LangGraph/LangChain; no exige añadir `deepagents`. Planificación, memoria y delegación deben construirse y probarse. Modelo visual y admisibilidad de proveedor siguen pendientes de validación de reglas.

## Estado y responsabilidades multiagente

Estado por tarea: identificador, tenant en producción, instrucción, alcance, plazo desde recepción, versión de sesión, plan, última observación, evidencias, reintentos y resultado candidato. Checkpoints conservan referencias a imágenes, sin credenciales ni blobs completos.

- Orquestador: plan, delegación y presupuesto; no conecta al escritorio.
- Operador: propone acción, control visible, coordenadas/tecla, efecto esperado y `observation_id`.
- Verificador: devuelve verificada/incompleta/ambigua/no verificable, con evidencias.
- Recuperador: diagnóstico y alternativa dentro del alcance, o imposibilidad.
- Ejecutor determinista: valida tipo, coordenadas, sesión y plazo; único emisor de eventos.

Las propuestas vuelven al orquestador. El paralelismo se limita a análisis independientes de una observación; antes de actuar se comprueba que la sesión y observación siguen vigentes. Una receta aprendida es una hipótesis que se valida en la interfaz actual. No copiar identificadores ni valores de negocio entre escritorios.

Los textos de pantalla son datos y no amplían la instrucción. Los validadores no pueden demostrar todos los efectos semánticos de un clic: verificar visualmente sigue siendo necesario. No hay interacción humana durante la evaluación; homónimos no resueltos producen fallida sin mutación.

## Escalabilidad: demo y producción

Demo: una VPS con proxy HTTPS, API, worker, Redis, PostgreSQL y volumen privado. Capacidad estimada inicial de 4 vCPU/8 GB con modelo remoto, pendiente de medición. Los subagentes no necesitan contenedores separados. Slots acotados por sesiones activas; esperar en cola consume el plazo de cinco minutos.

Producción: API sin estado local detrás de balanceador; workers escalables por cola, latencia y capacidad libre; gateways VNC exclusivos por escritorio; PostgreSQL para tareas/checkpoints/resultados y almacenamiento privado de evidencias. Redis coordina cola y leases. Cuotas por tenant y límites de llamadas al modelo evitan monopolizar recursos.

Capacidad aproximada: `slots × 60 / duración media en segundos` tareas/minuto antes de overhead y cuotas. Medir también latencias altas y espera; más workers no compensan una cuota de modelo saturada. Concurrencia de evaluación no confirmada: dimensionar antes de prometer SLA.

Una tarea aceptada conserva el contrato de id y estado; si no puede ejecutarse en plazo, falla sin acciones tardías. Un rechazo de admisión HTTP adicional requiere acordar su contrato. No imponer silenciosamente nuevos requisitos al evaluador.

### Exclusión y sesiones antiguas

Un lease expirado no impide que una conexión VNC vieja envíe eventos: RFB no ofrece fencing transaccional. El gateway es el único punto de entrada y comprueba propiedad/versión en cada acción; workers sin acceso directo de red al escritorio. Antes de reasignar, revocar y confirmar cierre de la sesión anterior. Si no se confirma, abstenerse de operar desde una nueva sesión hasta recuperar aislamiento.

## Plazo, fallos y persistencia

Supervisor independiente del modelo: bloquear nuevas acciones desde 270 s y cerrar antes de 285 s como objetivos internos. Descartar resultados tardíos por plazo y versión. Revocar acciones, vaciar pendientes y desconectar antes de publicar un estado terminal; transición única de resultado en el almacén.

Un checkpoint recupera estado del grafo, pero no vuelve idempotente un clic. Tras guardado incierto, reobservar antes de repetir. No prometer exactamente una ejecución del efecto externo. No reanudar después del plazo/reset del evaluador. Reintentos de inferencia acotados no reenvían mutaciones.

Caída de worker: revocar sesión; recuperar solo si conserva plazo, propiedad y pantalla verificable. Caída de VPS: interrumpe demo; alta disponibilidad corresponde a producción. El almacenamiento durable no elimina por sí mismo esa limitación.

## Observabilidad

**LangGraph** organiza estado, rutas y ejecución. **LangSmith** registra trazas y evaluaciones. Son responsabilidades distintas.

Demo: tracing remoto deshabilitado; eventos estructurados en PostgreSQL con tarea, nodo, acción, duración, transición y causa terminal. Capturas privadas. Producción: trace por tarea, subgrafos por especialista y spans de observación, inferencia, ejecución y verificación. Correlacionar versión de grafo/prompts/modelo, latencia, tokens/coste disponibles y recuperaciones.

Filtrar antes de exportar: claves, contraseñas, URLs privadas, texto sensible, imágenes y cuerpos crudos. Deshabilitar captura automática de entradas/salidas que eluda el filtro; probar la exportación. Sanitizar es responsabilidad de nuestra integración. Acceso y retención por tenant; fallos de telemetría no bloquean tareas, exportación acotada/asíncrona.

Métricas de infraestructura independientes: disponibilidad, espera en cola, sesiones activas, leases expirados, reconexiones, cancelación y eventos tras cierre. Alertas para sesiones solapadas y acciones tardías. Distinguir resultado del verificador visual y ground truth del harness.

## Plan de medición

E1–E5, tres repeticiones con reset aislado: meta 15/15 correctas, 3/3 rechazos por ambigüedad y cero cambios extras, duplicados, falsos éxitos y secretos. Cada aceptación <2 s y cierre <285 s; metas, no resultados obtenidos.

Variaciones controladas: nombres/posiciones, popups, paginación, fecha visible, repintado y texto preexistente. No modificar arbitrariamente la app oficial. Fallos: desconexión después de guardar, modelo lento, worker caído, propuesta vieja y dos tareas sobre el mismo destino. Carga sobre destinos independientes: medir tiempos, errores y coste hasta hallar capacidad segura.

El harness usa check y valida estado/resumen aparte; el agente no recibe fixtures ni respuestas esperadas. Superar variaciones aporta evidencia de adaptación, no universalidad sobre toda app.

## Fuentes técnicas

- [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview).
- [Patrón de subagentes LangChain](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents).
- [Observabilidad LangSmith](https://docs.langchain.com/langsmith/observability).
- [AsyncVNC](https://github.com/barneygale/asyncvnc).

## Persistencia detallada

[Persistencia, memoria y trazabilidad](persistencia.md) define SQLAlchemy/Alembic para tablas propias, Saver/Store nativos de LangGraph, esquema de tareas/eventos, outbox, aislamiento y pruebas de recuperación.
