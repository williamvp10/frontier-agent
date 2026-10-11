# Navegación y verificación visual — 2026-10-10

El ensayo manual mediante VNC mostró requisitos generales: elegir la capacidad
según entidad y operación, observar submenús antes de seleccionar una opción,
confirmar la vista por título/campos/columnas, comprobar filtros seleccionados
y aplicados, distinguir coincidencias parciales y recorrer la paginación.
No se incorporaron rutas, nombres de clientes ni respuestas del caso al agente.
Capturas y notas manuales permanecen en directorios locales ignorados.

## Cambios

- Operator registra navegación relevante en memoria del run y reutiliza rutas
  confirmadas. Explora alternativas cuando la ruta inspeccionada no ofrece la
  capacidad requerida; no exige inventariar toda la aplicación.
- Verifier separa `effect_status/effect_facts` del estado global del reporte.
  Puede confirmar una transición mientras la tarea sigue incompleta.
  `criterion_checks` conserva pruebas explícitas de criterios parciales.
  Cuando la última prueba parcial satisface todos los criterios, el grafo pide
  verificación de cierre y respuesta. No exige un hito adicional ni convierte
  por sí mismo un reporte incompleto en éxito.
- Un reporte incompleto no eleva sus afirmaciones automáticamente a hechos
  verificados. Una reconciliación inconsistente provoca recuperación con nueva
  evidencia, sin repetir la acción enviada.
- Los efectos confirmados quedan ligados a una observación posterior del mismo
  run/sesión en PostgreSQL, además del checkpoint. No se puede vincular una
  acción a evidencia anterior o de otra ejecución.
- El runtime canonicaliza los campos de la acción elegida. Una salida inválida
  no envía entrada VNC; los diagnósticos guardan códigos técnicos, sin payloads
  ni texto de excepciones del proveedor.
- Rechazos de UI/contexto se entregan al siguiente rol con `last_rejection`.
  Un replan rechazado conserva el plan admitido y usa recuperación acotada.
  Los controles requieren una vista registrada y procedencia del frame actual.
- El transporte ofrece `VNC_TEXT_INPUT=keys|clipboard_utf8|clipboard_latin1`.
  `keys` conserva el valor por defecto. Los modos de portapapeles requieren
  confirmar la codificación del endpoint y reemplazan el portapapeles del
  escritorio; evitan depender del estado de mayúsculas del teclado. La prueba
  manual confirmó UTF-8 en el escritorio local. El verificador sigue comprobando
  el valor visible. Pruebas de wire validan padding, longitud, bytes exactos,
  combinación Ctrl+A/Ctrl+V y captura posterior; no se registra el texto.
- La captura se refresca tras planificación lenta. La vigencia de propuestas
  se configura con `ACTION_PROPOSAL_TTL_SECONDS` (30 s, máximo 60), limitada
  por el cutoff. Se conserva verificación independiente antes de actuar;
  no hay bypass basado únicamente en `risk=navigation`.

## Evidencia y límites

La suite completa actual con PostgreSQL/Redis desechables pasó **340 pruebas**.
Las ejecuciones reales intermedias E3 siguieron fallando y no acreditan aceptación.
La ejecución posterior con prompts `context-jev-v2.7` pasó **E3: 1/1 PASS**;
admisión 0,03 s y terminal detectado a 196,3 s. Estado, formato/valor de respuesta,
plazo, estado de aplicación y contrato de cierre OK. PostgreSQL confirmó diez
acciones verificadas y sesión cerrada con `transport_closed/owner_stopped=true`.
Auditoría privada: 14 capturas comprobadas, 267 eventos y checkpoints del run.
La API permaneció saludable tras el ensayo. Un PASS local no acredita todos los
casos E1–E5 ni una medición de estabilidad con repeticiones.
La evidencia de cada intento se conserva privadamente en
`.private/local-python/evaluation-E3-20261010-run*.{json,events.jsonl,console.log}`.

Los cuatro roles siguen siendo Coordinator, Operator, Verifier y Recovery.
Jev permanece opcional y deshabilitado; estos cambios no demuestran su valor.
Langfuse exporta metadatos, uso y estados. Las imágenes y el contexto de negocio
se conservan localmente, sin subirlos a la telemetría remota.
