# Resultado de la prueba de acceso VNC

Se ejecutó `notebooks/01-vnc-access.ipynb` contra el escritorio de práctica, con configuración privada.

## Resultado observado

- Salud del escritorio: HTTP 200 y `ok: true`.
- Autenticación y conexión WSS/RFB: correctas.
- Framebuffer recibido: 1280×800; tres capturas completas por ejecución.
- Clic de 180 ms: produjo un cambio de navegación visible sin modificar registros.
- Movimiento del puntero y Tab: la captura mostró desplazamiento del foco al primer campo visible.
- Escape fue enviado, pero no cerró el menú observado. No asumir que una tecla tiene un efecto universal.
- Imagen PNG disponible como data URI para la futura integración visual, sin enviar datos a un modelo en esta prueba.
- Sesión desconectada al finalizar; sin reset ni check.

El prototipo usa AsyncVNC y un puente local de bytes TCP↔WebSocket. No usa Playwright ni Chromium. Se fijó una restricción de versión de cryptography porque AsyncVNC 1.3.0 usa la ubicación antigua de TripleDES; debe revisarse la dependencia final, incluida su licencia.

## Límites de esta evidencia

Se validó acceso y envío de entrada, no un agente autónomo ni éxito en E1–E5. Edición de campos, Unicode, exportación, reconexión y cancelación del sistema completo siguen pendientes. Las coordenadas del notebook son de demostración; el agente general debe obtenerlas de la pantalla actual.

Las salidas completas e imágenes se guardaron en `.private/`. El notebook publicable no incluye outputs.
