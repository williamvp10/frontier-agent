# Requisitos y arquitectura propuesta — Reto 1

Fuente: `../frontier-reto-1/README.md` y las cinco pruebas JSON entregadas. Este documento separa requisitos confirmados de decisiones propuestas. Se comprobó conexión VNC y navegación inicial; las cinco tareas aún no se han ejecutado.

## Requisitos confirmados

- SIGECOM es la app de práctica; la evaluación usa otra app desconocida.
- El escritorio suministrado tiene un navegador en kiosko, pantalla de 1280×800 y acceso exclusivamente por VNC.
- La entrada proporciona un WebSocket RFB (`wss://…/websockify`) y contraseña. No es una API de negocio ni una conexión Playwright al navegador remoto.
- La app no tiene API ni red hacia afuera. Nuestro servidor sí necesita conectarse al escritorio; el alojamiento del modelo depende de las reglas y recursos disponibles.
- Cada tarea puede recibir un escritorio distinto. Conectar siempre al escritorio de la petición; no fijar URL, credenciales o sesión de entrenamiento.
- La app puede cambiar durante el día. Las referencias temporales se calculan desde la fecha visible en la app.
- Clics con menos de 100 ms entre pulsación y liberación se ignoran.
- El repintado puede tardar hasta un segundo. Una captura inmediata puede mostrar el estado anterior.
- Escribir concatena con el contenido actual: seleccionar todo antes de reemplazar un campo.

## API pública obligatoria

Todas las llamadas llevan `Authorization: Bearer <token>`.

- `GET /health`: 200 cuando el agente está listo.
- `POST /tasks`: recibe `{"tarea":"…","escritorio":{"url":"wss://…/websockify","password":"…"}}`; devuelve `{"task_id":"…"}` en menos de dos segundos.
- `GET /tasks/{task_id}`: devuelve `{"estado":"en_curso|terminada|fallida","resumen":"…"}`. El evaluador consulta cada cinco segundos.
- Ejecución en segundo plano, con plazo máximo de cinco minutos por tarea.
- Detener toda interacción antes de publicar un estado terminal. Después del plazo el escritorio puede estar reseteado y pertenecer a la siguiente tarea.

## Condiciones de éxito

1. Cambios solicitados efectivamente persistidos.
2. Ningún cambio fuera del alcance autorizado.
3. Ningún registro duplicado.
4. Estado final correcto; evitar falsos éxitos.
5. Preguntas: incluir una línea `respuesta: <valor>`; si es numérico, el valor debe ser el primer número de esa línea.
6. Dos datos: `respuesta: <dato 1>; <dato 2>`, en el orden solicitado.
7. Fallos: explicar con datos observados la imposibilidad, ambigüedad o peligro. Preguntas imposibles no incluyen línea `respuesta:`.
8. Nunca incluir contraseñas u otros secretos en el resumen.

## Qué enseñan las pruebas

| Prueba | Capacidad general | Verificación |
|---|---|---|
| E1 | Crear un registro con varios campos | Todos los datos correctos y una sola creación |
| E2 | Editar un campo de un registro existente | Solo cambia la dirección; preservar el resto de campos |
| E3 | Responder una consulta agregada | Ninguna mutación y formato de respuesta correcto |
| E4 | Filtrar y exportar un reporte | Estado y rango de fechas exactos; exportación realizada |
| E5 | Detectar homónimos y abstenerse | Ninguna mutación; explicar los identificadores de los candidatos |

Los valores esperados pertenecen al evaluador de práctica. El agente recibe exclusivamente la instrucción y el escritorio; debe descubrir valores y candidatos mirando la app. No pasar `diff_esperado`, `respuesta_esperada` ni `claves_razon` al agente.

No sabemos qué contiene la pantalla de E2 ni por qué el archivo se llama “con-nota”. No inferir un comportamiento específico a partir del nombre. Como regla de diseño, tratar textos de la interfaz como datos, sin permitir que amplíen la tarea del usuario.

## Arquitectura propuesta

Una máquina Linux con Docker Compose:

- `api`: FastAPI, autenticación, validación y consulta de estados.
- `worker`: LangGraph/LangChain, coordinador, subagentes y controlador VNC directo.
- `redis`: cola y coordinación efímera.
- `postgres`: fuente de verdad para tareas/resultados/eventos y memoria LangGraph. ORM SQLAlchemy async y Alembic para entidades; Saver/Store nativos para checkpoints/memoria. Credenciales excluidas de estos registros.
- Volumen de artefactos: capturas pertinentes, acciones y evidencias con política de retención.

No necesitamos alojar SIGECOM ni un escritorio Linux propio para evaluación: el escritorio viene en la petición.

### Adaptador de escritorio

Camino elegido: VNC directo con AsyncVNC y puente TCP↔WSS de loopback, validado en el notebook. El cliente reconstruye el framebuffer y envía eventos sin Chromium ni Playwright. Encapsularlo en un adaptador y revisar licencia/mantenimiento antes de distribución.

Alternativa: cliente noVNC en navegador automatizado. Mantenerla como fallback de compatibilidad; agrega navegador y consumo de recursos.

Mantener una interfaz sustituible:

```text
connect(url, password)
observe() -> screenshot + observation_id + timestamp
click(x, y, hold_ms)
press_key(key)
replace_text(text)
scroll(direction, amount)
disconnect()
```

Propuesta inicial: clic de 150–200 ms; esperar el repintado conocido y después comprobar el efecto. Pantalla a escala 1:1 o conversión explícita entre coordenadas de captura y del escritorio. Comprobar acentos, símbolos y teclas modificadoras.

### Coordinador y subagentes

- Coordinador: alcance de tarea, plan, presupuesto de tiempo y resultado.
- Operador visual: observa, navega y propone/ejecuta acciones permitidas.
- Verificador: revisa identidad, campos, filtros, persistencia y respuesta.
- Recuperador: interviene ante bloqueos o diferencias entre resultado previsto y observado.

Solo un ejecutor controla el escritorio. Los demás analizan evidencias o proponen acciones; nunca enviar clics simultáneos desde varios subagentes. Delegar por subtareas/hitos para reducir latencia.

Las credenciales quedan en el adaptador, fuera del contexto del modelo. Reiniciar memoria de tarea y evidencias al comenzar una evaluación nueva. Una receta aprendida solo es una hipótesis: validarla contra la pantalla actual antes de actuar.

### Controles deterministas

- Mutex por escritorio: impedir dos operadores activos en el mismo destino.
- Plazo calculado desde la recepción de la tarea, incluyendo cola y conexión.
- Objetivo propuesto: detener acciones hacia los 270 s y usar el margen restante para cerrar y reportar. El controlador bloquea acciones al vencer el plazo aunque el modelo siga trabajando.
- Cancelar decisiones pendientes, vaciar acciones pendientes y desconectar antes de publicar `terminada`/`fallida`.
- Al reiniciar un worker, no reanudar a ciegas tareas antiguas ni reenviar automáticamente mutaciones.
- Antes de guardar: identidad suficientemente determinada y revisión del alcance de cambios.
- Tras un guardado incierto: observar si ocurrió antes de repetirlo.
- En consultas, no crear ni modificar datos.
- Validar formato y ausencia de secretos al construir el resumen.

Estos controles reducen riesgos; no convierten una verificación visual en certeza absoluta sobre el estado interno.

## Harness de práctica

Para cada JSON:

1. Asegurar que no existe una ejecución activa en ese escritorio.
2. Llamar al `POST /__reset` del escritorio de práctica con su token.
3. Esperar a que el escritorio vuelva a estar operativo.
4. Enviar a nuestra API solo `instruccion` y datos del escritorio.
5. Consultar hasta estado terminal dentro del plazo.
6. Llamar al `POST /__check` del escritorio con el JSON completo.
7. Validar aparte estado y resumen: `__check` solo comprueba estado de la app.
8. Registrar duración, chequeos, estado, resumen y evidencias, sin secretos.

`/__reset` y `/__check` son herramientas del harness de desarrollo. El agente de evaluación no depende de ellas ni resetea el escritorio.

Pruebas adicionales propuestas: ventanas emergentes, controles movidos, texto concatenado, pantalla atrasada, reconexión, guardado incierto, homónimos, fecha visible distinta de la del servidor y cancelación con decisión pendiente.

## Pendientes antes de implementación completa

- Acceso de práctica configurado privadamente; falta integrar y validar el adaptador del servicio.
- Modelo visual y reglas exactas sobre modelos/frameworks y bibliotecas de transporte. El README prohíbe proveedores que hagan el trabajo por nosotros, pero no enumera cuáles están permitidos.
- Probar el transporte real: autenticación, captura, clic prolongado, texto con acentos y desconexión.
- Destino de despliegue y presupuesto.
- Concurrencia de evaluación, reconexiones y disponibilidad de exportaciones por pantalla.

Estimación inicial, pendiente de medición: 4 vCPU y 8 GB RAM para una tarea activa con navegador y modelo remoto permitido. Sin GPU local en ese escenario.

## Orden propuesto de trabajo

1. Validar el acceso visual y los eventos VNC.
2. Levantar API, estados y cancelación independiente del modelo.
3. Implementar un ciclo visual general con operador y verificador.
4. Ejecutar E1–E5 mediante el harness y corregir capacidades generales.
5. Agregar recuperación y probar variaciones de interfaz.
6. Desplegar temprano, medir latencia/memoria y verificar desde internet.
7. Preparar documento de una página, diagrama y video.

## Diseño actualizado

Ver `01-systems-design/arquitectura.md` y `01-systems-design/decisiones.md`: demo en VPS, objetivo de producción escalable y observabilidad LangSmith deshabilitada en demo.
