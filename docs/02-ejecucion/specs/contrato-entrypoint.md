# Contrato del entrypoint: validación y gates

Contrastado el 10 de octubre de 2026 con el README local del organizador y el contrato compartido por el usuario. Nuevos agentes, memoria y Jev son detalles internos; no modifican la API de evaluación.

## Superficie pública obligatoria

Todas las llamadas llevan `Authorization: Bearer <token>` del equipo. Valores ficticios:

```json
{
  "tarea": "Consulta el dato solicitado en la aplicación.",
  "escritorio": {
    "url": "wss://desktop.example/websockify",
    "password": "<contraseña recibida>"
  }
}
```

| Requisito | Implementación | Evidencia / pendiente |
|---|---|---|
| GET /health devuelve 200 cuando listo | API verifica DB/Redis, memoria bootstrap y heartbeats; 503 si degradado | Contrato y DB/Redis real del baseline; ingress público pendiente |
| POST /tasks devuelve {"task_id":"…"} en <2 s | Transacción task/credenciales cifradas/outbox, sin inferencia/VNC/Jev; presupuesto admisión 1.5 s | Tests locales; medir cada request y p95 bajo carga/proxy Dokploy |
| GET /tasks/{task_id} devuelve estado/resumen | Sólo en_curso, terminada, fallida; estados internos proyectados | Tests de forma y proyección; GET consistente en réplicas pendiente R05 |
| Trabajo asíncrono y consulta cada 5 s | Worker consume referencias; POST no ejecuta grafo | Integración baseline POST→cola→grafo→cierre→GET sintética |
| Cada tarea tiene 5 minutos | Deadline desde recepción autenticada, cola incluida; drain268/cutoff270/targetclose285 | Tests de reloj/timeout/owner; crash/restart completo todavía pendiente |
| Detener al publicar terminal | Revocar y cerrar dueño antes de persistir terminada/fallida | Tests runner/proceso; no confundir __check con evidencia de cierre |
| Otra app, escritorio limpio por tarea | URL/password de cada petición; estado privado por run | Tests de aislamiento y destino; adaptación real app oculta no demostrada |
| Disponible desde 20:00 al fin de demo day | Dokploy/Compose preparado | No desplegado; disponibilidad externa es gate de entrega |

Respuestas normales, sin campos internos ni información de Jev:

```json
{"task_id":"00000000-0000-0000-0000-000000000001"}
```

```json
{"estado":"en_curso","resumen":""}
```

```json
{"estado":"terminada","resumen":"respuesta: <valor observado>"}
```

Los modelos trabajan con prompts ingleses. El runtime conserva claves/estados públicos españoles y formato de resumen definido en specs/harness. El README local entregado describe evaluación separada de estado/resumen, pero sus apartados finales aparecen sin detalle; no atribuir a ese texto reglas de extracción ausentes. Conservar las reglas actuales y contrastar con cualquier README actualizado del organizador.

## Errores de nuestro servicio

401 sin bearer o incorrecto; 422 entrada inválida; 404 task inexistente/ajena; 503 dependencia no lista o admisión indisponible. No reflejar passwords, headers, DSN ni payloads privados. La plataforma sólo exige 200 en health cuando listo; estas respuestas degradadas son decisiones de implementación.

No añadir `__reset`, `__check`, task_contract, idioma, modelo, consejo o IDs de subagente al body obligatorio. Los endpoints del escritorio son del harness externo, no de nuestra API. No requerir que Jev esté disponible para aceptar tareas si está configurado opcional. Su timeout/cuota consume el mismo presupuesto de 300 s.

## Release

Validar desde internet HTTPS/Bearer/POST/GET y cada POST <2 s; medir cola y cierre; ejecutar E1–E5 y segunda app sintética. Un test local passing no demuestra disponibilidad pública, exactitud visual ni cumplimiento del plazo ante caída total de la VPS. Una sesión uncertain tras crash no se desbloquea por lease vencido.

## Evidencia de esta revisión

Comando ejecutado sobre la revisión OpenRouter-only/prompts ingleses:

```sh
.venv/bin/python -m pytest tests/contract tests/unit tests/infra/test_compose.py -q --tb=short
.venv/bin/ruff check app tests scripts
.venv/bin/python scripts/check_public_files.py
```

Resultado: **73 pruebas pasan**, Ruff limpio; scan de **104 archivos** sin coincidencias privadas conocidas. Se verifican formas exactas de POST/GET, estados públicos, health degradado sin admisión y proveedor único; SDK HTTP simulado comprueba prompts/imágenes/JSON Schema. Esta revisión no volvió a ejecutar DB/Redis reales ni hizo inferencias pagadas o despliegue. La evidencia integrada anterior está en avance-inicial.md y no se presenta como una nueva ejecución.
