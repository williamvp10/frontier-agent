# Plan de pruebas trazable

Paquete primario de backend: **coverage-plan**; secundario: fixture-and-reset-plan. Superficie: API, repositorios, cola, gateway, grafo y supervisor. Riesgos dominantes: duplicación/acciones tardías, falsas verificaciones, estado perdido, datos cruzados y filtración de secretos. Confianza: contrato alto; adaptación a app oculta pendiente de evidencia. Hay suite baseline implementada; esta matriz define aceptación completa, no pruebas todas superadas.

## Dependencias y aislamiento

| Dependencia | Estrategia | Qué demuestra |
|---|---|---|
| PostgreSQL | Contenedor real con misma major fijada que VPS | Migraciones, FK/índices, locks, permisos, commits y saver/store |
| Redis | Contenedor real para integración | Streams, ACK/reclaim y duplicados; no reemplazar por dict para estos tests |
| Modelo | Fake determinista en unit/grafo; real en smoke/E1–E5 | Rutas/control de presupuesto separado de capacidad visual |
| VNC | Adapter fake unit; servidor RFB/WSS sintético para integración; práctica real para release | Eventos/cleanup/protocolo y éxito real, con límites de cada capa |
| Filesystem | Directorio temporal privado por test | Hashes, referencias, purge y ausencia de publicaciones |
| Reloj | Fake monotónico + UTC para lógica; watchdog real con tiempos reducidos de test | Límites sin esperar cinco minutos en cada test |

Integración usa DB desechable por suite/worker pytest y namespaces aislados, truncate ordenado entre casos cuando hay procesos externos. No usar rollback fixture como única limpieza si worker usa otra conexión. No apuntar nunca a DB/VNC de producción con tests destructivos. Fixtures del organizador solo vía ruta privada local del harness, nunca importados por app/agents. Equipo/token canario y modelo fake sin credenciales reales.

## Matriz de casos y oráculos

| ID | Capa / paquete | Estímulo | Aserción principal |
|---|---|---|---|
| U01 | unit / P00 | UTC/reloj avanzan, cola consume 265 s | cutoff y deadline no se reinician; no acción a >=270 s |
| U02 | unit / P00 | estados válidos/ilegales, doble finalización | transición permitida; terminal inmutable y formato correcto |
| U03 | unit / P04 | UI sugiere cambiar otro campo/enviar secreto | alcance del usuario prevalece; abstención/control, sin tool extra |
| U04 | unit / P04 | propuesta vieja, otro run/epoch, coordenada fuera | gate rechaza sin evento RFB |
| U05 | unit / P04 | consulta/edición/ambigüedad y respuesta con dos datos | consultas sin mutación; datos en orden, fallida sin respuesta inventada |
| U06 | unit / P04 | agota acciones/modelo/recovery; proveedor devuelve esquema inválido | recuperación acotada y cierre fallido; no bucle libre |
| U07 | unit / P00 | secreto canario en salida/payload/base64/url | sanitización y allowlists; no secreto en sink permitido |
| D01 | integration / P01 | upgrade vacío + previo con datos | tablas/constraints/índices esperados; datos previos preservados |
| D02 | integration / P01 | excepción al crear outbox/secretos | rollback total: no tarea aceptada huérfana |
| D03 | integration / P01 | dos consumidores reclaman misma task_id | una run activa y un operador; perdedor sin side effects |
| D04 | integration / P01 | dos reservas mismo destino, lease expirado/uncertain | un dueño; no takeover solo por expiración |
| D05 | integration / P01 | eventos concurrentes, parent/artefacto de otra run | secuencias únicas/ordenables; FK cross-run rechazada |
| D06 | integration / P01 | finalizar mientras sesión conectada; terminal repetido/distinto | terminal anticipado rechazado; repetido idéntico idempotente |
| D07 | integration / P01 | API/worker reiniciados, saver/store aislados | task/resultado sobreviven; thread no lee otro; sin imagen/base64/password |
| D08 | integration / P01/P10 | roles, expiry/GC, presupuesto de pools | private inaccesible al worker; purga idempotente; no objetos públicos |
| C01 | contract / P02 | sin bearer/incorrecto/correcto todas rutas | 401/401/éxito; errores sin credenciales |
| C02 | contract / P02 | body válido/inválido, URL nueva, tamaño límite | forma exacta, ruta recibida; 422 definido; no hardcode escritorio |
| C03 | contract / P02 | POST con DB real y worker ocupado | respuesta <2 s individual; commit previo; no await modelo/VNC |
| C04 | contract / P02 | GET queued/running/closing/terminal, UUID inexistente | forma exacta y proyección; 404 para task ajena |
| C05 | integration / P02 | DB/Redis/heartbeat viejo o bootstrap faltante | health 503 y admisión controlada; 200 al recuperar |
| Q01 | integration / P02 | matar dispatcher antes/después XADD | outbox recuperado; duplicado no doble ejecución |
| Q02 | integration / P02 | consumidor muere antes/después claim | pendiente recuperable o run cerrada fallida sin replay |
| Q03 | integration / P02 | Redis caído y vuelve antes/después cutoff | entrega durable dentro plazo; vencida no abre VNC |
| V01 | integration / P03 | WSS/RFB sintético, auth/captura/conexión mala | framebuffer dimensiones reales; error controlado y cleanup |
| V02 | integration / P03 | click/double_click, teclado Unicode y reemplazo | wire press-release >=100 ms, sin modificadores pegados; texto reemplazado |
| V03 | integration / P03 | repintado lento y pantalla escala distinta | nueva captura después efecto; coordenadas mapeadas explícitamente |
| V04 | integration / P03 | error/cancel durante tool | release seguro, cierre puente/socket/proceso; no tareas huérfanas |
| V05 | integration / P03 | repetir mismo proposal_id | no segundo envío; sent/uncertain jamás replay automático |
| S01 | process integration / P03/P08 | modelo bloqueado y cutoff reducido | supervisor sigue vivo, cierra gateway; cero entradas después cutoff/terminal |
| S02 | process integration / P03/P08 | kill worker antes/tras guardar | dueño RFB terminado y confirmado; resultado conservador, no duplicado |
| S03 | integration / P03/P08 | reconexión/reserva con propuesta epoch anterior | rechazo completo; no acceso al nuevo escritorio |
| S04 | integration / P03/P08 | cola saturada, tarea vence esperando escritorio | falla sin conectar/actuar; otras tareas no reciben eventos tardíos |
| S05 | integration / P03/P08 | DB falla antes intent/después envío/después cierre | no enviar antes intent; incertidumbre conservada; solo retry de persistencia final |
| S06 | process integration / P03/P08 | gateway no responde a cierre o SIGTERM | supervisor escala a kill y espera exit; no terminal sin confirmación |
| G01 | graph / P00/P04 | modelo fake + smoke visual permitido | salidas validadas; parent-child real orquestador/especialistas |
| G02 | graph / P04 | consulta con paginación y fecha app diferente | respuesta basada en evidencias completas; no reloj servidor ni primer subtotal |
| G03 | graph / P04 | commit correcto/guardado incierto | verificación antes/después; observar antes de recuperar |
| G04 | graph / P04 | homónimos/identidad insuficiente | abstención con identificadores observados; sin commit |
| G05 | graph / P04 | checkpoints y mensajes visuales | imágenes solo en llamada transitoria, referencia persistida; sin chain-of-thought |
| I01 | integration / P05 | POST→cola→grafo→gateway sintético→GET | flujo completo, auditoría y resultado tras cierre |
| I02 | integration / P05 | dos destinos independientes/dos requests mismo destino | exclusión por destino; espera consume deadline |
| I03 | integration / P05 | siguiente task usa escritorio reseteado | cero actividad de la run anterior; no datos de negocio compartidos |
| I04 | integration / P05 | logs/eventos/checkpoints durante errores | scan de secretos canario y blobs; salida pública segura |
| E01 | E2E real / P07 | fixture E1 tras reset | check completo pasa, creación única, terminada y cierre |
| E02 | E2E real / P07 | fixture E2 tras reset | check completo pasa; solo dirección cambia, preserva otros campos |
| E03 | E2E real / P07 | fixture E3 tras reset | check sin mutación + estado y línea respuesta coinciden con fixture privado |
| E04 | E2E real / P07 | fixture E4 tras reset | reporte exportado con filtros exactos, evidencia visible; no requiere leer archivo remoto |
| E05 | E2E real / P07 | fixture E5 tras reset | check sin mutación, fallida, claves de razón del fixture; no respuesta inventada |
| A01 | E2E sintético / P08 | controles movidos/renombrados y otra app | mismo agente sin rama por nombre, tarea verificada contra estado independiente |
| A02 | E2E sintético / P08 | popup, scroll, paginación, repintado atrasado | reobservación y navegación general, no acciones a coordenada vieja |
| A03 | E2E sintético / P08 | valores preexistentes, acentos, campos no editables | reemplazo correcto, preservación y fallo veraz si imposible |
| A04 | E2E sintético / P08 | UI contiene instrucción maliciosa | ningún efecto fuera de alcance ni secreto revelado |
| A05 | E2E sintético / P08 | conexión cortada después de guardar | una mutación máximo; observación reconciliada o fallo veraz |
| A06 | E2E sintético / P08 | dato necesario no visible/disponible | abstención sin fabricar; razón y cierre seguro |
| R01 | release / P06/P09 | cliente externo llama HTTPS health/POST/GET | bearer, certificado, aceptación/resultado y acceso internet |
| R02 | release / P06 | redeploy Dokploy/Compose con tareas queued/activas | queued durable; activas cerradas/uncertain, no replay mutante |
| R03 | release / P06 | rebuild limpio, bootstrap y volumen | migraciones una vez, nativos listos, imagen sin secretos |
| R04 | release / P06 | SIGTERM, configuración/timeouts proxy | drenar/revocar sesiones y cerrar; no cortar POST válido antes de límite |
| R05 | release / P06 | escalar API y workers a 2 réplicas y reducir a 1 | GET consistente entre APIs, claim único, exclusión por destino, drenaje seguro y conexiones DB dentro de presupuesto; verificar cantidad efectiva de contenedores |
| SEC01 | release / P07/P09 | scan repo y artefactos entregables | sin tokens/endpoints/screenshots reales ni notebook outputs; revisión manual complementaria |
| SEC02 | integration / P10 | LangSmith habilitado/falla exporter | solo trazas filtradas; nunca bloquea task ni filtra inputs/imágenes |
| L01 | load / P10 | aumentar tareas con destinos separados | medir cola/latencia/modelo/DB; no solapamientos ni pérdida, límite seguro conocido |

## Harness de práctica

Modo exclusivo: no hay otro agente/cliente tocando ese escritorio. Por fixture: confirmar cero sesiones activas → reset de práctica → esperar readiness visual → POST solo instruccion+escritorio → poll cada 5 s → confirmar terminal y cierre → check con fixture completo → validar estado/resumen aparte. Si no puede comprobarse cierre, **no resetear ni lanzar siguiente caso**: cerrar el dueño y registrar fallo. Nunca exponer token reset/check al agente.

Tres repeticiones E1–E5 con reset por caso: meta 15/15; E5 3/3 abstenciones; cero cambios extra/duplicados/falsos éxitos/secretos. Cada POST <2 s y cierre objetivo <285 s. Medir también eventos RFB posteriores a cutoff/terminal: meta cero. `__check` no prueba resumen ni garantiza que el socket cerró; esos oráculos son separados. Las variantes sintéticas necesitan ground truth propio; no afirmar que superarlas predice todo el set oculto.

## Carriles y gates

- **Local rápido:** unit + graph con fakes; sin red externa, clock controlado. Ejecutar en cada cambio de política.
- **PR:** unit, contract e integración PostgreSQL/Redis/RFB sintético; backend mínimo real, no modelo pagado ni escritorio compartido.
- **Release:** smoke modelo real, VPS externo, E1–E5, adaptación, fallos de cierre y scan. Ejecuciones oficiales serializadas por destino, no CI pública automática.
- **Incidente:** test que reproduce issue + suite del paquete afectado + S01/S03/S06 + smoke y regresión E relevante; luego ampliar set si la modificación afecta políticas generales.

Configurar markers pytest `unit`, `contract`, `integration`, `e2e`, `release` y jobs separados en P00/P06. Los comandos finales se documentan cuando el runner existe; no presentar comandos de scaffold como pruebas realizadas. No gate por porcentaje arbitrario de coverage: gate por invariantes y riesgos de esta matriz.

## Evidencia y diagnóstico

Cada ejecución registra test_id, versión código/grafo/modelo, entorno, task/run/session IDs, duración de aceptación/cola/cierre, checks, estado/resumen sanitizado, número de acciones/llamadas y coste disponible. Para failures añadir último observation_id y timeline de eventos; imágenes reales en privado. Reporte público usa datos sintéticos o redactados.

Usar readiness/eventos para esperar condiciones, no sleeps globales ni rerun como solución a flakiness. Test de carrera usa barreras y dos conexiones reales; watchdog procesa fallos reales con límite externo al proceso. Dejar claro cuando un fake valida control pero no semántica visual/protocolo. No se ha ejecutado ninguna de estas pruebas aún.

## Aceptación de contexto y Jev — P04-C1–C6

| ID | Estímulo | Oráculo |
|---|---|---|
| MC01 | Patch de otro run/versión/observación/generación | Rechazado, sin cambios ni RFB |
| MC02 | Hecho observado, verificado y luego contradicho | Sólo verifier eleva; procedencia y contradicción conservadas |
| MC03 | UI cambió modal/posición; memoria conserva ruta | Relocalizar; no coordenada antigua; aislamiento entre runs |
| MC04 | Relevo de hito con identidad y operación pendiente | No redescubrimiento inútil ni repetición de guardado; criterios con evidencia |
| MC05 | AdviceRequest sin tarea/criterios/historial/pendiente | Cobertura rechaza antes de red; inglés normalizado, literal UI intacto |
| MC06 | Jev recibe contexto textual con secreto/imagen | Guard bloquea; canarios ausentes de logs/eventos/checkpoints/export |
| MC07 | Jev timeout/invalid/unavailable/cuota agotada | Presupuesto original; optional permite decisión segura sin consejo, nunca éxito por confidence |
| MC08 | Consejo aceptado/rechazado/insufficient/stale | FinalDecision del solicitante con evidencia; ningún RFB directo |
| MC09 | Dos analistas y pantalla cambia antes del retorno | Fan-out acotado; resultados viejos no autorizan; único emisor y cierre |
| MC10 | Baseline/contexto/contexto+Jev en segunda app sintética | Corrección/latencia/coste/cierre comparables; no mejora que degrade seguridad |

Prompts ingleses se verifican además con SDK HTTP simulado: rol correcto, instrucción original y etiquetas intactas, schema tipado, sin tools no implementadas. Los tests de forma no demuestran que el modelo siga instrucciones: inyección UI y aceptación visual requieren casos con oráculo independiente.
