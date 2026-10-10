# Frontier Agent · Diseño del sistema

**Reto 1 · Versión 0.7 · 10 de octubre de 2026**

## Problema, límites y supuestos

Resolver tareas en apps desconocidas mediante píxeles y entrada VNC, con cambios exactos, sin duplicados ni acciones adicionales y resultado veraz. El evaluador entrega escritorio por tarea y lo resetea a los cinco minutos. Leer dimensiones reales; tomar «hoy» de la app. Modelo visual permitido y concurrencia siguen pendientes de validación. Fuera de alcance: DOM/API internos, archivos remotos y entrenamiento por aplicación; solo disponemos de acceso visual. El agente no conoce respuestas esperadas ni usa reset/check durante evaluación.

## Arquitectura y flujo

**Demo:** VPS Linux, Docker Compose, proxy HTTPS, FastAPI/Pydantic, PostgreSQL, Redis y worker. SQLAlchemy async/psycopg + Alembic gestionan tareas, ejecuciones y eventos; aceptación durable con outbox en <2 s. Redis coordina la cola. **LangGraph** persiste estado por thread con AsyncPostgresSaver; AsyncPostgresStore conserva memoria permitida; **LangChain** integra modelo y herramientas. Orquestador con subagentes operador, verificador y recuperador, delegados por hitos; un ejecutor determinista controla **VNC directo: AsyncVNC + puente WSS**, validado en notebook. Ciclo observar → proponer → validar → actuar → verificar. Credenciales fuera del grafo/modelo; memoria aislada por tarea; datos de negocio no se reutilizan entre evaluaciones.

**Producción:** réplicas API, scheduler con cuotas, pool de workers y gateways VNC exclusivos por escritorio; misma persistencia durable y almacenamiento privado de evidencias. Workers sin acceso directo al escritorio. Escalar por sesiones independientes y capacidad/cuotas del modelo, manteniendo plazo desde recepción, incluida cola. Un lease/versionado se comprueba en cada acción; antes de reasignar se confirma cierre anterior. Si no se confirma, no operar una sesión nueva. La VPS del demo es un punto único de fallo.

## Decisiones y trade-offs

| Decisión | Alternativa descartada y coste asumido |
|---|---|
| VNC directo | noVNC + Playwright añade navegador; transporte directo validado, licencia/mantenimiento de AsyncVNC por revisar |
| API y workers separados | Petición larga compromete aceptación/disponibilidad; añade cola y coordinación |
| Grafo explícito y especialistas | Agente monolítico reduce llamadas, pero mezcla responsabilidades; delegación por hitos limita latencia |
| Ejecutor único por escritorio | Control paralelo genera carreras; paralelismo solo entre destinos/análisis independientes |
| PostgreSQL desde el demo | Solo Redis simplifica despliegue pero no separa cola de estado durable; coste de DB, pools y migraciones |

## Casos difíciles y cierre seguro

Homónimos: fallar sin modificar, con identificadores observados. Edición: preservar otros campos, seleccionar todo y comprobar persistencia. Clics de 150–200 ms; esperar repintado antes de observar. Consultas sin mutaciones; exportación con filtros comprobados. Texto de pantalla no amplía el alcance. Guardado incierto: reobservar antes de repetir. Checkpoints no hacen idempotentes los efectos externos.

Supervisor independiente: bloquear acciones desde 270 s; cancelar pendientes, desconectar y confirmar cierre antes de publicar resultado, objetivo <285 s. Descartar respuestas tardías por plazo/versión. No reanudar mutaciones tras reset o vencimiento. Si la evidencia no permite concluir, fallar con razón; la verificación visual no garantiza conocer todo el estado interno.

## Medición y observabilidad

E1–E5, tres repeticiones desde reset: meta **15/15 correctas**, **3/3 rechazos de homónimos**, **0 cambios extras, duplicados, falsos éxitos y secretos**. Harness combina check con validación de estado/resumen. Aceptación **cada llamada <2 s**; cierre **cada tarea <285 s**; acciones posteriores **0**. Preguntas: línea `respuesta:` con valor primero y dos datos separados por `;`.

Variaciones controladas: controles movidos/renombrados, popups, paginación y fecha distinta. Fallos: modelo lento, desconexión tras guardar, caída de worker y propuesta vieja. Carga: destinos independientes y exclusión en destino compartido. Medir éxito por escenario, espera, duración, pasos, llamadas/coste y cancelación. Son metas: solo transporte y navegación están comprobados.

Demo: historial estructurado en PostgreSQL, ligado a tarea/ejecución/agente; imágenes privadas referenciadas. Producción: **LangSmith** registra trazas por tarea/subagente/tool y evaluaciones; LangGraph orquesta. Filtrar secretos, URLs e imágenes antes de exportar; no habilitar captura cruda por defecto. Telemetría asíncrona y acotada, sin bloquear tareas.
