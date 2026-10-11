# Auditoría de arquitectura y evidencia E3 — 2026-10-10

Este documento conserva el diagnóstico inicial. Los cambios posteriores están
descritos en [mejora de navegación visual](mejora-navegacion-visual.md); no confundir
los conteos ni resultados de este corte con una ejecución posterior.

Revisión del código actual, bases locales, checkpoints y traza remota. No se
repitieron tareas ni resets. No contiene instrucciones, datos de negocio,
capturas, credenciales ni endpoints privados del escritorio.

## Ejecuciones distintas

| Runtime | Fallo durable | Acciones | Cierre |
|---|---|---:|---|
| Docker local | context_rejected; evento context_failure con invalid_contract | 0 | closed; transport_closed y owner_stopped true |
| Python local | invalid_reconciliation | 2 | closed con evidencia |

La corrección de grounding/UI existente corresponde al primer fallo. Se confirmó
que el runtime agrega procedencia a controles semánticos, normaliza descripciones
vacías, rechaza propuestas inválidas hacia recuperación y refresca la captura
tras planificación lenta. No modifica la condición de reconciliación responsable
del fallo Python. No afirmar que E3 pasó por contar con estos cambios.

## Arquitectura implementada

Coordinator, Operator, Verifier y Recovery son roles especializados dentro del
StateGraph y runtime compartido, con proyecciones de contexto tipado. Esto concuerda
con la spec vigente multiagente-contexto-jev.md; no son cuatro procesos autónomos.
El gateway VNC es el único emisor, en proceso hijo con watchdog. El runner controla
cierre/terminal fuera del modelo. PostgreSQL conserva autoridad; Redis transporta
referencias; checkpoints nativos guardan estado aceptado por run. El store nativo
está inicializado pero no se usa para reutilizar datos de negocio.

En E3 Python se observaron 1 llamada Coordinator, 3 Operator, 4 Verifier, 0 Recovery.
La separación de responsabilidades está implementada, pero esta ejecución no
acredita completar consultas ni todos los gates de arquitectura/fallos/reinicio.

## Causa exacta de invalid_reconciliation

Checkpoint terminal: mode=effect; acción relevante enviada presente; verificador
report.status=incomplete, operation_resolution=applied, observed_effect presente y
2 hechos. El grafo genera new_facts únicamente con report.status=verified. Al
reconciliar, new_facts queda vacío y se activa invalid_reconciliation.

El contrato permite tipar esta combinación; los prompts no evitan que se produzca.
El reporte global mezcla suficiencia para verificar un efecto con completitud de
la tarea. Revisar el contrato para separar ambas evaluaciones y responder con
reobservación/recuperación acotada ante inconsistencia, sin elevar hechos no
verificados ni repetir una acción enviada. No debilitar la guardia de evidencia.
No se aplicó una corrección funcional en esta auditoría.

## Auditoría de evidencia

Python E3: 5 observaciones y 5 PNG privados existentes; SHA-256, tamaño y permisos
0600 comprobados. 58 eventos y 19 checkpoints; 2 action_attempts. El adapter guarda
capturas con referencia/hash/dimensiones/fecha y expiración de 24 horas en DB;
la expiración no equivale a un proceso operativo de GC.

Auditoría local reconstruida, con galería y propuestas/verificaciones persistidas:
`.private/local-python/audit-E3/audit.md`; manifest estructural en el mismo directorio.
Los eventos return conservan el tipo de salida, no el retorno completo. Los
checkpoints contienen snapshots aceptados y la última verificación, no una copia
exacta de todos los prompts/retornos descartados. No se conserva razonamiento interno.

Brecha: primera acción confirmed en shared.action_history, pero action_attempts
mantiene las dos sent, sin verified_at ni outcome_observation_id. Falta registrar
la reconciliación de efecto en el repositorio SQL y ligar la evidencia posterior.

## Langfuse y Jev

Consulta CLI remota: 30 observaciones de la traza Python, 8 model_calls, 2
action_attempts, 9 vnc_calls, 0 recovery_escalations y 0 jev_calls. Estado fallida.
Generaciones registran modelos/uso/coste cuando está disponible. No se exportan
imágenes, prompts ni decisiones completas; es la frontera explícita de privacidad.
El motivo invalid_reconciliation se convierte en other por la lista permitida de
telemetría: ampliar los códigos técnicos seguros para diagnóstico correlacionado.

Jev está deshabilitado en este runtime. Su adapter OpenRouter y flujo consult→mismo
solicitante→decisión están implementados con límites y validación de contexto.
Es consejero textual opcional, no un quinto ejecutor ni verificador visual. No
hay evidencia en E3 de valor añadido; medir con/sin Jev sobre casos comparables,
éxito, abstención, coste y latencia antes de habilitarlo.

## Verificación actual

- pytest de graph_v2, context_service, observability, jev_advisor y advice_context:
  **119 passed**.
- pytest de ui_grounding: **10 passed**.
- Estas pruebas usan mocks/modelos sintéticos; no equivalen a una repetición real E3.
- Esta auditoría no hizo inferencia, escritura remota Langfuse ni interacción VNC.
